import type { LiveEvent, Source } from "@imagevault/shared-types";
import { QueryClient } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";

import sample from "../../shared-types/events.sample.json";
import { applyPlan, keys, LiveEvents, parseMessage, planFor, reconnectDelay } from "../src";

describe("parseMessage", () => {
  it("accepts every message the engine really sends (events.sample.json)", () => {
    for (const raw of sample) {
      const msg = parseMessage(JSON.stringify(raw));
      expect(msg, JSON.stringify(raw)).not.toBeNull();
      if (msg?.type === "batch") expect(msg.events).toHaveLength((raw as { events: unknown[] }).events.length);
    }
    const kinds = sample.flatMap((m) => ("events" in m && m.events ? m.events.map((e) => e.type) : [m.type]));
    expect(new Set(kinds)).toEqual(
      new Set(["hello", "asset.updated", "job.updated", "scan.progress", "source.status", "tags.updated", "collections.updated", "resync"]),
    );
  });

  it("rejects malformed messages and drops unknown events", () => {
    expect(parseMessage("not json")).toBeNull();
    expect(parseMessage({ type: "hello" })).toBeNull();
    expect(parseMessage({ type: "batch", events: [{ type: "asset.updated", ids: ["1"] }, { type: "new.thing" }, { type: "tags.updated" }] }))
      .toEqual({ type: "batch", events: [{ type: "tags.updated" }] });
  });
});

describe("planFor / applyPlan", () => {
  it("refreshes only what changed, once per key", () => {
    const plan = planFor([
      { type: "asset.updated", ids: [3, 9] },
      { type: "asset.updated", ids: [3] },
      { type: "job.updated", count: 5 },
      { type: "tags.updated" },
    ]);
    expect(plan.all).toBe(false);
    expect(plan.invalidate).toEqual([
      keys.asset.one(3), keys.asset.one(9), keys.assets.all, keys.stats, keys.jobs.all, keys.jobStats, keys.health, keys.tags,
    ]);
    expect(planFor([{ type: "resync" }]).all).toBe(true);
  });

  it("writes scan progress and drive status straight into the sources cache", () => {
    const qc = new QueryClient();
    const src = { id: 1, status: "active", scanning: false, last_scan: null } as unknown as Source;
    qc.setQueryData(keys.sources, [src, { ...src, id: 2 }]);
    const spy = vi.spyOn(qc, "invalidateQueries");
    const events: LiveEvent[] = [
      { type: "scan.progress", source_id: 1, run_id: 7, kind: "full", status: "running", files_seen: 40, files_new: 40, files_changed: 0, files_missing: 0 },
      { type: "source.status", source_id: 2, status: "unreachable" },
    ];
    applyPlan(qc, planFor(events));
    const [a, b] = qc.getQueryData<Source[]>(keys.sources)!;
    expect(a!.scanning).toBe(true);
    expect(a!.last_scan?.files_seen).toBe(40);
    expect(b!.status).toBe("unreachable");
    expect(spy.mock.calls.map((c) => c[0]?.queryKey)).toEqual([keys.stats]); // no refetch of sources mid-scan
  });
});

// ------------------------------------------------------------------------------ socket
class FakeWS {
  static all: FakeWS[] = [];
  onmessage: ((ev: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;
  constructor(readonly url: string, readonly protocols: string[]) {
    FakeWS.all.push(this);
  }
  send(data: unknown) {
    this.onmessage?.({ data: JSON.stringify(data) } as MessageEvent);
  }
  drop() {
    this.onclose?.();
  }
  close() {
    this.closed = true;
    this.onclose?.();
  }
}

describe("LiveEvents", () => {
  afterEach(() => {
    FakeWS.all = [];
    vi.useRealTimers();
  });

  it("backs off 0.5 s doubling to 10 s", () => {
    expect([0, 1, 2, 3, 4, 5, 9].map(reconnectDelay)).toEqual([500, 1000, 2000, 4000, 8000, 10000, 10000]);
  });

  it("authenticates by subprotocol, reconnects, and asks for a full refresh after a reconnect", () => {
    vi.useFakeTimers();
    const onEvents = vi.fn();
    const onConnected = vi.fn();
    const states: string[] = [];
    const live = new LiveEvents({ port: 5000, token: "abc" }, {
      onEvents, onConnected, onState: (s) => states.push(s), WebSocketImpl: FakeWS as unknown as typeof WebSocket,
    });
    const first = FakeWS.all[0]!;
    expect(first.url).toBe("ws://127.0.0.1:5000/v1/events");
    expect(first.protocols).toEqual(["ivsidecar", "bearer.abc"]);
    first.send({ type: "hello", flush_ms: 250 });
    first.send({ type: "batch", events: [{ type: "tags.updated" }] });
    expect(onConnected).toHaveBeenLastCalledWith(false);
    expect(onEvents).toHaveBeenCalledWith([{ type: "tags.updated" }]);

    first.drop(); // engine restarted
    expect(FakeWS.all).toHaveLength(1);
    vi.advanceTimersByTime(499);
    expect(FakeWS.all).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeWS.all).toHaveLength(2);
    FakeWS.all[1]!.drop(); // still down: next wait doubles
    vi.advanceTimersByTime(999);
    expect(FakeWS.all).toHaveLength(2);
    vi.advanceTimersByTime(1);
    FakeWS.all[2]!.send({ type: "hello", flush_ms: 250 });
    expect(onConnected).toHaveBeenLastCalledWith(true);

    live.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeWS.all).toHaveLength(3);
    expect(states).toEqual(["connecting", "live", "reconnecting", "reconnecting", "reconnecting", "reconnecting", "live", "closed"]);
  });
});
