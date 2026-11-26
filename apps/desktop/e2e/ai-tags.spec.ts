import { spawnSync } from "node:child_process";
import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { type Engine, makePhotos, startEngine } from "./engine";

// T3: AI tags in the UI. The models are not needed here: the test writes AI tags the way the
// classify stage does (handlers.apply_ai_tags), then checks the sidebar, filters and details.
let engine: Engine;
test.beforeAll(async () => {
  engine = await startEngine();
});
test.afterAll(async () => {
  await engine?.stop();
});

async function shot(page: Page, name: string) {
  const dir = process.env.IV_E2E_SHOTS;
  if (!dir) return;
  mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: resolve(dir, `${name}.png`), fullPage: true });
}

function seedAiTags(library: string, tags: Record<number, [string, number][]>) {
  const script = `
import json, sys
from pathlib import Path
from ivsidecar import db, handlers, library
conn = db.open_library(Path(sys.argv[1]))
with library.tx(conn):
    for aid, tags in json.loads(sys.argv[2]).items():
        handlers.apply_ai_tags(conn, int(aid), tags)
conn.close()
`;
  const out = spawnSync("uv", ["run", "--quiet", "python", "-c", script, library, JSON.stringify(tags)], {
    cwd: resolve(import.meta.dirname, "../../../services/sidecar"),
    encoding: "utf8",
  });
  if (out.status !== 0) throw new Error(out.stderr);
}

test("AI tags: sorted-by-AI groups, their own filter section, accept in details", async ({ page, request }) => {
  const photos = makePhotos(4);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept(photos));
  const api = (path: string) => `http://127.0.0.1:${engine.port}${path}`;
  const headers = { Authorization: `Bearer ${engine.token}` };

  await page.goto(`/?port=${engine.port}&token=${engine.token}#/sources`);
  await page.getByRole("button", { name: "+ Add folder" }).click();
  const list = async () =>
    ((await (await request.get(api("/v1/assets?limit=10&sort=indexed&order=asc"), { headers })).json()).items as { id: number }[]).map(
      (a) => a.id,
    );
  await expect.poll(async () => (await list()).length, { timeout: 30_000 }).toBe(4);
  const ids = await list();
  seedAiTags(engine.library, {
    [ids[0]!]: [["dog", 0.9], ["grass", 0.6]],
    [ids[1]!]: [["dog", 0.85], ["grass", 0.55]],
    [ids[2]!]: [["dog", 0.8]],
    [ids[3]!]: [["cat", 0.7]],
  });

  await page.reload();
  const groups = page.getByRole("region", { name: "Sorted by AI" });
  await expect(groups.getByRole("link", { name: /dog\s*3/ })).toBeVisible();
  await expect(groups.getByRole("link", { name: /grass\s*2/ })).toBeVisible();
  await expect(groups.getByRole("link", { name: /cat\s*1/ })).toBeVisible();

  // A group is the tag filter on All photos; the filter panel's AI section shows the same count.
  await groups.getByRole("link", { name: /dog\s*3/ }).click();
  await expect(page.getByRole("heading", { name: /All photos · 3/ })).toBeVisible();
  await page.getByRole("button", { name: /Filters/ }).click();
  const panel = page.getByRole("complementary", { name: "Filters" });
  const aiList = panel.getByRole("list", { name: "AI tags" });
  await expect(aiList.getByRole("checkbox", { name: /dog\s*3/ })).toBeChecked();
  await expect(panel.getByRole("list", { name: "Tags", exact: true })).toHaveCount(0); // no tags of yours yet
  await shot(page, "t3-ai-groups");

  // Accepting "dog" on one photo makes it one of your tags everywhere.
  const grid = page.getByRole("grid", { name: "Photos" });
  await grid.getByRole("gridcell").first().dblclick();
  const details = page.getByRole("complementary", { name: "Photo details" });
  const suggested = details.getByLabel("Suggested by AI");
  await suggested.getByRole("button", { name: "Accept tag dog" }).click();
  await expect(details.getByLabel("Tags", { exact: true })).toContainText("dog");
  await expect(panel.getByRole("list", { name: "Tags", exact: true }).getByRole("checkbox", { name: /dog\s*3/ })).toBeVisible();
  await expect(aiList.getByRole("checkbox", { name: /dog/ })).toHaveCount(0);
  await shot(page, "t3-accepted");
  expect(errors).toEqual([]);
});

function seedVectors(library: string, colours: Record<number, [number, number, number]>) {
  const script = `
import json, sys
from pathlib import Path
from ivsidecar import db, handlers, library
conn = db.open_library(Path(sys.argv[1]))
with library.tx(conn):
    for aid, (r, g, b) in json.loads(sys.argv[2]).items():
        v = [r, g, b] + [1.0] * 509
        n = sum(x * x for x in v) ** 0.5
        handlers._apply_embedding(conn, int(aid), [x / n for x in v])
conn.close()
`;
  const out = spawnSync("uv", ["run", "--quiet", "python", "-c", script, library, JSON.stringify(colours)], {
    cwd: resolve(import.meta.dirname, "../../../services/sidecar"),
    encoding: "utf8",
  });
  if (out.status !== 0) throw new Error(out.stderr);
}

test("More like this, and search explains it needs the models", async ({ page, request }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const api = (path: string) => `http://127.0.0.1:${engine.port}${path}`;
  const headers = { Authorization: `Bearer ${engine.token}` };
  const ids = ((await (await request.get(api("/v1/assets?limit=10&sort=indexed&order=asc"), { headers })).json()).items as { id: number }[]).map(
    (a) => a.id,
  );
  expect(ids.length).toBe(4); // the photos from the test above
  seedVectors(engine.library, { [ids[0]!]: [200, 10, 10], [ids[1]!]: [190, 20, 20], [ids[2]!]: [10, 10, 200], [ids[3]!]: [20, 200, 20] });

  // From a photo's details: "More like this" lists the others, nearest first, never the photo itself.
  await page.goto(`/?port=${engine.port}&token=${engine.token}#/photos`);
  const grid = page.getByRole("grid", { name: "Photos" });
  await grid.getByRole("gridcell").first().dblclick();
  await page.getByRole("complementary", { name: "Photo details" }).getByRole("button", { name: "More like this" }).click();
  await expect(page).toHaveURL(/#\/similar\/\d+$/);
  const shown = Number(page.url().split("/").pop());
  const results = page.getByRole("list", { name: "Search results" });
  await expect(results.getByRole("button")).toHaveCount(3);
  // The screen shows the engine's order (nearest first); the photo itself is never listed.
  const api_sim = (await (await request.get(api(`/v1/assets/${shown}/similar`), { headers })).json()).items as { id: number; file_name: string }[];
  expect(api_sim.map((h) => h.id)).not.toContain(shown);
  await expect(results.getByRole("button")).toHaveText(["", "", ""]);
  expect(await results.getByRole("button").evaluateAll((els) => els.map((e) => e.getAttribute("aria-label")))).toEqual(
    api_sim.map((h, i) => `${h.file_name}, match ${i + 1}`),
  );
  // The two reddish photos are each other's nearest match.
  if (shown === ids[0] || shown === ids[1]) expect(api_sim[0]!.id).toBe(shown === ids[0] ? ids[1] : ids[0]);
  await shot(page, "t4-similar");

  // Search by description: the models are not downloaded in this test run.
  await page.keyboard.press("Control+k");
  await page.keyboard.type("red things");
  await page.keyboard.press("Enter");
  await expect(page.getByText("Search by description needs the AI models")).toBeVisible();
  expect(errors).toEqual([]);
});
