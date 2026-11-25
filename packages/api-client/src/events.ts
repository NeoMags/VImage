import type { LiveEvent, ScanProgressEvent, ServerMessage, Source, TaskProgressEvent } from "@imagevault/shared-types";
import type { QueryClient, QueryKey } from "@tanstack/react-query";

import type { Endpoint } from "./client";
import { keys } from "./keys";

// ------------------------------------------------------------------------------ parsing
const isObj = (v: unknown): v is Record<string, unknown> => typeof v === "object" && v !== null;
const isInt = (v: unknown): v is number => Number.isInteger(v);
const SIMPLE = new Set(["tags.updated", "collections.updated", "resync", "library.restored"]);

function parseEvent(e: unknown): LiveEvent | null {
  if (!isObj(e) || typeof e.type !== "string") return null;
  switch (e.type) {
    case "asset.updated":
      if (e.all === true) return { type: "asset.updated", all: true };
      return Array.isArray(e.ids) && e.ids.every(isInt) ? { type: "asset.updated", ids: e.ids } : null;
    case "job.updated":
      return isInt(e.count) ? { type: "job.updated", count: e.count } : null;
    case "scan.progress":
      return isInt(e.source_id) && typeof e.status === "string" && isInt(e.files_seen)
        ? (e as unknown as ScanProgressEvent)
        : null;
    case "source.status":
      return isInt(e.source_id) && typeof e.status === "string" ? (e as unknown as LiveEvent) : null;
    case "task.progress":
      return typeof e.task === "string" && isInt(e.done) && isInt(e.total) ? (e as unknown as LiveEvent) : null;
    default:
      return SIMPLE.has(e.type) ? (e as unknown as LiveEvent) : null;
  }
}

/** Validates one message from WS /v1/events. Unknown event kinds are dropped, not fatal. */
export function parseMessage(raw: unknown): ServerMessage | null {
  let msg: unknown = raw;
  if (typeof raw === "string") {
    try {
      msg = JSON.parse(raw);
    } catch {
      return null;
    }
  }
  if (!isObj(msg)) return null;
  if (msg.type === "hello") return isInt(msg.flush_ms) ? { type: "hello", flush_ms: msg.flush_ms } : null;
  if (msg.type === "batch" && Array.isArray(msg.events)) {
    return { type: "batch", events: msg.events.map(parseEvent).filter((e): e is LiveEvent => e !== null) };
  }
  return null;
}

// ------------------------------------------------------------------------------ what to refresh
export interface RefreshPlan {
  /** Refetch everything (after a reconnect or a "resync"). */
  all: boolean;
  invalidate: QueryKey[];
  scans: ScanProgressEvent[];
  sourceStatus: { source_id: number; status: Source["status"] }[];
  tasks: TaskProgressEvent[];
}

/** Pure: which cached queries a batch of events makes stale. Events are hints; the engine is the truth. */
export function planFor(events: LiveEvent[]): RefreshPlan {
  const plan: RefreshPlan = { all: false, invalidate: [], scans: [], sourceStatus: [], tasks: [] };
  const add = (k: QueryKey) => {
    if (!plan.invalidate.some((x) => JSON.stringify(x) === JSON.stringify(k))) plan.invalidate.push(k);
  };
  for (const e of events) {
    switch (e.type) {
      case "resync":
      case "library.restored":
        plan.all = true;
        break;
      case "task.progress":
        plan.tasks.push(e);
        break;
      case "asset.updated":
        if ("all" in e) add(keys.asset.all);
        else for (const id of e.ids) add(keys.asset.one(id));
        add(keys.assets.all);
        add(keys.stats);
        break;
      case "job.updated":
        add(keys.jobs.all);
        add(keys.jobStats);
        add(keys.stats);
        break; // not health: it runs self-tests and is fetched only on demand
      case "scan.progress":
        plan.scans.push(e);
        if (e.status !== "running") {
          add(keys.sources);
          add(keys.stats);
        }
        break;
      case "source.status":
        plan.sourceStatus.push({ source_id: e.source_id, status: e.status });
        add(keys.stats);
        break;
      case "tags.updated":
        add(keys.tags);
        break;
      case "collections.updated":
        add(keys.collections);
        break;
    }
  }
  return plan;
}

