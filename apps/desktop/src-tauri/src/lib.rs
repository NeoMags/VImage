//! Desktop shell: starts the Python sidecar, hands its port + token to the UI, stops it on exit.
//! The shell never touches the library database; all data goes through the sidecar API.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Instant;

use serde::Serialize;
use tauri::{Manager, RunEvent};

#[derive(Clone, Serialize)]
struct Endpoint {
    port: u16,
    token: String,
}

/// Process start time; every startup log line is relative to it.
static T0: OnceLock<Instant> = OnceLock::new();

/// Startup timeline, written to `<app log dir>/startup.log` once the sidecar is ready (or failed),
/// so slow launches can be diagnosed on a user's PC without a debugger.
static TIMELINE: Mutex<Vec<String>> = Mutex::new(Vec::new());

fn mark(step: &str) {
    let ms = T0.get().map(|t| t.elapsed().as_millis()).unwrap_or(0);
    TIMELINE.lock().unwrap().push(format!("{ms:>6} ms  {step}"));
}

/// `<library dir>/logs`. Known before Tauri starts, so the timeline is written even if window
/// or webview creation stalls.
static LOG_DIR: OnceLock<PathBuf> = OnceLock::new();

/// Library folder: `IV_LIBRARY_DIR`, else `%LOCALAPPDATA%\\ImageVault\\Library`.
/// Deliberately a subfolder: the per-user installer puts program files in
/// `%LOCALAPPDATA%\\ImageVault`, and library data must never mix with them.
/// The shell passes this to the sidecar with `--library`, so it is decided in one place.
fn library_dir() -> Option<PathBuf> {
    if let Some(dir) = std::env::var_os("IV_LIBRARY_DIR") {
        return Some(PathBuf::from(dir));
    }
    let base = std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/share")))?;
    Some(base.join("ImageVault").join("Library"))
}

fn write_timeline() {
    let Some(dir) = LOG_DIR.get() else {
        return;
    };
    let lines = TIMELINE.lock().unwrap().join("\n");
    if std::fs::create_dir_all(dir).is_ok() {
        let _ = std::fs::write(dir.join("startup.log"), lines + "\n");
    }
}

#[derive(Default)]
struct Sidecar {
    child: Mutex<Option<Child>>,
    endpoint: Mutex<Option<Endpoint>>,
    error: Mutex<Option<String>>,
}

fn random_token() -> String {
    let mut bytes = [0u8; 24];
    getrandom::fill(&mut bytes).expect("OS random source unavailable");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

fn sidecar_path(package_info: &tauri::PackageInfo) -> Result<PathBuf, String> {
    // Bundled build: <resources>/sidecar/ivsidecar(.exe). Dev override: IV_SIDECAR_PATH.
    if let Ok(p) = std::env::var("IV_SIDECAR_PATH") {
        return Ok(PathBuf::from(p));
    }
    let exe = if cfg!(windows) {
        "ivsidecar.exe"
    } else {
        "ivsidecar"
    };
    // Resolved without an AppHandle so the sidecar can start before the window and webview exist.
    tauri::utils::platform::resource_dir(package_info, &tauri::Env::default())
        .map(|dir| dir.join("sidecar").join(exe))
        .map_err(|e| format!("resource dir unavailable: {e}"))
}

fn start_sidecar(
    package_info: &tauri::PackageInfo,
    library: &std::path::Path,
    state: Arc<Sidecar>,
) -> Result<(), String> {
    let path = sidecar_path(package_info)?;
    let token = random_token();
    let mut cmd = Command::new(&path);
    cmd.arg("--library")
        .arg(library)
        .env("IV_TOKEN", &token)
        .env("IV_PARENT_PID", std::process::id().to_string())
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    mark("sidecar spawn requested");
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("could not start {}: {e}", path.display()))?;
    mark("sidecar process created");
    let stdout = child.stdout.take().ok_or("sidecar stdout unavailable")?;
    *state.child.lock().unwrap() = Some(child);

    // Read the one-line readiness message on a background thread.
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        match reader.read_line(&mut line) {
            Ok(n) if n > 0 => match serde_json::from_str::<serde_json::Value>(&line) {
                Ok(v) if v["event"] == "ready" => {
                    mark(&format!(
                        "sidecar ready (sidecar reports {} ms after its own start, db open {} ms)",
                        v["startup_ms"], v["db_ms"]
                    ));
                    let port = v["port"].as_u64().unwrap_or(0) as u16;
                    *state.endpoint.lock().unwrap() = Some(Endpoint { port, token });
                }
                Ok(v) => *state.error.lock().unwrap() = Some(format!("sidecar error: {v}")),
                Err(e) => *state.error.lock().unwrap() = Some(format!("bad sidecar output: {e}")),
            },
            _ => *state.error.lock().unwrap() = Some("sidecar exited before ready".into()),
        }
        if let Some(err) = state.error.lock().unwrap().clone() {
            mark(&format!("sidecar failed: {err}"));
        }
        write_timeline();
        // Keep draining stdout so the sidecar never blocks on a full pipe.
        let mut sink = String::new();
        while reader.read_line(&mut sink).map(|n| n > 0).unwrap_or(false) {
            sink.clear();
        }
    });
    Ok(())
}

#[tauri::command]
fn get_sidecar_endpoint(state: tauri::State<Arc<Sidecar>>) -> Result<Endpoint, String> {
    if let Some(err) = state.error.lock().unwrap().clone() {
        return Err(err);
    }
    state
        .endpoint
        .lock()
        .unwrap()
        .clone()
        .ok_or_else(|| "sidecar not ready yet".to_string())
}

pub fn run() {
    T0.get_or_init(Instant::now);
    mark("app process started");
    let library = library_dir();
    if let Some(dir) = &library {
        let _ = LOG_DIR.set(dir.join("logs"));
    }
    let context = tauri::generate_context!();
    // Start the engine first: creating the window and webview can take seconds on a cold start
    // (2.8 s measured on Linux/Xvfb), and the engine's own startup runs in parallel with it.
    let sidecar = Arc::new(Sidecar::default());
    let started = match &library {
        Some(lib) => start_sidecar(context.package_info(), lib, sidecar.clone()),
        None => Err("cannot determine the library folder (LOCALAPPDATA not set)".to_string()),
    };
    if let Err(e) = started {
        mark(&format!("sidecar failed: {e}"));
        *sidecar.error.lock().unwrap() = Some(e);
    }
    tauri::Builder::default()
        .manage(sidecar)
        .invoke_handler(tauri::generate_handler![get_sidecar_endpoint])
        .on_page_load(|_, payload| {
            mark(&format!("page load {:?}", payload.event()));
            write_timeline();
        })
        .setup(|_app| {
            mark("setup hook");
            write_timeline();
            Ok(())
        })
        .build(context)
        .expect("error while building ImageVault")
        .run(|app, event| match event {
            RunEvent::Ready => mark("event loop ready"),
            RunEvent::Exit => {
                if let Some(mut child) = app.state::<Arc<Sidecar>>().child.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
            _ => {}
        });
}
