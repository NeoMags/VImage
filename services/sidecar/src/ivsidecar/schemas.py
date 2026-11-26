"""Response models for every HTTP route (B9).

They exist for the UI: `scripts/dump_openapi.py` writes them into `/v1/openapi.json`, and
`packages/shared-types` is generated from that file, so a change here that the UI does not
expect fails the TypeScript build.

`extra="forbid"` is deliberate: if a route starts returning a key its model does not declare,
the response fails validation (HTTP 500 in tests) instead of the key being dropped silently.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

AssetKind = Literal["image", "screenshot", "video", "pdf", "drawing", "document", "other"]
AssetStatus = Literal["pending", "processing", "ready", "failed", "unsupported"]
Availability = Literal["present", "missing", "unreachable"]
Origin = Literal["source", "ai", "user"]
JobStatus = Literal["queued", "running", "succeeded", "failed", "skipped", "dead"]
LocationState = Literal["present", "moved", "missing", "unreachable"]
SourceKind = Literal["local_folder", "external_drive", "network_share", "takeout_import", "clipboard", "drag_drop"]
SourceStatus = Literal["active", "paused", "unreachable", "removed"]
ScanKind = Literal["full", "incremental", "watch_batch"]
ScanStatus = Literal["running", "succeeded", "failed", "cancelled"]


class Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------- assets
class AssetSummary(Out):
    id: int
    uid: str
    kind: AssetKind
    status: AssetStatus
    mime_type: str
    date: str | None
    taken_at: str | None
    indexed_at: str
    byte_size: int
    width: int | None
    height: int | None
    duration_ms: int | None
    title: str | None
    is_favorite: bool
    is_reviewed: bool
    is_hidden: bool
    thumb_path: str | None
    """Relative to the library folder; the UI loads it through the shell's thumbnail scope."""
    availability: Availability
    file_name: str | None
    """Name of the file in one of its locations (a present one first); null if it has none."""


class SearchHit(AssetSummary):
    score: float
    """Cosine similarity, higher is closer. Only comparable within one search."""


class SearchResults(Out):
    items: list[SearchHit]
    photos_analysed: int
    """Photos with a search vector so far; the others cannot be found by description yet."""


class TeachResult(Out):
    tag_id: int
    name: str
    photos_tagged: int


class TaughtTag(Out):
    tag_id: int
    name: str
    prompt: str
    threshold: float
    photos: int
    created_at: str


class Untaught(Out):
    photos_untagged: int


class AssetPage(Out):
    items: list[AssetSummary]
    next_cursor: str | None


class MonthCount(Out):
    month: str | None
    """YYYY-MM of the asset's date; null only for assets with no date at all."""
    count: int


class FacetValue(Out):
    value: str
    count: int


class FacetId(Out):
    id: int
    count: int


class FacetTag(Out):
    id: int
    name: str
    count: int


class YesNo(Out):
    yes: int
    no: int


class DateBounds(Out):
    min: str | None
    max: str | None


class SizeBounds(Out):
    min: int | None
    max: int | None


class AssetFacets(Out):
    total: int
    kind: list[FacetValue]
    format: list[FacetValue]
    status: list[FacetValue]
    availability: list[FacetValue]
    source: list[FacetId]
    collection: list[FacetId]
    tags: list[FacetTag]
    """Tags you added, from files, or AI tags you accepted on at least one photo."""
    ai_tags: list[FacetTag]
    """Tags only the AI has suggested so far (T3)."""
    favorite: YesNo
    reviewed: YesNo
    date: DateBounds
    size: SizeBounds


class AssetMonths(Out):
    total: int
    months: list[MonthCount]


class WithOrigin(Out):
    value: str | None
    origin: Origin | None


class AssetMetadata(Out):
    camera_make: str | None
    camera_model: str | None
    lens: str | None
    iso: int | None
    exposure_s: float | None
    f_number: float | None
    focal_length_mm: float | None
    orientation: int | None
    color_profile: str | None
    gps_lat: float | None
    gps_lon: float | None
    gps_alt_m: float | None
    extractor: str
    extracted_at: str


class Derivative(Out):
    kind: Literal["thumb_256", "thumb_512", "preview_1600", "video_poster"]
    rel_path: str
    width: int | None
    height: int | None


class Location(Out):
    id: int
    source_id: int
    source_name: str
    rel_path: str
    path: str
    state: LocationState
    reachable: bool
    last_seen_at: str | None


class AssetTag(Out):
    id: int
    name: str
    origin: Origin
    confidence: float | None
    confirmed: bool


class CollectionRef(Out):
    id: int
    name: str


class Note(Out):
    id: int
    body: str
    created_at: str
    updated_at: str


class AssetVersion(Out):
    sha256: str
    byte_size: int
    seen_at: str


class AssetJob(Out):
    id: int
    """For POST /v1/jobs/{id}/retry."""
    stage: str
    status: JobStatus
    attempts: int
    last_error: str | None
    finished_at: str | None


class AssetDetail(Out):
    id: int
    uid: str
    kind: AssetKind
    status: AssetStatus
    mime_type: str
    date: str | None
    taken_at: WithOrigin
    indexed_at: str
    byte_size: int
    width: int | None
    height: int | None
    duration_ms: int | None
    title: WithOrigin
    is_favorite: bool
    is_reviewed: bool
    is_hidden: bool
    thumb_path: str | None
    availability: Availability
    file_name: str | None
    file_modified_at: str | None
    error: str | None
    updated_at: str
    sha256: str
    metadata: AssetMetadata | None
    derivatives: list[Derivative]
    locations: list[Location]
    tags: list[AssetTag]
    collections: list[CollectionRef]
    notes: list[Note]
    versions: list[AssetVersion]
    jobs: list[AssetJob]


