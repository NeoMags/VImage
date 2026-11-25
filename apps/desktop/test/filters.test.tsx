import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import { chipsFor, formatLabel, setFilters } from "../src/gallery/filters";
import { Assets } from "../src/screens/Assets";
import { renderWith } from "./helpers";

vi.mock("../src/native", () => ({
  revealInExplorer: vi.fn(),
  getAppPaths: vi.fn(async () => null),
  inTauri: () => false,
}));

beforeAll(() => {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
});
afterEach(() => {
  setFilters({});
  localStorage.clear();
});

const facets = (over: Record<string, unknown> = {}) => ({
  total: 120,
  kind: [
    { value: "image", count: 100 },
    { value: "screenshot", count: 20 },
  ],
  format: [
    { value: "image/jpeg", count: 90 },
    { value: "image/png", count: 30 },
  ],
  status: [{ value: "ready", count: 120 }],
  availability: [{ value: "present", count: 118 }, { value: "missing", count: 2 }],
  source: [{ id: 1, count: 120 }],
  collection: [{ id: 5, count: 3 }],
  tags: [
    { id: 1, name: "Beach", count: 12 },
    { id: 2, name: "Sunset", count: 4 },
  ],
  favorite: { yes: 7, no: 113 },
  reviewed: { yes: 0, no: 120 },
  date: { min: "2019-05-02", max: "2026-03-10" },
  size: { min: 1000, max: 9_000_000 },
  ...over,
});

const routes = (seen: URL[]) => ({
  "GET /v1/assets:months": (req: Request) => {
    seen.push(new URL(req.url));
    return { total: new URL(req.url).searchParams.has("kind") ? 20 : 120, months: [] };
  },
  "GET /v1/assets:facets": (req: Request) => {
    seen.push(new URL(req.url));
    return facets();
  },
  "GET /v1/tags": () => [
    { id: 1, name: "Beach", slug: "beach", count: 12 },
    { id: 2, name: "Sunset", slug: "sunset", count: 4 },
  ],
  "GET /v1/sources": () => [{ id: 1, display_name: "Pictures" }],
  "GET /v1/collections": () => [
    { id: 4, uid: "c4", name: "Trips", parent_id: null, kind: "manual", count: 0, created_at: "x" },
    { id: 5, uid: "c5", name: "Italy", parent_id: 4, kind: "manual", count: 3, created_at: "x" },
  ],
});

describe("Filters", () => {
  it("shows counts, filters the gallery, and clears from the chips", async () => {
    const seen: URL[] = [];
    renderWith(<Assets />, routes(seen));
    fireEvent.click(await screen.findByRole("button", { name: "Filters" }));
    const panel = await screen.findByRole("complementary", { name: "Filters" });
    const shots = await within(panel).findByRole("checkbox", { name: /Screenshots\s*20/ });
    expect(within(panel).getByText("120 match")).toBeTruthy();
    expect(within(panel).getByRole("checkbox", { name: /PNG\s*30/ })).toBeTruthy();
    // nested collections are indented under their parent
    const coll = within(panel).getByRole("combobox", { name: "Collection" }) as HTMLSelectElement;
    expect([...coll.options].map((o) => o.textContent)).toEqual(["Any", "Trips (0)", "  Italy (3)"]);

    fireEvent.click(shots);
    await waitFor(() => expect(seen.some((u) => u.pathname.endsWith("months") && u.searchParams.get("kind") === "screenshot")).toBe(true));
    expect(screen.getByRole("button", { name: "Filters (1)" })).toBeTruthy();
    const chips = screen.getByLabelText("Active filters");
    expect(within(chips).getByText("Screenshots")).toBeTruthy();

    fireEvent.click(within(panel).getByRole("checkbox", { name: /Beach/ }));
    fireEvent.click(within(panel).getByRole("checkbox", { name: /Sunset/ }));
    expect(await within(panel).findByText("Photos must have all ticked tags.")).toBeTruthy();
    await waitFor(() =>
      expect(seen.some((u) => u.pathname.endsWith("facets") && u.searchParams.getAll("tag").join() === "1,2")).toBe(true),
    );

    fireEvent.click(within(chips).getByRole("button", { name: "Remove filter Screenshots" }));
    expect(screen.getByRole("button", { name: "Filters (1)" })).toBeTruthy(); // the tags stay
    fireEvent.click(within(screen.getByLabelText("Active filters")).getByRole("button", { name: "Clear all" }));
    expect(screen.getByRole("button", { name: "Filters" })).toBeTruthy();
    expect(screen.queryByLabelText("Active filters")).toBeNull();
  });

  it("commits sizes in MB on Enter and says when nothing matches", async () => {
    const seen: URL[] = [];
    renderWith(<Assets />, {
      ...routes(seen),
      "GET /v1/assets:months": (req: Request) => {
        seen.push(new URL(req.url));
        return { total: new URL(req.url).searchParams.has("min_bytes") ? 0 : 120, months: [] };
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Filters" }));
    const min = await screen.findByRole("spinbutton", { name: "Smallest file size in MB" });
    fireEvent.change(min, { target: { value: "2.5" } });
    expect(seen.some((u) => u.searchParams.has("min_bytes"))).toBe(false); // not while typing
    fireEvent.keyDown(min, { key: "Enter" });
    expect(await screen.findByText("No photos match these filters")).toBeTruthy();
    expect(seen.some((u) => u.searchParams.get("min_bytes") === "2500000")).toBe(true);
    expect(within(screen.getByLabelText("Active filters")).getByText("≥ 2.5 MB")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => expect(screen.queryByText("No photos match these filters")).toBeNull());
  });

  it("keeps filters while switching screens (in memory only)", async () => {
    setFilters({ favorite: true });
    const { unmount } = renderWith(<Assets />, routes([]));
    expect(await screen.findByRole("button", { name: "Filters (1)" })).toBeTruthy();
    unmount();
    renderWith(<Assets />, routes([]));
    expect(await screen.findByRole("button", { name: "Filters (1)" })).toBeTruthy();
    expect(within(screen.getByLabelText("Active filters")).getByText("Favourites")).toBeTruthy();
  });
});

describe("filter labels", () => {
  it("names formats and chips plainly", () => {
    expect(["image/jpeg", "image/x-canon-cr2", "image/heic", "video/mp4"].map(formatLabel)).toEqual(["JPEG", "CR2", "HEIC", "MP4"]);
    const names = { tag: () => "Beach", source: () => "Pictures", collection: () => "Italy" };
    const chips = chipsFor({ from: "2024-01-01", max_w: 800, tag: [1], collection: 5, reviewed: false }, names);
    expect(chips.map((c) => c.label)).toEqual(["From 2024-01-01", "At most 800 × any px", "#Beach", "In “Italy”", "Not reviewed"]);
    expect(chips[2]!.remove({ tag: [1, 2] })).toEqual({ tag: [2] });
  });
});
