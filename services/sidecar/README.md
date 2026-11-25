# services/sidecar

Local Python process started by the desktop app. Owns the library database (SQLite + FTS5 + sqlite-vec),
will scan and watch sources and run the processing queue, and serves a localhost-only API to the UI.
Never writes to, moves or deletes user files.

Current state (spike B2): `/v1/health` only, plus database migrations and capability probes
(libvips thumbnailing, HEIC, ExifTool).

```
uv sync                                    # from repo root
cd services/sidecar && uv run pytest -q    # IV_SCHEMA_N=100000 for a bigger schema test
IV_TOKEN=<16+ chars> uv run python -m ivsidecar --library ./tmp-lib
uv run pyinstaller --noconfirm --distpath dist --workpath build packaging/ivsidecar.spec   # one-folder build
```

Startup contract with the desktop shell: env `IV_TOKEN` (required), `IV_PARENT_PID` (exit when the app dies),
optional `IV_LIBRARY_DIR`; prints `{"event":"ready","port":N}` on stdout once listening on 127.0.0.1.
