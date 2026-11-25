//! The engine process: start it, hand its port + token to the UI, restart it after a crash,
//! and stop it cleanly on quit. The shell never touches the library database itself.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;

use crate::log;

#[derive(Clone, Serialize, Debug, PartialEq)]
pub struct Endpoint {
    pub port: u16,
    pub token: String,
}

/// What the UI is told when the engine comes back or gives up (Tauri events).
pub const EVENT_RESTARTED: &str = "engine-restarted";
pub const EVENT_FAILED: &str = "engine-failed";

/// Crash policy: wait 1 s, 2 s, then 5 s before restarting; a 4th crash within 60 s means the
/// engine cannot run (bad library, missing DLL...) and the UI shows the error and the log path.
const RESTART_DELAYS: [Duration; 3] = [
    Duration::from_secs(1),
    Duration::from_secs(2),
    Duration::from_secs(5),
];
const CRASH_WINDOW: Duration = Duration::from_secs(60);
/// How long a clean quit may take before the engine is killed.
pub const SHUTDOWN_BUDGET: Duration = Duration::from_secs(5);

/// Pure: how long to wait before restarting after a crash, given the earlier crash times
/// (newest last), or `None` to give up. Old crashes outside the window are ignored.
pub fn restart_delay(crashes: &[Instant], now: Instant) -> Option<Duration> {
    let recent = crashes
        .iter()
        .filter(|t| now.duration_since(**t) <= CRASH_WINDOW)
        .count();
    RESTART_DELAYS.get(recent).copied()
}

/// (event name, message) sink, set once the Tauri app exists.
type Notify = Box<dyn Fn(&str, &str) + Send>;

#[derive(Default)]
pub struct Sidecar {
    child: Mutex<Option<Child>>,
    endpoint: Mutex<Option<Endpoint>>,
    error: Mutex<Option<String>>,
    crashes: Mutex<Vec<Instant>>,
    quitting: AtomicBool,
    /// Called with (event name, message) when the engine restarts or gives up.
    notify: Mutex<Option<Notify>>,
}

pub struct Launch {
    pub exe: PathBuf,
    pub library: PathBuf,
}

