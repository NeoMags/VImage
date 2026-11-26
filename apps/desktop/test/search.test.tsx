import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { Route, Routes, useLocation } from "react-router";
import { describe, expect, it, vi } from "vitest";

import { SearchBox } from "../src/App";
import { SearchScreen, SimilarScreen } from "../src/screens/Search";
import { problem, renderWith } from "./helpers";

vi.mock("../src/native", () => ({
  revealInExplorer: vi.fn(),
  getAppPaths: vi.fn(async () => null),
  inTauri: () => false,
}));

const hit = (id: number, score: number) => ({
  id, uid: `u${id}`, kind: "image", status: "ready", mime_type: "image/jpeg", date: "2026-03-10T10:00:00",
  taken_at: null, indexed_at: "2026-03-11T10:00:00", byte_size: 1000, width: 640, height: 480, duration_ms: null,
  title: null, is_favorite: false, is_reviewed: false, is_hidden: false, thumb_path: `thumbs/${id}.webp`,
  availability: "present", file_name: `IMG_${id}.jpg`, score,
});
const aiStatus = (installed = true) => ({
  installed, enabled: installed, max_tags: 8, download: { state: "idle", done_bytes: 0, total_bytes: 1, error: null },
  photos_done: 0, photos_waiting: 0, photos_failed: 0, photos_with_ai_tags: 0,
});
const detail = (id: number) => ({
  ...hit(id, 0), title: { value: null, origin: null }, taken_at: { value: null, origin: null }, sha256: "ab".repeat(32),
  file_modified_at: null, updated_at: "2026-03-11T10:00:00", error: null, metadata: null, tags: [], collections: [],
  notes: [], locations: [], derivatives: [], versions: [], jobs: [],
});

function Where() {
  const l = useLocation();
  return <output aria-label="location">{l.pathname + l.search}</output>;
}

describe("Search by description", () => {
  it("Ctrl+K focuses the box and Enter opens the results", () => {
    renderWith(
      <>
        <SearchBox />
        <Where />
      </>,
      {},
    );
    const box = screen.getByRole("searchbox", { name: "Search photos by description" });
    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(document.activeElement).toBe(box);
    fireEvent.change(box, { target: { value: " dog on a beach " } });
    fireEvent.submit(box.closest("form")!);
    expect(screen.getByLabelText("location").textContent).toBe("/search?q=dog%20on%20a%20beach");
  });

  it("shows the best matches in order and opens one", async () => {
    const { calls } = renderWith(<SearchScreen />, {
      "GET /v1/ai": () => aiStatus(),
      "POST /v1/search": () => ({ items: [hit(3, 0.31), hit(1, 0.27)], photos_analysed: 40 }),
      "GET /v1/assets/3": () => detail(3),
      "POST /v1/assets/3/preview": () => problem(422, "decode_failed", "x"),
      "GET /v1/tags": () => [],
      "GET /v1/collections": () => [],
    }, "/search?q=dog");
    const grid = await screen.findByRole("list", { name: "Search results" });
    const cells = within(grid).getAllByRole("button");
    expect(cells.map((c) => c.getAttribute("aria-label"))).toEqual(["IMG_3.jpg, match 1", "IMG_1.jpg, match 2"]);
    expect(screen.getByText(/Searched 40 analysed photos/)).toBeTruthy();
    const body = await calls.find((r) => r.url.endsWith("/v1/search"))!.json();
    expect(body).toEqual({ text: "dog", limit: 120 });
    fireEvent.click(cells[0]!);
    expect(await screen.findByRole("complementary", { name: "Photo details" })).toBeTruthy();
  });

  it("explains that the models are needed first", async () => {
    renderWith(<SearchScreen />, {
      "GET /v1/ai": () => aiStatus(false),
      "POST /v1/search": () => problem(409, "conflict", "download the AI models first"),
    }, "/search?q=dog");
    expect(await screen.findByText("Search by description needs the AI models")).toBeTruthy();
  });
});

describe("More like this", () => {
  it("lists similar photos", async () => {
    renderWith(
      <Routes>
        <Route path="/similar/:id" element={<SimilarScreen />} />
      </Routes>,
      {
        "GET /v1/assets/5/similar": () => ({ items: [hit(6, 0.9)], photos_analysed: 9 }),
      },
      "/similar/5",
    );
    expect(await screen.findByRole("button", { name: "IMG_6.jpg, match 1" })).toBeTruthy();
  });

  it("waits for analysis", async () => {
    renderWith(
      <Routes>
        <Route path="/similar/:id" element={<SimilarScreen />} />
      </Routes>,
      { "GET /v1/assets/7/similar": () => problem(409, "conflict", "this photo has not been analysed yet") },
      "/similar/7",
    );
    await waitFor(() => expect(screen.getByText("This photo has not been analysed yet")).toBeTruthy());
  });
});

describe("Teach a tag", () => {
  it("tags the best N matches using the Nth match as the bar", async () => {
    const items = [hit(3, 0.31), hit(1, 0.27), hit(7, 0.22)];
    const { calls } = renderWith(<SearchScreen />, {
      "GET /v1/ai": () => aiStatus(),
      "POST /v1/search": () => ({ items, photos_analysed: 40 }),
      "POST /v1/ai/teach": () => ({ tag_id: 9, name: "dogs", photos_tagged: 2 }),
    }, "/search?q=dog");
    fireEvent.click(await screen.findByRole("button", { name: "Teach a tag from this search…" }));
    const form = screen.getByRole("form", { name: "Teach a tag" });
    expect((within(form).getByLabelText("Tag name") as HTMLInputElement).value).toBe("dog");
    fireEvent.change(within(form).getByLabelText("Tag name"), { target: { value: " dogs " } });
    fireEvent.change(within(form).getByLabelText("How many of the best matches"), { target: { value: "2" } });
    const grid = screen.getByRole("list", { name: "Search results" });
    expect(within(grid).getAllByRole("button").map((b) => b.classList.contains("dimmed"))).toEqual([false, false, true]);
    fireEvent.click(within(form).getByRole("button", { name: "Teach" }));
    expect(await within(form).findByText(/Tagged 2 photos “dogs”/)).toBeTruthy();
    const body = await calls.find((r) => r.url.endsWith("/v1/ai/teach"))!.json();
    expect(body).toEqual({ name: "dogs", prompt: "dog", threshold: 0.27 });
  });

  it("Cancel closes the form and un-dims the results", async () => {
    renderWith(<SearchScreen />, {
      "GET /v1/ai": () => aiStatus(),
      "POST /v1/search": () => ({ items: [hit(3, 0.31), hit(1, 0.27)], photos_analysed: 40 }),
    }, "/search?q=dog");
    fireEvent.click(await screen.findByRole("button", { name: "Teach a tag from this search…" }));
    fireEvent.change(screen.getByLabelText("How many of the best matches"), { target: { value: "1" } });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("form", { name: "Teach a tag" })).toBeNull();
    const grid = screen.getByRole("list", { name: "Search results" });
    expect(within(grid).getAllByRole("button").some((b) => b.classList.contains("dimmed"))).toBe(false);
  });

  it("is not offered on More like this", async () => {
    renderWith(
      <Routes>
        <Route path="/similar/:id" element={<SimilarScreen />} />
      </Routes>,
      {
        "GET /v1/ai": () => aiStatus(),
        "GET /v1/assets/5/similar": () => ({ items: [hit(3, 0.9)], photos_analysed: 40 }),
      },
      "/similar/5",
    );
    await screen.findByRole("list", { name: "Search results" });
    expect(screen.queryByRole("button", { name: "Teach a tag from this search…" })).toBeNull();
  });
});