class BulkResult(Out):
    photos: int
    tags_added: int | None = None
    tags_removed: int | None = None
    ai_tags_confirmed: int | None = None
    added_to_collection: int | None = None
    removed_from_collection: int | None = None
    set_favorite: int | None = None
    set_reviewed: int | None = None
    hidden_changed: int | None = None


class Hidden(Out):
    id: int
    is_hidden: bool


class RevealPath(Out):
    path: str


class AssetTags(Out):
    tags: list[AssetTag]


# ---------------------------------------------------------------------------- tags, collections
class Tag(Out):
    id: int
    name: str
    slug: str
    count: int


class RenamedTag(Out):
    id: int
    name: str
    slug: str


class TagDeleted(Out):
    photos_untagged: int


class TagMerged(Out):
    photos_moved: int


class Collection(Out):
    id: int
    uid: str
    name: str
    parent_id: int | None
    kind: Literal["manual", "smart"]
    count: int
    created_at: str


class NewCollection(Out):
    id: int
    name: str
    parent_id: int | None
    count: int


class Added(Out):
    added: int


class Removed(Out):
    removed: int


# ---------------------------------------------------------------------------- jobs, queue
class QueueRuntime(Out):
    paused: bool
    gate: str | None
    """Why work is held back right now (e.g. 'on battery'), or null."""
    slots: int | None
    busy: int


class JobStats(Out):
    jobs: dict[JobStatus, int]
    by_stage: dict[str, dict[JobStatus, int]]
    assets: dict[AssetStatus, int]
    queue: QueueRuntime | None


class Paused(Out):
    paused: bool


class Requeued(Out):
    requeued: int


class Job(Out):
    id: int
    stage: str
    status: JobStatus
    priority: int
    attempts: int
    crashes: int
    asset_id: int | None
    location_id: int | None
    path: str | None
    last_error: str | None
    run_after: str | None
    started_at: str | None
    finished_at: str | None


class JobPage(Out):
    items: list[Job]
    next_before_id: int | None


class JobRetried(Out):
    id: int
    status: Literal["queued"]


# ---------------------------------------------------------------------------- stats, health
class PhotoCounts(Out):
    total: int
    by_status: dict[AssetStatus, int]
    by_kind: dict[AssetKind, int]
    hidden: int
    favorites: int
    present: int
    unreachable: int
    missing: int


class LibraryStats(Out):
    photos: PhotoCounts
    files_unsupported: int
    tags: int
    collections: int
    sources: dict[SourceStatus, int]
    last_scan_at: str | None
    jobs: dict[JobStatus, int]
    queue: QueueRuntime | None
    db_version: int
    db_latest: int


class HealthQueue(Out):
    queued: int
    running: int
    failed: int


class Health(Out):
    status: Literal["ok"]
    version: str
    needs_restore: str | None  # library.db was damaged at start and set aside under this name (B19)
    db_version: int
    queue: HealthQueue
    uptime_s: float
    python: str
    platform: str
    frozen: bool
    # Diagnostics for the About/support screen; their keys vary with the build, so they stay loose.
    db: dict[str, Any]
    vips: dict[str, Any]
    heif: dict[str, Any]
    stages: dict[str, Any]
    exiftool: dict[str, Any]


# ---------------------------------------------------------------------------- sources
class ScanRun(Out):
    id: int
    kind: ScanKind
    status: ScanStatus
    files_seen: int
    files_new: int
    files_changed: int
    files_missing: int
    started_at: str
    finished_at: str | None
    last_error: str | None


class Source(Out):
    id: int
    uid: str
    kind: SourceKind
    display_name: str
    root_path: str
    status: SourceStatus
    last_scan_at: str | None
    last_error: str | None
    files: dict[LocationState, int]
    scanning: bool
    watching: bool
    last_scan: ScanRun | None


class SourceAdded(Out):
    id: int
    scan_run_id: int


class ScanStarted(Out):
    scan_run_id: int


class ScanCancelled(Out):
    cancelled: bool


class ShuttingDown(Out):
    stopping: bool


class RemovalPreview(Out):
    id: int
    display_name: str
    root_path: str
    files: int
    assets_only_here: int


class SourceRemoved(Out):
    removed: int
    assets_deleted: int
    thumbnails_deleted: int


# ---------------------------------------------------------------------------- backups (B19)
BackupKind = Literal["auto", "manual", "pre-restore", "pre-migration", "damaged"]


class Backup(Out):
    name: str
    kind: BackupKind
    created_at: str
    size: int
    schema_version: int | None


class Backups(Out):
    backups: list[Backup]
    folder: str
    needs_restore: str | None  # the damaged file's name when the library could not be opened at start


class BackupMade(Out):
    backup: Backup
    pruned: list[str]
    copy_error: str | None


class Restored(Out):
    restored_from: str
    schema_version: int
    pre_restore: str
    thumbnails_requeued: int


class Exported(Out):
    path: str
    photos: int
    bytes: int
    format: Literal["json", "csv"]


# --------------------------------------------------------------------------- AI (T2)
class AiDownload(Out):
    state: Literal["idle", "downloading", "failed", "done"]
    done_bytes: int
    total_bytes: int
    error: str | None


class AiStatus(Out):
    installed: bool
    enabled: bool
    max_tags: int
    download: AiDownload
    photos_done: int
    photos_waiting: int
    photos_failed: int
    photos_with_ai_tags: int
