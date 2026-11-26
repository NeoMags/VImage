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
