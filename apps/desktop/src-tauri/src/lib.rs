//! Desktop shell: starts the engine (Python sidecar) before the window, keeps it running, gives
//! the UI the few Windows features it needs, and stops the engine cleanly on quit.
//! The shell never touches the library database; all data goes through the engine API.

mod log;
mod sidecar;

use std::path::PathBuf;
use std::sync::Arc;

use serde::Serialize;
use tauri::{Emitter, Manager, RunEvent};

use sidecar::{Endpoint, Launch, Sidecar};

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

#[tauri::command]
fn get_sidecar_endpoint(state: tauri::State<Arc<Sidecar>>) -> Result<Endpoint, String> {
    state.endpoint()
}

#[derive(Serialize)]
struct AppPaths {
    library: Option<PathBuf>,
    logs: Option<PathBuf>,
    thumbs: Option<PathBuf>,
}

/// Folders shown in Settings and in error messages ("see the log in ...").
#[tauri::command]
fn get_app_paths() -> AppPaths {
    let library = library_dir();
    AppPaths {
        logs: library.as_ref().map(|l| l.join("logs")),
        thumbs: library.as_deref().map(sidecar::thumbs_dir),
        library,
    }
}

pub fn run() {
    let library = library_dir();
    log::init(library.as_ref().map(|l| l.join("logs")));
    log::mark("app process started");
    let context = tauri::generate_context!();
    // Start the engine first: creating the window and webview can take seconds on a cold start
    // (2.8 s measured on Linux/Xvfb), and the engine's own startup runs in parallel with it.
    let engine = Arc::new(Sidecar::default());
    let started = match (&library, sidecar::sidecar_path(context.package_info())) {
        (Some(lib), Ok(exe)) => engine.start(Arc::new(Launch {
            exe,
            library: lib.clone(),
        })),
        (None, _) => Err("cannot determine the library folder (LOCALAPPDATA not set)".to_string()),
        (_, Err(e)) => Err(e),
    };
    if let Err(e) = started {
        engine.fail(e);
        log::write_timeline();
    }
    let thumbs = library.as_deref().map(sidecar::thumbs_dir);
    tauri::Builder::default()
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_opener::init())
        .manage(engine.clone())
        .invoke_handler(tauri::generate_handler![
            get_sidecar_endpoint,
            get_app_paths
        ])
        .on_page_load(|_, payload| {
            log::mark(&format!("page load {:?}", payload.event()));
            log::write_timeline();
        })
        .setup(move |app| {
            log::mark("setup hook");
            // The page may read thumbnails from disk (asset protocol) and nothing else.
            if let Some(dir) = &thumbs {
                let _ = std::fs::create_dir_all(dir);
                app.asset_protocol_scope()
                    .allow_directory(dir, true)
                    .map_err(|e| format!("thumbnail scope: {e}"))?;
            }
            let handle = app.handle().clone();
            engine.set_notify(move |event, message| {
                let _ = handle.emit(event, message);
            });
            log::write_timeline();
            Ok(())
        })
        .build(context)
        .expect("error while building ImageVault")
        .run(|app, event| match event {
            RunEvent::Ready => log::mark("event loop ready"),
            RunEvent::Exit => app.state::<Arc<Sidecar>>().stop(),
            _ => {}
        });
}
