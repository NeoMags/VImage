//! Desktop shell: starts the Python sidecar, hands its port + token to the UI, stops it on exit.
//! The shell never touches the library database; all data goes through the sidecar API.

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use serde::Serialize;
use tauri::{Manager, RunEvent};

#[derive(Clone, Serialize)]
struct Endpoint {
    port: u16,
    token: String,
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

fn sidecar_path(app: &tauri::AppHandle) -> PathBuf {
    // Bundled build: <resources>/sidecar/ivsidecar(.exe). Dev override: IV_SIDECAR_PATH.
    if let Ok(p) = std::env::var("IV_SIDECAR_PATH") {
        return PathBuf::from(p);
    }
    let exe = if cfg!(windows) {
        "ivsidecar.exe"
    } else {
        "ivsidecar"
    };
    app.path()
        .resource_dir()
        .expect("resource dir")
        .join("sidecar")
        .join(exe)
}

fn start_sidecar(app: &tauri::AppHandle) -> Result<(), String> {
    let path = sidecar_path(app);
    let token = random_token();
    let mut cmd = Command::new(&path);
    cmd.env("IV_TOKEN", &token)
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
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("could not start {}: {e}", path.display()))?;
    let stdout = child.stdout.take().ok_or("sidecar stdout unavailable")?;
    let state = app.state::<Sidecar>();
    *state.child.lock().unwrap() = Some(child);

    // Read the one-line readiness message on a background thread.
    let handle = app.clone();
    std::thread::spawn(move || {
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let state = handle.state::<Sidecar>();
        match reader.read_line(&mut line) {
            Ok(n) if n > 0 => match serde_json::from_str::<serde_json::Value>(&line) {
                Ok(v) if v["event"] == "ready" => {
                    let port = v["port"].as_u64().unwrap_or(0) as u16;
                    *state.endpoint.lock().unwrap() = Some(Endpoint { port, token });
                }
                Ok(v) => *state.error.lock().unwrap() = Some(format!("sidecar error: {v}")),
                Err(e) => *state.error.lock().unwrap() = Some(format!("bad sidecar output: {e}")),
            },
            _ => *state.error.lock().unwrap() = Some("sidecar exited before ready".into()),
        }
        // Keep draining stdout so the sidecar never blocks on a full pipe.
        let mut sink = String::new();
        while reader.read_line(&mut sink).map(|n| n > 0).unwrap_or(false) {
            sink.clear();
        }
    });
    Ok(())
}

#[tauri::command]
fn get_sidecar_endpoint(state: tauri::State<Sidecar>) -> Result<Endpoint, String> {
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
    tauri::Builder::default()
        .manage(Sidecar::default())
        .invoke_handler(tauri::generate_handler![get_sidecar_endpoint])
        .setup(|app| {
            if let Err(e) = start_sidecar(app.handle()) {
                *app.state::<Sidecar>().error.lock().unwrap() = Some(e);
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building ImageVault")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(mut child) = app.state::<Sidecar>().child.lock().unwrap().take() {
                    let _ = child.kill();
                    let _ = child.wait();
                }
            }
        });
}
