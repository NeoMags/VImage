# services/sidecar
Local Python process started by the desktop app. Owns the library database (`db/migrations`, SQLite + FTS5 +
sqlite-vec), scans and watches sources, runs the processing queue, and serves a localhost-only API to the UI.
Never writes to, moves or deletes user files.

Schema tests: `python -I services/sidecar/db/tests/test_schema.py 100000` (needs `sqlite-vec`, `numpy`).
