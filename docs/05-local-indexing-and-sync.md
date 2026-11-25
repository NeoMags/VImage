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
1. **Full scan** (new source, or user request; implemented in B6): walk directories depth-first in sorted name order, so the order is fixed and `scan_runs.resume_token` (the last finished folder) is enough to resume after a kill or reboot. For each file: directory metadata only → if `(file_key, size, mtime)` unchanged → skip without opening it (path updated if it moved); else enqueue `identify`. Skipped: hidden/system files and folders, `$RECYCLE.BIN`, `System Volume Information`, `Thumbs.db`, `desktop.ini`, `~$*`, `*.tmp/.part/.crdownload`, symlinks/junctions, the library folder, per-source globs, and online-only cloud files (attributes `OFFLINE`/`RECALL_ON_OPEN`/`RECALL_ON_DATA_ACCESS`; option `settings.scan.include_online_only`). After a complete scan, locations not seen are marked `missing`. Measured on the Linux CI container: an unchanged 100k-file tree rescans in about 3 s (Windows timing to be measured on Neo's PC).
2. **Watch** (B7): Python `watchdog` (Apache-2.0; `ReadDirectoryChangesW` on Windows) on each active source, one recursive watch per source. Events only say *which folders* to look at again: file events → their folder; a folder created, moved or deleted → its whole subtree. Events are debounced (2 s after the last one) and each source's folders are re-listed as one `scan_runs(kind='watch_batch')`, using the same identity, exclusion and "never open unchanged files" rules as a full scan. A missed, doubled or reordered event therefore costs a re-list, never a wrong library. Files modified less than 2 s ago (a copy still running) are looked at again later, and nothing in their folder is marked missing meanwhile; after 1 min they are taken as they are. Events inside the library folder (when the library sits inside a source) are ignored. A batch waits while a full scan of the same source runs (per-source lock).
3. **Reconcile** (B7): a full scan of every watched source on app start (changes made while the app was closed), every 6 h, when a drive comes back, and when the OS dropped events (`EventsLostEvent`, Windows buffer overflow) or a batch touches more than 500 folders.
4. **Reachability** (B7): every 30 s, and at once when the watched folder itself is reported deleted, each source's folder is checked. Gone → source and its present locations `unreachable` (never `missing`), watch stopped. On Windows a drive that comes back under another letter is found again by its volume serial and `root_path` is updated. Back → `active`, locations `present`, watch re-created, full scan.

## 3. Change rules
| Observation | Result |
|---|---|
| New file, new sha256 | New asset + location, jobs queued |
| New file, sha256 already known | New location on existing asset (exact duplicate), no reprocessing |
| Same file_key, new path | Location updated (`moved`), tags/notes untouched |
| Same file_key, content changed (edit), asset lives nowhere else | Same asset takes the new content: old hash saved to `asset_versions`, metadata and thumbnail redone; tags, notes and favourite stay (B7 decision 5) |
| Content changed, old content also at another location | New asset for the edited file, with the tags copied; the other location keeps the old asset |
| Content changed to content already in the library | Location moves to that asset; the old asset stays with its tags |
| Location gone, source reachable | Location `missing`; asset kept; if no present locations remain, asset shows "missing" badge |
| Source unreachable | All its locations `unreachable`; nothing removed |
| User removes a source | `GET /v1/sources/{id}/removal` gives the counts; `DELETE /v1/sources/{id}?confirm=<assets_only_here>` stops its watch and scans, deletes the assets found only there (with tags, notes and thumbnails) and the source with its locations. Photos also in another folder stay. The folder itself is never touched. A stale count is refused (409). Logged in `audit_log` |

## 4. Resource control and resume
- Job runner pool size = user setting (default: physical cores − 1); process priority BELOW_NORMAL; optional "only when idle" (no input for 2 min) and "only on AC power".
- RAM cap: stage concurrency reduced when the sidecar's working set exceeds the cap; very large images processed with pyvips streaming/shrink-on-load.
- All state is in SQLite: after a crash or reboot, `running` jobs return to `queued` and scans continue from `resume_token`.

## 5. What happens if…
| Event | Behaviour |
|---|---|
| Same file event delivered twice | Idempotent: `(file_key,size,mtime)` check + `UNIQUE(asset_id, stage, pipeline_version)` on jobs |
| App killed mid-scan | Resumes from last completed directory |
| 10k files renamed at once | One or a few watch batches, or a full scan if events were dropped; file ids keep tags attached. Measured on the Linux CI container: 10,000 renames settled 7 s after they started, no location lost |
| Corrupt or unsupported file | Asset `failed`/`unsupported` with reason; scan continues |
| DB file corrupted | `PRAGMA integrity_check` on start. **Built (B19):** a damaged `library.db` (plus WAL/SHM) is moved to `backups/damaged-<stamp>.db`, never deleted; the engine starts on an empty library and the window shows the backups with Restore (or "Start with an empty library") |

## 6. Tests required for D1 sign-off
Rename/move storm (10k files), drive unplug mid-scan and reconnect under a new letter, kill -9 during scan and during processing, 100k-file initial index (time + RAM on Neo's PC), read-only fixture folder proving no writes to sources.

## Read-only guarantee, tested (B20)

`services/sidecar/tests/test_readonly.py` runs a real engine and its worker processes over the 50-file fixture folder made read-only (POSIX: no write bits; Windows: read-only attribute), on Ubuntu and Windows CI. It goes through the full scan and every stage, 1600 px previews, reveal, every edit the API offers, a new file seen by the watcher, a full rescan and source removal. It fails if:

- the audit hook `tests/readonly_audit/sitecustomize.py`, loaded into every engine process through `PYTHONPATH`, records a write-mode `open`, or `remove`/`rename`/`replace`/`mkdir`/`rmdir`/`utime`/`chmod`/`shutil` move or copy, under the source folder;
- the folder's paths, sizes, mtimes or SHA-256 differ before and after (this covers native code the hook cannot see: libvips, ExifTool);
- any job failed with a permission error.

ExifTool gets every argument list through `assert_read_only` first, which refuses tag assignments (`-Tag=…`, `-Tag<…`) and the writing options (`-o`, `-w`, `-overwrite_original`, `-tagsFromFile`, `-geotag`, …). Not checked: last-access times, which the OS updates on reads.
