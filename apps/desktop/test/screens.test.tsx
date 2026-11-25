import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { Dashboard } from "../src/screens/Dashboard";
import { Processing } from "../src/screens/Processing";
import { Sources, sourceState } from "../src/screens/Sources";
import { problem, renderWith, source, stats } from "./helpers";

vi.mock("../src/native", () => ({
  pickFolder: vi.fn(async () => "D:\\Photos"),
  revealInExplorer: vi.fn(),
  getAppPaths: vi.fn(async () => ({ library: "C:\\Lib", logs: "C:\\Lib\\logs", thumbs: "C:\\Lib\\thumbs" })),
  inTauri: () => false,
}));

describe("Dashboard", () => {
  it("first run: asks for a folder and adds the one picked", async () => {
    let sources: unknown[] = [];
    const { calls } = renderWith(<Dashboard />, {
      "GET /v1/stats": () => stats({ photos: { ...stats().photos, total: 0 } }),
      "GET /v1/sources": () => sources,
      "POST /v1/sources": () => {
        sources = [source()];
        return { id: 1, scan_run_id: 1 };
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Add folder…" }));
    await screen.findByText("Photos"); // the dashboard replaces the empty state once the folder exists
    const post = calls.find((r) => r.method === "POST")!;
    expect(await post.json()).toEqual({ path: "D:\\Photos" });
  });

  it("shows the counters", async () => {
    renderWith(<Dashboard />, { "GET /v1/stats": () => stats(), "GET /v1/sources": () => [source()] });
    expect(await screen.findByText("1,234")).toBeTruthy();
    expect(screen.getByText("On offline drives").parentElement!.textContent).toContain("30");
    expect(screen.getByRole("link", { name: "2" }).getAttribute("href")).toBe("/processing");
  });

  it("shows the engine's error with a retry and the log folder", async () => {
    let fail = true;
    renderWith(<Dashboard />, {
      "GET /v1/stats": () => (fail ? problem(500, "internal", "OperationalError") : stats()),
      "GET /v1/sources": () => [source()],
    });
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("OperationalError");
    await waitFor(() => expect(alert.textContent).toContain("C:\\Lib\\logs"));
    fail = false;
    fireEvent.click(within(alert).getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("1,234")).toBeTruthy();
  });
});

describe("Sources", () => {
  it("labels each folder's state", () => {
    const s = (o: Record<string, unknown>) => sourceState(source(o) as never).label;
    expect(s({ status: "unreachable" })).toBe("Offline");
    expect(s({ scanning: true, last_scan: { files_seen: 1500 } })).toBe("Scanning · 1,500 files");
    expect(s({ last_scan: { status: "failed", last_error: "x" }, watching: false })).toBe("Scan failed");
    expect(s({})).toBe("Live");
  });

  it("removes a folder only with the count the user saw", async () => {
    const { calls } = renderWith(<Sources />, {
      "GET /v1/sources": () => [source()],
      "GET /v1/sources/1/removal": () => ({ id: 1, display_name: "Pictures", root_path: "C:\\P", files: 10, assets_only_here: 8 }),
      "DELETE /v1/sources/1": () => ({ removed: 1, assets_deleted: 8, thumbnails_deleted: 16 }),
    });
    fireEvent.click(await screen.findByRole("button", { name: "Remove" }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("8 photos");
    expect(dialog.textContent).toContain("Your files on disk are not touched.");
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove folder" }));
    expect(await screen.findByText(/8 photos left the library/)).toBeTruthy();
    const del = calls.find((r) => r.method === "DELETE")!;
    expect(new URL(del.url).searchParams.get("confirm")).toBe("8");
  });

  it("explains why a folder could not be added", async () => {
    renderWith(<Sources />, {
      "GET /v1/sources": () => [],
      "POST /v1/sources": () => problem(400, "validation_failed", "folder is already in the library"),
    });
    fireEvent.click(await screen.findByRole("button", { name: "+ Add folder" }));
    expect((await screen.findByRole("alert")).textContent).toContain("folder is already in the library");
  });
});

describe("Processing", () => {
  it("lists failed jobs and retries one", async () => {
    const { calls } = renderWith(<Processing />, {
      "GET /v1/jobs/stats": () => ({ jobs: { failed: 1, queued: 2 }, by_stage: { thumbnail: { failed: 1 } }, assets: {}, queue: { paused: false, gate: null, slots: 2, busy: 0 } }),
      "GET /v1/jobs": () => ({
        items: [{ id: 9, stage: "thumbnail", status: "failed", priority: 0, attempts: 3, crashes: 0, asset_id: 1, location_id: null, path: "a/b.jpg", last_error: "decode_failed: truncated", run_after: null, started_at: null, finished_at: null }],
        next_before_id: null,
      }),
      "POST /v1/jobs/9/retry": () => ({ id: 9, status: "queued" }),
      "GET /v1/settings/resources": () => ({ threads: 4, max_ram_mb: 4096, only_when_idle: false, only_on_ac: false, gpu: "auto" }),
    });
    const row = (await screen.findByText("a/b.jpg")).closest("tr")!;
    expect(row.textContent).toContain("decode_failed: truncated");
    fireEvent.click(within(row).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(calls.some((r) => r.method === "POST" && r.url.endsWith("/v1/jobs/9/retry"))).toBe(true));
    expect(screen.getByRole("button", { name: "Retry all failed (1)" })).toBeTruthy();
  });

  it("saves resource limits", async () => {
    const { calls } = renderWith(<Processing />, {
      "GET /v1/jobs/stats": () => ({ jobs: {}, by_stage: {}, assets: {}, queue: null }),
      "GET /v1/jobs": () => ({ items: [], next_before_id: null }),
      "GET /v1/settings/resources": () => ({ threads: 4, max_ram_mb: 4096, only_when_idle: false, only_on_ac: false, gpu: "auto" }),
      "PUT /v1/settings/resources": async (req) => req.json(),
    });
    fireEvent.change(await screen.findByLabelText("Worker threads"), { target: { value: "2" } });
    fireEvent.click(screen.getByLabelText("Only when plugged in"));
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText("Saved");
    const put = calls.find((r) => r.method === "PUT")!;
    expect(await put.json()).toMatchObject({ threads: 2, only_on_ac: true });
  });
});
