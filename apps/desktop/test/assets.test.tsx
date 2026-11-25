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

const detail = (id: number, over: Record<string, unknown> = {}) => ({
  ...summary(id),
  title: { value: null, origin: null },
  taken_at: { value: "2026-03-10T10:00:00", origin: "source" },
  sha256: "ab".repeat(32),
  file_modified_at: null,
  updated_at: "2026-03-11T10:00:00",
  error: null,
  metadata: {
    camera_make: "Canon", camera_model: "EOS R6", lens: "RF 35mm", iso: 400, f_number: 1.8, exposure_s: 0.004,
    focal_length_mm: 35, gps_lat: null, gps_lon: null, gps_alt_m: null, color_profile: "sRGB", orientation: 1,
    extractor: "exiftool", extracted_at: "2026-03-11T10:00:00",
  },
  tags: [{ id: 7, name: "beach", origin: "user", confidence: null, confirmed: true }],
  collections: [],
  notes: [],
  locations: [
    { id: 1, source_id: 1, source_name: "Photos", rel_path: `IMG_${id}.JPG`, path: `D:\\Photos\\IMG_${id}.JPG`, state: "present", reachable: true, last_seen_at: null },
  ],
  derivatives: [{ kind: "thumb_512", rel_path: `thumbs/${id}_512.webp`, width: 512, height: 384 }],
  versions: [],
  jobs: [],
  ...over,
});

describe("Photo details", () => {
  const months = () => ({ total: 2, months: [{ month: "2026-03", count: 2 }] });
  const list = () => ({ items: [summary(1), summary(2)], next_cursor: null });

  it("opens with Enter, steps with the arrows, and closes with Esc", async () => {
    renderWith(<Assets />, {
      "GET /v1/assets:months": months,
      "GET /v1/assets": list,
      "GET /v1/assets/1": () => detail(1),
      "GET /v1/assets/2": () => detail(2),
      "POST /v1/assets/1/preview": () => ({ kind: "preview_1600", rel_path: "thumbs/1_1600.webp", width: 1600, height: 1200 }),
      "POST /v1/assets/2/preview": () => problem(409, "file_unreachable", "its drive is not connected"),
    });
    const grid = await screen.findByRole("grid", { name: "Photos" });
    await within(grid).findByRole("gridcell", { name: "IMG_1.jpg" });
    fireEvent.keyDown(grid, { key: "Enter" });
    const panel = await screen.findByRole("complementary", { name: "Photo details" });
    expect(await within(panel).findByRole("heading", { name: "IMG_1.JPG" })).toBeTruthy();
    expect(within(panel).getByText("Canon EOS R6")).toBeTruthy();
    // The sharper preview replaces the small thumbnail once it is made.
    await waitFor(() => expect(panel.querySelector(".preview-frame img")!.getAttribute("src")).toContain("1_1600.webp"));

    fireEvent.click(within(panel).getByRole("button", { name: "Next photo" }));
    expect(await within(panel).findByRole("heading", { name: "IMG_2.JPG" })).toBeTruthy();
    // No reachable file: keep the thumbnail and say why.
    expect(await within(panel).findByText(/Showing the small thumbnail: .*drive is not connected/)).toBeTruthy();
    expect(panel.querySelector(".preview-frame img")!.getAttribute("src")).toContain("2_512.webp");

    fireEvent.keyDown(within(panel).getByRole("button", { name: "Next photo" }), { key: "Escape" });
    await waitFor(() => expect(screen.queryByRole("complementary", { name: "Photo details" })).toBeNull());
  });

  it("adds a tag and a note, and says when the photo is gone", async () => {
    let tags = [{ id: 7, name: "beach", origin: "user", confidence: null, confirmed: true }];
    let notes: { id: number; body: string; created_at: string; updated_at: string }[] = [];
    const { calls } = renderWith(<Assets />, {
      "GET /v1/assets:months": months,
      "GET /v1/assets": list,
      "GET /v1/assets/1": () => detail(1, { tags, notes }),
      "GET /v1/assets/2": () => problem(404, "not_found", "no such asset"),
      "POST /v1/assets/1/preview": () => problem(422, "decode_failed", "cannot decode"),
      "GET /v1/tags": () => [],
      "GET /v1/collections": () => [],
      "POST /v1/assets/1/tags": async (req) => {
        const { names } = (await req.json()) as { names: string[] };
        tags = [...tags, { id: 8, name: names[0]!, origin: "user", confidence: null, confirmed: true }];
        return { tags };
      },
      "POST /v1/assets/1/notes": async (req) => {
        const { body } = (await req.json()) as { body: string };
        notes = [{ id: 1, body, created_at: "2026-03-11T10:00:00", updated_at: "2026-03-11T10:00:00" }];
        return notes[0];
      },
    });
    const grid = await screen.findByRole("grid", { name: "Photos" });
    fireEvent.doubleClick(await within(grid).findByRole("gridcell", { name: "IMG_1.jpg" }));
    const panel = await screen.findByRole("complementary", { name: "Photo details" });
    await within(panel).findByText("beach");

    const tagInput = within(panel).getByRole("combobox", { name: "Add a tag" });
    fireEvent.change(tagInput, { target: { value: " sunset " } });
    fireEvent.submit(tagInput.closest("form")!);
    expect(await within(panel).findByText("sunset")).toBeTruthy();

    fireEvent.change(within(panel).getByRole("textbox", { name: "New note" }), { target: { value: "Grandma's 80th" } });
    fireEvent.click(within(panel).getByRole("button", { name: "Add note" }));
    expect(await within(panel).findByText("Grandma's 80th")).toBeTruthy();
    const tagCall = calls.find((r) => r.method === "POST" && r.url.endsWith("/tags"))!;
    expect(await tagCall.json()).toEqual({ names: ["sunset"] });

    fireEvent.click(within(panel).getByRole("button", { name: "Next photo" }));
    expect(await within(panel).findByText("This photo is no longer in the library")).toBeTruthy();
  });
});