fn random_token() -> String {
    let mut bytes = [0u8; 24];
    getrandom::fill(&mut bytes).expect("OS random source unavailable");
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

impl Sidecar {
    pub fn endpoint(&self) -> Result<Endpoint, String> {
        if let Some(err) = self.error.lock().unwrap().clone() {
            return Err(err);
        }
        self.endpoint
            .lock()
            .unwrap()
            .clone()
            .ok_or_else(|| "engine not ready yet".to_string())
    }

    pub fn set_notify(&self, f: impl Fn(&str, &str) + Send + 'static) {
        *self.notify.lock().unwrap() = Some(Box::new(f));
    }

    fn emit(&self, event: &str, message: &str) {
        if let Some(f) = self.notify.lock().unwrap().as_ref() {
            f(event, message);
        }
    }

    pub fn fail(&self, err: String) {
        log::mark(&format!("engine failed: {err}"));
        *self.error.lock().unwrap() = Some(err);
    }

    /// Starts the engine and a supervisor thread that restarts it if it exits unexpectedly.
    pub fn start(self: &Arc<Self>, launch: Arc<Launch>) -> Result<(), String> {
        self.spawn_once(&launch, 0)?;
        let me = self.clone();
        std::thread::Builder::new()
            .name("iv-supervisor".into())
            .spawn(move || me.supervise(launch))
            .map_err(|e| format!("supervisor thread: {e}"))?;
        Ok(())
    }

    fn spawn_once(self: &Arc<Self>, launch: &Launch, restart: u32) -> Result<(), String> {
        let token = random_token();
        let mut cmd = Command::new(&launch.exe);
        cmd.arg("--library")
            .arg(&launch.library)
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
        log::mark("sidecar spawn requested");
        let mut child = cmd
            .spawn()
            .map_err(|e| format!("could not start {}: {e}", launch.exe.display()))?;
        log::mark("sidecar process created");
        let stdout = child.stdout.take().ok_or("sidecar stdout unavailable")?;
        *self.endpoint.lock().unwrap() = None;
        *self.child.lock().unwrap() = Some(child);

        // Read the one-line readiness message on a background thread.
        let me = self.clone();
        std::thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            let mut line = String::new();
            let ready = match reader.read_line(&mut line) {
                Ok(n) if n > 0 => match serde_json::from_str::<serde_json::Value>(&line) {
                    Ok(v) if v["event"] == "ready" => {
                        let port = v["port"].as_u64().unwrap_or(0) as u16;
                        log::mark(&format!(
                            "sidecar ready on port {port} (restart {restart}; sidecar reports {} ms after its own start, db open {} ms)",
                            v["startup_ms"], v["db_ms"]
                        ));
                        *me.endpoint.lock().unwrap() = Some(Endpoint { port, token });
                        *me.error.lock().unwrap() = None;
                        true
                    }
                    Ok(v) => {
                        me.fail(format!("sidecar error: {v}"));
                        false
                    }
                    Err(e) => {
                        me.fail(format!("bad sidecar output: {e}"));
                        false
                    }
                },
                _ => false, // exited before ready: the supervisor decides what happens next
            };
            log::write_timeline();
            if ready && restart > 0 {
                me.emit(EVENT_RESTARTED, &format!("restart {restart}"));
            }
            // Keep draining stdout so the sidecar never blocks on a full pipe.
            let mut sink = String::new();
            while reader.read_line(&mut sink).map(|n| n > 0).unwrap_or(false) {
                sink.clear();
            }
        });
        Ok(())
    }

    fn supervise(self: Arc<Self>, launch: Arc<Launch>) {
        let mut restarts = 0;
        loop {
            std::thread::sleep(Duration::from_millis(250));
            if self.quitting.load(Ordering::SeqCst) {
                return;
            }
            let exited = {
                let mut guard = self.child.lock().unwrap();
                match guard.as_mut().map(|c| c.try_wait()) {
                    Some(Ok(Some(status))) => {
                        *guard = None;
                        Some(format!("{status}"))
                    }
                    Some(Ok(None)) => None,
                    Some(Err(e)) => Some(format!("wait failed: {e}")),
                    None => return, // stopped on purpose
                }
            };
            let Some(status) = exited else { continue };
            if self.quitting.load(Ordering::SeqCst) {
                return;
            }
            *self.endpoint.lock().unwrap() = None;
            let now = Instant::now();
            let delay = {
                let mut crashes = self.crashes.lock().unwrap();
                let delay = restart_delay(&crashes, now);
                crashes.push(now);
                delay
            };
            log::mark(&format!("engine exited unexpectedly ({status})"));
            let Some(delay) = delay else {
                let msg = format!(
                    "The ImageVault engine stopped 4 times within a minute (last: {status}). See the logs in {}.",
                    launch.library.join("logs").display()
                );
                self.fail(msg.clone());
                log::write_timeline();
                self.emit(EVENT_FAILED, &msg);
                return;
            };
            std::thread::sleep(delay);
            if self.quitting.load(Ordering::SeqCst) {
                return;
            }
            restarts += 1;
            log::mark(&format!("restarting engine (restart {restarts})"));
            if let Err(e) = self.spawn_once(&launch, restarts) {
                self.fail(e.clone());
                log::write_timeline();
                self.emit(EVENT_FAILED, &e);
                return;
            }
        }
    }

    /// Clean quit: ask the engine to finish up (`POST /v1/shutdown`), wait up to the budget,
    /// then kill it. Jobs commit atomically, so a kill loses at most the work in progress.
    pub fn stop(&self) {
        self.quitting.store(true, Ordering::SeqCst);
        let Some(mut child) = self.child.lock().unwrap().take() else {
            return;
        };
        let t0 = Instant::now();
        let asked = match self.endpoint.lock().unwrap().clone() {
            Some(ep) => request_shutdown(&ep, Duration::from_secs(1)).is_ok(),
            None => false,
        };
        if asked {
            while t0.elapsed() < SHUTDOWN_BUDGET {
                if let Ok(Some(_)) = child.try_wait() {
                    log::mark(&format!(
                        "engine stopped cleanly in {} ms",
                        t0.elapsed().as_millis()
                    ));
                    log::write_timeline();
                    return;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
        let _ = child.kill();
        let _ = child.wait();
        log::mark(if asked {
            "engine did not stop within 5 s; killed"
        } else {
            "engine not ready; killed"
        });
        log::write_timeline();
    }
}

/// The HTTP request for a clean stop. Plain std networking: one request does not justify an
/// HTTP client dependency.
pub fn shutdown_request(ep: &Endpoint) -> String {
    format!(
        "POST /v1/shutdown HTTP/1.1\r\nHost: 127.0.0.1:{}\r\nAuthorization: Bearer {}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        ep.port, ep.token
    )
}

/// Sends the shutdown request; Ok when the engine answered 200.
pub fn request_shutdown(ep: &Endpoint, timeout: Duration) -> Result<(), String> {
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], ep.port));
    let mut stream = TcpStream::connect_timeout(&addr, timeout).map_err(|e| e.to_string())?;
    stream.set_read_timeout(Some(timeout)).ok();
    stream.set_write_timeout(Some(timeout)).ok();
    stream
        .write_all(shutdown_request(ep).as_bytes())
        .map_err(|e| e.to_string())?;
    let mut head = [0u8; 16];
    let n = stream.read(&mut head).map_err(|e| e.to_string())?;
    let status = String::from_utf8_lossy(&head[..n]);
    if status.starts_with("HTTP/1.1 200") {
        Ok(())
    } else {
        Err(format!("unexpected reply: {status}"))
    }
}