/** Applies a plan: scan counters and source status are written straight into the cache (no refetch). */
export function applyPlan(qc: QueryClient, plan: RefreshPlan): void {
  for (const t of plan.tasks) qc.setQueryData(keys.task(t.task), t);
  if (plan.all) {
    void qc.invalidateQueries();
    return;
  }
  if (plan.scans.length || plan.sourceStatus.length) {
    qc.setQueryData<Source[]>(keys.sources, (old) =>
      old?.map((s) => {
        let next = s;
        for (const st of plan.sourceStatus) if (st.source_id === s.id) next = { ...next, status: st.status };
        for (const p of plan.scans) {
          if (p.source_id !== s.id) continue;
          next = {
            ...next,
            scanning: p.status === "running",
            last_scan: {
              id: p.run_id,
              kind: p.kind,
              status: p.status,
              files_seen: p.files_seen,
              files_new: p.files_new,
              files_changed: p.files_changed,
              files_missing: p.files_missing,
              started_at: next.last_scan?.id === p.run_id ? next.last_scan.started_at : new Date().toISOString(),
              finished_at: null,
              last_error: null,
            },
          };
        }
        return next;
      }),
    );
  }
  for (const key of plan.invalidate) void qc.invalidateQueries({ queryKey: key });
}

// ------------------------------------------------------------------------------ the socket
export type LiveState = "connecting" | "live" | "reconnecting" | "closed";

/** 0.5 s, 1 s, 2 s, 4 s, 8 s, then every 10 s. */
export function reconnectDelay(attempt: number): number {
  return Math.min(10_000, 500 * 2 ** attempt);
}

export interface LiveOptions {
  onEvents: (events: LiveEvent[]) => void;
  /** Called after every (re)connection: whatever happened while disconnected was missed. */
  onConnected?: (reconnect: boolean) => void;
  onState?: (state: LiveState) => void;
  WebSocketImpl?: typeof WebSocket;
}

/** One WebSocket to /v1/events with automatic reconnect. The token travels as a subprotocol. */
export class LiveEvents {
  private ws: WebSocket | null = null;
  private attempt = 0;
  private timer: ReturnType<typeof setTimeout> | null = null;
  private closed = false;
  private everConnected = false;
  private readonly WS: typeof WebSocket;

  constructor(
    private readonly endpoint: Endpoint,
    private readonly opts: LiveOptions,
  ) {
    this.WS = opts.WebSocketImpl ?? WebSocket;
    this.connect();
  }

  private connect(): void {
    this.opts.onState?.(this.everConnected ? "reconnecting" : "connecting");
    const ws = new this.WS(`ws://127.0.0.1:${this.endpoint.port}/v1/events`, [
      "ivsidecar",
      `bearer.${this.endpoint.token}`,
    ]);
    this.ws = ws;
    ws.onmessage = (ev: MessageEvent) => {
      const msg = parseMessage(ev.data);
      if (!msg) return;
      if (msg.type === "hello") {
        const reconnect = this.everConnected;
        this.everConnected = true;
        this.attempt = 0;
        this.opts.onState?.("live");
        this.opts.onConnected?.(reconnect);
      } else if (msg.events.length) {
        this.opts.onEvents(msg.events);
      }
    };
    ws.onclose = () => {
      if (this.ws !== ws) return;
      this.ws = null;
      if (this.closed) return;
      this.opts.onState?.("reconnecting");
      this.timer = setTimeout(() => this.connect(), reconnectDelay(this.attempt++));
    };
  }

  close(): void {
    this.closed = true;
    if (this.timer) clearTimeout(this.timer);
    this.ws?.close();
    this.ws = null;
    this.opts.onState?.("closed");
  }
}
