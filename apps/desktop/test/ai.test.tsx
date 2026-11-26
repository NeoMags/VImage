import { fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { AiGroupsNav } from "../src/ai/AiGroupsNav";
import { setFilters, useFilters } from "../src/gallery/filters";
import { AiPanel } from "../src/screens/AiPanel";
import { renderWith } from "./helpers";

const status = (over: Record<string, unknown> = {}) => ({
  installed: false,
  enabled: false,
  max_tags: 8,
  download: { state: "idle", done_bytes: 0, total_bytes: 886_302_788, error: null },
  photos_done: 0,
  photos_waiting: 0,
  photos_failed: 0,
  photos_with_ai_tags: 0,
  ...over,
});

describe("Automatic tags panel", () => {
  it("offers the one-time download, then shows progress", async () => {
    let downloading = false;
    const { calls } = renderWith(<AiPanel />, {
      "GET /v1/ai": () =>
        downloading ? status({ download: { state: "downloading", done_bytes: 443_000_000, total_bytes: 886_302_788, error: null } }) : status(),
      "POST /v1/ai:install": () => {
        downloading = true;
        return status({ download: { state: "downloading", done_bytes: 0, total_bytes: 886_302_788, error: null } });
      },
    });
    fireEvent.click(await screen.findByRole("button", { name: "Download AI models (886 MB)" }));
    expect(await screen.findByLabelText("AI model download")).toBeTruthy();
    const progress = screen.getByRole("status");
    await expect.poll(() => progress.textContent, { timeout: 5000 }).toMatch(/49% · 443 MB of\s*886 MB/);
    expect(calls.some((r) => r.url.endsWith("/v1/ai:install") && r.method === "POST")).toBe(true);
  });

  it("shows a failed download with Retry", async () => {
    renderWith(<AiPanel />, {
      "GET /v1/ai": () => status({ download: { state: "failed", done_bytes: 10, total_bytes: 886_302_788, error: "connection reset" } }),
    });
    expect(await screen.findByRole("button", { name: "Retry download" })).toBeTruthy();
    expect(screen.getByText("Download stopped: connection reset")).toBeTruthy();
  });

  it("switches tagging on and changes the number of tags", async () => {
    const saved: unknown[] = [];
    const { calls } = renderWith(<AiPanel />, {
      "GET /v1/ai": () => status({ installed: true, photos_done: 1200, photos_waiting: 30 }),
      "PUT /v1/settings/ai": () => ({ enabled: true, max_tags: 8 }),
    });
    expect(await screen.findByText("1,200 photos tagged · 30 waiting")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Tag my photos automatically" }));
    fireEvent.change(screen.getByRole("combobox", { name: "Tags per photo" }), { target: { value: "5" } });
    await screen.findByText("1,200 photos tagged · 30 waiting");
    for (const r of calls.filter((c) => c.url.endsWith("/v1/settings/ai"))) saved.push(await r.json());
    expect(saved).toContainEqual({ enabled: true, max_tags: 8 });
    expect(saved).toContainEqual({ enabled: false, max_tags: 5 });
  });
});

function FilterProbe() {
  return <output aria-label="filters">{JSON.stringify(useFilters())}</output>;
}

describe("Sorted by AI", () => {
  afterEach(() => setFilters({}));

  it("lists AI groups with counts and opens one as a tag filter", async () => {
    const groups = Array.from({ length: 12 }, (_, i) => ({ id: i + 1, name: `tag${i + 1}`, count: 100 - i }));
    renderWith(
      <>
        <AiGroupsNav />
        <FilterProbe />
      </>,
      { "GET /v1/ai/groups": () => groups },
    );
    const region = await screen.findByRole("region", { name: "Sorted by AI" });
    expect(await screen.findByRole("link", { name: /tag1\s*100/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: /tag12/ })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show all 12" }));
    expect(screen.getByRole("link", { name: /tag12\s*89/ })).toBeTruthy();
    fireEvent.click(screen.getByRole("link", { name: /tag3\s*98/ }));
    expect(screen.getByLabelText("filters").textContent).toBe('{"tag":[3]}');
    expect(region).toBeTruthy();
  });

  it("stays out of the sidebar until something is tagged", async () => {
    const { calls } = renderWith(
      <AiGroupsNav />,
      { "GET /v1/ai/groups": () => [] },
    );
    await waitFor(() => expect(calls.some((r) => r.url.endsWith("/v1/ai/groups"))).toBe(true));
    expect(screen.queryByRole("region", { name: "Sorted by AI" })).toBeNull();
  });
});
