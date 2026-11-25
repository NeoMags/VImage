import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";

import { Assets } from "../src/screens/Assets";
import { problem, renderWith } from "./helpers";

vi.mock("../src/native", () => ({
  revealInExplorer: vi.fn(),
  getAppPaths: vi.fn(async () => null),
  inTauri: () => false,
}));

// jsdom has no layout: give the gallery a size so the virtual list renders rows.
const restore: (() => void)[] = [];
beforeAll(() => {
  const set = (proto: object, key: string, value: number) => {
    const old = Object.getOwnPropertyDescriptor(proto, key);
    Object.defineProperty(proto, key, { configurable: true, get: () => value });
    restore.push(() => (old ? Object.defineProperty(proto, key, old) : delete (proto as Record<string, unknown>)[key]));
  };
  set(HTMLElement.prototype, "clientWidth", 1000);
  set(HTMLElement.prototype, "clientHeight", 800);
  set(HTMLElement.prototype, "offsetWidth", 1000);
  set(HTMLElement.prototype, "offsetHeight", 800);
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  localStorage.clear();
});
afterAll(() => restore.forEach((f) => f()));

const summary = (id: number, over: Record<string, unknown> = {}) => ({
  id,
  uid: `u${id}`,
  kind: "image",
  status: "ready",
  mime_type: "image/jpeg",
  date: "2026-03-10T10:00:00",
  taken_at: "2026-03-10T10:00:00",
  indexed_at: "2026-03-11T10:00:00",
  byte_size: 1_000_000,
  width: 4000,
  height: 3000,
  duration_ms: null,
  title: `IMG_${id}.jpg`,
  is_favorite: false,
  is_reviewed: false,
  is_hidden: false,
  thumb_path: `thumbs/${id}.webp`,
  availability: "present",
  file_name: `IMG_${id}.JPG`,
  ...over,
});

describe("All photos", () => {
  it("explains an empty library", async () => {
    renderWith(<Assets />, { "GET /v1/assets:months": () => ({ total: 0, months: [] }) });
    expect(await screen.findByText("No photos yet")).toBeTruthy();
  });

  it("shows the engine's error with a retry", async () => {
    renderWith(<Assets />, { "GET /v1/assets:months": () => problem(503, "busy", "database is busy") });
    expect(await screen.findByText(/database is busy/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("loads months on demand, shows badges, selects a range and hides it", async () => {
    let hidden: number[] = [];
    const { calls } = renderWith(<Assets />, {
      "GET /v1/assets:months": () => ({
        total: 3 - hidden.length,
        months: [{ month: "2026-03", count: 3 - hidden.length }],
      }),
      "GET /v1/assets": (req) => {
        const q = new URL(req.url).searchParams;
        expect(q.get("from")).toBe("2026-03-01");
        expect(q.get("to")).toBe("2026-03-31");
        const all = [summary(1, { is_favorite: true }), summary(2, { status: "failed" }), summary(3, { availability: "unreachable" })];
        return { items: all.filter((a) => !hidden.includes(a.id)), next_cursor: null };
      },
      "POST /v1/assets:bulk": async (req) => {
        const body = (await req.json()) as { ids: number[]; hide: boolean };
        expect(body.hide).toBe(true);
        hidden = body.ids;
        return { photos: body.ids.length, hidden_changed: body.ids.length };
      },
    });
    expect(await screen.findByRole("heading", { name: "March 2026" })).toBeTruthy();
    const grid = screen.getByRole("grid", { name: "Photos" });
    const cell1 = await within(grid).findByRole("gridcell", { name: "IMG_1.jpg" });
    expect(within(cell1).getByTitle("Favourite")).toBeTruthy();
    expect(within(grid).getByRole("gridcell", { name: "IMG_2.jpg" }).querySelector('[title="Processing failed"]')).toBeTruthy();
    expect(within(grid).getByRole("gridcell", { name: "IMG_3.jpg" }).querySelector('[title="Its drive is not connected"]')).toBeTruthy();
    expect(cell1.querySelector("img")!.getAttribute("src")).toBe("http://127.0.0.1:1/dev/thumbs/thumbs/1.webp?t=t");

    fireEvent.click(cell1);
    fireEvent.click(within(grid).getByRole("gridcell", { name: "IMG_2.jpg" }), { shiftKey: true });
    const bar = await screen.findByRole("toolbar", { name: "Selected photos" });
    expect(within(bar).getByText("2 photos selected")).toBeTruthy();
    fireEvent.click(within(bar).getByRole("button", { name: "Hide" }));
    expect(await screen.findByText("Hidden: 2 photos.")).toBeTruthy();
    await waitFor(() => expect(within(grid).queryByRole("gridcell", { name: "IMG_1.jpg" })).toBeNull());
    expect(screen.queryByRole("toolbar", { name: "Selected photos" })).toBeNull();
    expect(calls.filter((r) => r.method === "POST")).toHaveLength(1);
  });
});
