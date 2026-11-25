//! Startup and lifecycle timeline, written to `<library>/logs/startup.log`, so slow launches and
//! engine crashes can be diagnosed on a user's PC without a debugger.

use std::path::PathBuf;
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

/// Process start time; every line is relative to it.
static T0: OnceLock<Instant> = OnceLock::new();
static TIMELINE: Mutex<Vec<String>> = Mutex::new(Vec::new());
/// `<library dir>/logs`. Known before Tauri starts, so the timeline is written even if window
/// or webview creation stalls.
static LOG_DIR: OnceLock<PathBuf> = OnceLock::new();

pub fn init(log_dir: Option<PathBuf>) {
    T0.get_or_init(Instant::now);
    if let Some(dir) = log_dir {
        let _ = LOG_DIR.set(dir);
    }
}

pub fn mark(step: &str) {
    let ms = T0.get().map(|t| t.elapsed().as_millis()).unwrap_or(0);
    TIMELINE.lock().unwrap().push(format!("{ms:>6} ms  {step}"));
}

pub fn write_timeline() {
    let Some(dir) = LOG_DIR.get() else {
        return;
    };
    let lines = TIMELINE.lock().unwrap().join("\n");
    if std::fs::create_dir_all(dir).is_ok() {
        let _ = std::fs::write(dir.join("startup.log"), lines + "\n");
    }
}
