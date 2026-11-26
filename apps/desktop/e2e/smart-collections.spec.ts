import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { type Engine, makePhotos, startEngine } from "./engine";

// S1: a smart collection saved from the gallery filters, against a real engine. It updates itself
// when another photo starts to match, and its rule can be edited from All photos.
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

test("smart collection: save the filters, new matches appear, edit the rule", async ({ page, request }) => {
  const photos = makePhotos(5);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept(photos));
  const api = (path: string) => `http://127.0.0.1:${engine.port}${path}`;
  const headers = { Authorization: `Bearer ${engine.token}` };
  const favourite = (id: number) => request.patch(api(`/v1/assets/${id}`), { headers, data: { is_favorite: true } });

  await page.goto(`/?port=${engine.port}&token=${engine.token}#/sources`);
  await page.getByRole("button", { name: "+ Add folder" }).click();
  await expect
    .poll(async () => ((await (await request.get(api("/v1/assets?limit=10"), { headers })).json()).items as unknown[]).length, {
      timeout: 30_000,
    })
    .toBe(5);
  const ids = ((await (await request.get(api("/v1/assets?limit=10"), { headers })).json()).items as { id: number }[]).map((a) => a.id);
  expect((await favourite(ids[0]!)).ok()).toBe(true);

  // All photos → Filters → Favourite: Yes → save it under a name.
  await page.getByRole("link", { name: "All photos" }).click();
  await page.getByRole("button", { name: /^Filters/ }).click();
  const panel = page.getByRole("complementary", { name: "Filters" });
  await panel.getByRole("radiogroup", { name: "Favourite" }).getByRole("radio", { name: /Yes/ }).click();
  await panel.getByRole("button", { name: "Save as smart collection…" }).click();
  await panel.getByRole("textbox", { name: "Smart collection name" }).fill("Best");
  await panel.getByRole("button", { name: "Save", exact: true }).click();

  // It opens, shows its rule, and has the one favourite.
  await expect(page.getByRole("heading", { name: /Best · 1/ })).toBeVisible();
  const rule = page.getByRole("group", { name: "Smart collection rule" });
  await expect(rule).toContainText("Smart collection");
  const grid = page.getByRole("grid", { name: "Photos" });
  await expect(grid.getByRole("gridcell")).toHaveCount(1);
  const nav = page.getByRole("region", { name: "Collections" });
  await expect(nav.getByRole("link", { name: /Best\s*1/ })).toBeVisible();
  await shot(page, "s1-smart-collection");

  // A second favourite appears by itself.
  expect((await favourite(ids[1]!)).ok()).toBe(true);
  await expect(page.getByRole("heading", { name: /Best · 2/ })).toBeVisible();
  await expect(grid.getByRole("gridcell")).toHaveCount(2);

  // Edit the rule in All photos: Favourite: No, then update it.
  await rule.getByRole("button", { name: "Edit rule" }).click();
  await expect(page).toHaveURL(/#\/photos/);
  await panel.getByRole("radiogroup", { name: "Favourite" }).getByRole("radio", { name: /No/ }).click();
  await panel.getByRole("button", { name: "Update “Best”" }).click();
  await expect(page.getByRole("heading", { name: /Best · 3/ })).toBeVisible();
  await expect(grid.getByRole("gridcell")).toHaveCount(3);

  // Nothing was copied or moved: still five photos in the library.
  expect((await (await request.get(api("/v1/assets:months"), { headers })).json()).total).toBe(5);
  expect(errors).toEqual([]);
});