/// Bundled build: `<resources>/sidecar/ivsidecar(.exe)`. Dev override: `IV_SIDECAR_PATH`.
pub fn sidecar_path(package_info: &tauri::PackageInfo) -> Result<PathBuf, String> {
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

/// Thumbnails live in `<library>/thumbs`; the page may read nothing else from disk.
pub fn thumbs_dir(library: &Path) -> PathBuf {
    library.join("thumbs")
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;

    #[test]
    fn restart_backoff_then_give_up() {
        let t0 = Instant::now();
        let at = |s: u64| t0 + Duration::from_secs(s);
        let secs = |n| Some(Duration::from_secs(n));
        assert_eq!(restart_delay(&[], at(0)), secs(1)); // 1st crash
        assert_eq!(restart_delay(&[at(0)], at(2)), secs(2)); // 2nd
        assert_eq!(restart_delay(&[at(0), at(2)], at(5)), secs(5)); // 3rd
        assert_eq!(restart_delay(&[at(0), at(2), at(5)], at(20)), None); // 4th within a minute: give up
                                                                         // crashes older than 60 s are forgotten
        assert_eq!(restart_delay(&[at(0), at(2), at(5)], at(64)), secs(2));
        assert_eq!(restart_delay(&[at(0), at(2), at(5)], at(70)), secs(1));
    }

    #[test]
    fn shutdown_sends_the_token_and_accepts_200() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().unwrap();
            let mut buf = [0u8; 1024];
            let n = sock.read(&mut buf).unwrap();
            sock.write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 17\r\n\r\n{\"stopping\":true}")
                .unwrap();
            String::from_utf8_lossy(&buf[..n]).to_string()
        });
        let ep = Endpoint {
            port,
            token: "secret-token".into(),
        };
        request_shutdown(&ep, Duration::from_secs(2)).unwrap();
        let req = server.join().unwrap();
        assert!(req.starts_with("POST /v1/shutdown HTTP/1.1\r\n"));
        assert!(req.contains("Authorization: Bearer secret-token\r\n"));
    }

    #[test]
    fn shutdown_reports_a_refusal() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            let (mut sock, _) = listener.accept().unwrap();
            let mut buf = [0u8; 1024];
            let _ = sock.read(&mut buf);
            sock.write_all(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
                .unwrap();
        });
        let ep = Endpoint {
            port,
            token: "x".into(),
        };
        assert!(request_shutdown(&ep, Duration::from_secs(2)).is_err());
    }

    #[test]
    fn thumbnail_scope_is_the_thumbs_folder_only() {
        let lib = Path::new("C:/Users/neo/AppData/Local/ImageVault/Library");
        assert_eq!(thumbs_dir(lib), lib.join("thumbs"));
    }
}
