# apps/desktop

The ImageVault Windows app: a Tauri 2 shell (`src-tauri/`, B10) around a React + TypeScript UI
(`src/`, B11). The UI talks to the local engine only through `@imagevault/api-client`.

```
pnpm install                                   # repo root, once
# build the engine first (services/sidecar, see its README), then:
pnpm --filter @imagevault/desktop tauri build  # installer: src-tauri/target/release/bundle/nsis/
# develop: IV_SIDECAR_PATH=<path to ivsidecar(.exe)> IV_DEV_ORIGINS=http://localhost:1420 pnpm --filter @imagevault/desktop tauri dev
```

| Command (in this folder) | What it does |
|---|---|
| `pnpm test` | Component tests (Vitest + Testing Library, fake engine) |
| `pnpm e2e` | Built UI in Chromium against a real engine on a temporary library (needs `uv sync` at the repo root). `IV_E2E_SHOTS=<dir>` saves screenshots |
| `pnpm typecheck` | `tsc` over the app, tests and e2e |

Screens (doc 07): Dashboard, Sources (add / scan / remove folders), Processing (queue, failures,
resources), Settings. Every screen has loading, empty and error states; a banner shows while the
live connection to the engine is reconnecting, and a full-screen state while it starts or after it
gave up. The photo grid arrives in B12.

The shell starts the engine before the window, restarts it after a crash, and stops it cleanly on
quit. It never opens the library database itself.
