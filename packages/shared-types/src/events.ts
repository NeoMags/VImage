// Messages on WS /v1/events (sidecar events.py). Hand-written: WebSockets are not in OpenAPI.
// Checked against events.sample.json, which the sidecar's tests produce from the real bus.

export type ScanProgressEvent = {
  type: "scan.progress";
  source_id: number;
  run_id: number;
  kind: "full" | "incremental" | "watch_batch";
  status: "running" | "succeeded" | "failed" | "cancelled";
  files_seen: number;
  files_new: number;
  files_changed: number;
  files_missing: number;
};

/** Progress of a long request (B19: export). Latest counters only. */
export type TaskProgressEvent = { type: "task.progress"; task: "export"; done: number; total: number };

export type LiveEvent =
  | { type: "asset.updated"; ids: number[] }
  | { type: "asset.updated"; all: true }
  | { type: "job.updated"; count: number }
  | ScanProgressEvent
  | { type: "source.status"; source_id: number; status: "active" | "paused" | "unreachable" | "removed" }
  | { type: "tags.updated" }
  | { type: "collections.updated" }
  | TaskProgressEvent
  /** A backup replaced the library: refetch everything. */
  | { type: "library.restored" }
  /** The window fell behind and its backlog was dropped: refetch everything. */
  | { type: "resync" };

export type ServerMessage =
  | { type: "hello"; flush_ms: number }
  | { type: "batch"; events: LiveEvent[] };
