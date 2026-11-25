# 05 · Local indexing and synchronization

Applies from Phase 4 (desktop) and Phase 5 (mobile). Phase 1 is cloud-only (D1).

## 1. Model in one paragraph
Each device keeps a **local index** it can search fully offline. The cloud is the **merge point and system of record for user-authored metadata** (tags, notes, collections, confirmations). Files stay where they are: the desktop indexes in place and uploads only metadata + derivatives unless the user turns on "back up originals" for a source (D2). Sync is **pull by change-log cursor, push by idempotent mutation batch**, with per-field last-writer-wins for scalar fields and add/remove sets for memberships.

## 2. Desktop agent (Windows)
| Concern | Design |
|---|---|
| Local store | SQLite (WAL) with FTS5 for text and **sqlite-vec** for vectors (verify current license/maturity before Phase 4 — D3). Thumbnail cache on disk, LRU-capped (default 5 GB). |
| Source identity | Windows: volume serial + NTFS file ID (`GetFileInformationByHandle`) as `provider_item_id`, path as attribute → survives renames/moves within a volume. Network shares: path + size + mtime + hash (no stable file IDs guaranteed). External drives: volume GUID; drive offline → locations become `unreachable`, never `deleted`. |
| Change detection | `ReadDirectoryChangesW` watcher (via Rust `notify` crate in Tauri or `watchdog` in Python — pick one in Phase 4 spike) **plus** periodic reconciliation scan (watchers drop events on overflow and do not cover offline periods). USN journal considered later for NTFS-only fast rescans (needs elevated rights — verify). |
| Move/rename detection | Same file ID new path → `moved`. Unknown ID but known sha256 → treat as move if old location now missing, else second location. |
| Deletion at source | Location `deleted_at_source`; asset kept, shown with a badge. Nothing deleted in cloud automatically. |
| Hashing | Stream SHA-256 once per (file ID, size, mtime) tuple; pHash from decoded thumbnail. |
| Resource control | Worker pool size, CPU priority (BELOW_NORMAL), max RAM, GPU on/off, "only when idle / on AC power", pause. Persisted queue in SQLite → resumes after reboot. |
| Exclusions | Glob patterns, min size, system folders excluded by default, per-folder "never process faces". |

## 3. Mobile (Expo)
- `expo-media-library` for gallery; **limited-library access on iOS and Android partial access must be handled** — never assume full library. Screenshot auto-import = query new assets in the Screenshots album on app open/background task.
- Background: `expo-background-task` (OS-scheduled, minimum intervals and execution windows decided by iOS/Android — verify current Expo SDK docs at Phase 5) + foreground catch-up on every app open. Large uploads use background-capable upload (`expo-file-system` upload tasks — verify).
- Local DB: `expo-sqlite` holding the synced subset (metadata for all assets, thumbnails for recent + pinned collections, originals only when "available offline").

## 4. Sync protocol
**Pull.** `GET /sync/changes?since=<seq>` returns ordered entries from `change_log` (written by DB triggers on syncable tables). Device applies them in a transaction, stores `next_seq`. Gaps impossible (single sequence). If `since` is older than retention → `410 resync_required` → device does a snapshot pull.

**Push.** Device records local edits in an outbox table `{client_mutation_id (UUIDv7), entity, id, op, base_row_version, fields, created_at}`. `POST /sync/push` applies each mutation in its own savepoint:
- `client_mutation_id` already applied → return previous result (idempotent; safe to resend after crash).
- `base_row_version == current` → apply, bump `row_version`, write change_log.
- Stale → field-level merge: for each field, if server value changed since base and device also changed it → **conflict resolution rule** below; untouched fields merge cleanly.

**Conflict rules [proposed]**
| Data | Rule |
|---|---|
| Scalar user fields (title, favourite, review state, taken_at override) | Last-writer-wins by server receive time; losing value kept in `audit_logs` |
| Tags, collection membership | Set semantics: add/remove operations commute; remove wins over concurrent add only if remove is later |
| Notes body | No auto-merge: keep both, create a conflict copy note and flag it |
| AI-generated fields | Never synced up from a device if cloud has `user` origin for the same field |
| Deletes (hide) | Hide wins over concurrent edit; restore available |

**Assets from devices.** `sync/assets:upsert-local` keyed by `(connection_id, provider_item_id)`; asset matched by `(workspace_id, sha256)`. Device-made embeddings accepted only if `model_id` matches an active cloud model; otherwise the cloud recomputes from the uploaded preview.

## 5. Failure cases (and what happens)
| Case | Behaviour |
|---|---|
| Runs twice / retried push | Idempotent by `client_mutation_id` / `idempotency_key` |
| Crash mid-batch | Outbox rows remain until acknowledged; per-item savepoints mean partial batches are fine |
| Offline for weeks | Pull resumes from cursor; if pruned → snapshot resync, outbox still pushed afterwards |
| Drive unplugged | Locations `unreachable`; search still shows assets (thumbnail cached), "original unavailable" |
| Token expired | Device keeps working offline; sync pauses with a banner |
| Device lost | `DELETE /devices/{id}` revokes; refresh token revoked via Supabase admin API |
| Clock skew | Server receive time is authoritative; devices never send timestamps used for ordering |

## 6. Tests required before Phase 4/5 sign-off
Two-device conflict matrix per rule above; kill -9 during push; 10k-file rename storm; drive unplug mid-scan; 30-day offline then reconnect; 100k-file initial index wall-clock and RAM on target PC.
