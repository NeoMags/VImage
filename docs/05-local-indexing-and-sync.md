# 05 · Local indexing (and why there is no sync yet)

Desktop-only (D0): there is no cloud and no second device, so **there is nothing to synchronize**. "Sync" in this build means keeping the library in step with the files on disk. A cloud/multi-device sync design existed in the first plan; if that is ever wanted, the `uid` columns and the archived Postgres schema are the starting point.

## 1. Sources
| Kind | Identity of a file (`file_key`) | Offline behaviour |
|---|---|---|
| Local folder (NTFS) | volume serial + NTFS file id (stable across rename/move on the same volume) | — |
| External drive | volume GUID/serial + file id; drive letter may change → re-matched by `volume_id` | Unplugged → source `unreachable`, locations `unreachable`, assets stay searchable with cached thumbs |
| Network share / NAS | path + size + mtime (SMB file ids not reliable — verify) | Share down → `unreachable`; retry with backoff |
| exFAT/FAT32 drives | no stable file id → path + size + mtime | Renames look like delete + add; reconciled by sha256 |
| Google Takeout (D4) | path inside the extracted Takeout folder; JSON sidecar gives original date/GPS/description | Format is not officially documented — parse defensively, keep raw JSON |

Folders kept in sync by OneDrive/Google Drive/Dropbox desktop clients are just local folders here. Caution: "online-only" placeholder files would be **downloaded** if read; default rule skips files with the `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`/offline attribute (verify attribute behaviour per client in D1).

## 2. Scan → watch → reconcile
1. **Full scan** (new source, or user request): walk directories breadth-first; record progress in `scan_runs.resume_token` after each directory so a reboot resumes rather than restarts. For each file: stat → if `(file_key, size, mtime)` unchanged → skip; else enqueue `identify`.
2. **Watch:** `ReadDirectoryChangesW` (Python `watchdog` or Rust `notify` — decide in D1 spike) on each active source. Events are debounced (2 s) and batched into `scan_runs(kind='watch_batch')`. Files still being written (size changing, locked) are retried later.
3. **Reconcile:** every 6 h **[proposed]**, on app start, on drive reconnect, and on watcher buffer overflow: incremental scan comparing stat data with `file_locations`. Watchers miss events while the app is closed; reconcile covers that.

## 3. Change rules
| Observation | Result |
|---|---|
| New file, new sha256 | New asset + location, jobs queued |
| New file, sha256 already known | New location on existing asset (exact duplicate), no reprocessing |
| Same file_key, new path | Location updated (`moved`), tags/notes untouched |
| Same file_key, content changed | Old hash saved to `asset_versions`; asset re-hashed; if new hash already belongs to another asset → location moves to that asset |
| Location gone, source reachable | Location `missing`; asset kept; if no present locations remain, asset shows "missing" badge |
| Source unreachable | All its locations `unreachable`; nothing removed |
| User removes a source | Its locations deleted; assets with no remaining locations deleted from the library (DB only), after a confirmation dialog listing the count |

## 4. Resource control and resume
- Job runner pool size = user setting (default: physical cores − 1); process priority BELOW_NORMAL; optional "only when idle" (no input for 2 min) and "only on AC power".
- RAM cap: stage concurrency reduced when the sidecar's working set exceeds the cap; very large images processed with pyvips streaming/shrink-on-load.
- All state is in SQLite: after a crash or reboot, `running` jobs return to `queued` and scans continue from `resume_token`.

## 5. What happens if…
| Event | Behaviour |
|---|---|
| Same file event delivered twice | Idempotent: `(file_key,size,mtime)` check + `UNIQUE(asset_id, stage, pipeline_version)` on jobs |
| App killed mid-scan | Resumes from last completed directory |
| 10k files renamed at once | Watcher overflow → reconcile; file ids keep tags attached |
| Corrupt or unsupported file | Asset `failed`/`unsupported` with reason; scan continues |
| DB file corrupted | `PRAGMA integrity_check` on start; restore offered from `backups/` |

## 6. Tests required for D1 sign-off
Rename/move storm (10k files), drive unplug mid-scan and reconnect under a new letter, kill -9 during scan and during processing, 100k-file initial index (time + RAM on Neo's PC), read-only fixture folder proving no writes to sources.
