import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { type Engine, makePhotos, startEngine } from "./engine";

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

test("collections: nest, add photos, open, remove one, delete keeps the inner one", async ({ page, request }) => {
  const photos = makePhotos(5);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept(photos));
  const api = (path: string) => `http://127.0.0.1:${engine.port}${path}`;
  const headers = { Authorization: `Bearer ${engine.token}` };

  await page.goto(`/?port=${engine.port}&token=${engine.token}#/sources`);
  await page.getByRole("button", { name: "+ Add folder" }).click();
  await expect(page.getByRole("row", { name: /iv-e2e-photos/ })).toContainText("5", { timeout: 30_000 });
  await expect
    .poll(async () => ((await (await request.get(api("/v1/assets?limit=10"), { headers })).json()).items as unknown[]).length, {
      timeout: 30_000,
    })
    .toBe(5);

  // A top-level collection from the sidebar, then one inside it from its page.
  const nav = page.getByRole("region", { name: "Collections" });
  await nav.getByRole("button", { name: "New collection" }).click();
  await nav.getByRole("textbox", { name: "New collection name" }).fill("Trips");
  await nav.getByRole("textbox", { name: "New collection name" }).press("Enter");
  await expect(page.getByRole("heading", { name: /^Trips/ })).toBeVisible();
  await page.getByRole("button", { name: "New sub-collection" }).click();
  await page.getByRole("textbox", { name: "New sub-collection name" }).fill("Italy");
  await page.getByRole("textbox", { name: "New sub-collection name" }).press("Enter");
  await expect(page.getByRole("heading", { name: /Trips › Italy/ })).toBeVisible();
  await expect(page.getByText("This collection is empty")).toBeVisible();

  // Add three photos with the picker.
  await page.getByRole("link", { name: "All photos" }).click();
  const grid = page.getByRole("grid", { name: "Photos" });
  const cells = grid.getByRole("gridcell");
  await expect(cells).toHaveCount(5);
  await cells.nth(0).click();
  await cells.nth(2).click({ modifiers: ["Shift"] });
  const bar = page.getByRole("toolbar", { name: "Selected photos" });
  await bar.getByRole("button", { name: "Add to collection…" }).click();
  await page.getByRole("searchbox", { name: "Find or name a collection" }).fill("ita");
  await page.getByRole("listbox", { name: "Collections" }).getByRole("button", { name: "Trips › Italy" }).click();
  await expect(page.getByText("Added to “Italy”: 3 photos.")).toBeVisible();

  // Open it from the sidebar: only its photos; remove one.
  await expect(nav.getByRole("link", { name: /Italy\s*3/ })).toBeVisible();
  await nav.getByRole("link", { name: /Italy/ }).click();
  await expect(page.getByRole("heading", { name: /Trips › Italy · 3/ })).toBeVisible();
  await expect(grid.getByRole("gridcell")).toHaveCount(3);
  await grid.getByRole("gridcell").nth(0).click();
  await shot(page, "13-collection");
  await bar.getByRole("button", { name: "Remove from collection" }).click();
  await expect(page.getByRole("heading", { name: /Trips › Italy · 2/ })).toBeVisible();

  // Deleting Trips keeps Italy (moved to the top level) and every photo.
  await nav.getByRole("link", { name: /Trips/ }).click();
  await page.getByRole("button", { name: "Delete…" }).click();
  const dialog = page.getByRole("dialog", { name: "Delete “Trips”?" });
  await expect(dialog).toContainText("1 sub-collection move up one level");
  await dialog.getByRole("button", { name: "Delete collection" }).click();
  await expect(page.getByRole("heading", { name: /All photos · 5/ })).toBeVisible();
  await expect(nav.getByRole("link", { name: /Trips/ })).toHaveCount(0);
  await expect(nav.getByRole("link", { name: /Italy\s*2/ })).toBeVisible();
  expect(errors).toEqual([]);
});
