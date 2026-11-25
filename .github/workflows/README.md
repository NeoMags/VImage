Planned workflows (added with the first code): `ci.yml` on ubuntu (ruff, mypy, pytest incl. sidecar DB tests;
eslint, tsc, vitest) and `build-windows.yml` on a Windows runner (Tauri build + bundled sidecar, unsigned installer
artifact). Both are free for public repos and within the free monthly minutes for private repos (verify quota).
