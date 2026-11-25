# apps/desktop

Tauri 2 Windows app. Current state (spike B2): a plain HTML page that starts the sidecar and shows its health
report. The real UI (React + TypeScript, Vite, Tailwind, shadcn/ui, TanStack Query) replaces `ui/` in backlog B11.

```
# build the sidecar first (services/sidecar, see its README), then:
npm ci
npx tauri build          # installer: src-tauri/target/release/bundle/nsis/
# dev without bundling: IV_SIDECAR_PATH=<path to ivsidecar.exe> npx tauri dev
```
The Rust shell only starts/stops the sidecar and hands its port + token to the UI; it never opens the database.
