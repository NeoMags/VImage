# packages/shared-types

The UI's view of the engine API, generated from the sidecar (B9). Do not hand-edit `openapi.json` or `src/api.ts`.

```
pnpm generate        # from the repo root: dumps openapi.json from the sidecar, then writes src/api.ts
```

CI regenerates both files and fails if they differ from the committed copies, so a sidecar
change the UI was not updated for fails the build instead of the app.

- `src/api.ts`: generated (`openapi-typescript`).
- `src/index.ts`: short names for the schemas the UI uses.
- `src/events.ts`: messages on `WS /v1/events`, written by hand (WebSockets are not part of OpenAPI).
  `events.sample.json` holds one real message of every kind, produced by the sidecar's tests.
