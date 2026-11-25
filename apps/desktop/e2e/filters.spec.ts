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

test("filters: counts match the real engine and narrow the gallery", async ({ page, request }) => {
  const photos = makePhotos(6);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept(photos));
  const api = (path: string) => `http://127.0.0.1:${engine.port}${path}`;
  const headers = { Authorization: `Bearer ${engine.token}` };

  await page.goto(`/?port=${engine.port}&token=${engine.token}#/sources`);
  await page.getByRole("button", { name: "+ Add folder" }).click();
  await expect(page.getByRole("row", { name: /iv-e2e-photos/ })).toContainText("6", { timeout: 30_000 });
  const list = async () =>
    ((await (await request.get(api("/v1/assets?limit=10"), { headers })).json()).items as { id: number }[]).map((a) => a.id);
  await expect.poll(async () => (await list()).length, { timeout: 30_000 }).toBe(6); // files found → photos indexed
  const ids = await list();
  await request.post(api("/v1/assets:bulk"), { headers, data: { ids: ids.slice(0, 3), add_tags: ["Beach"] } });
  await request.post(api("/v1/assets:bulk"), { headers, data: { ids: ids.slice(0, 1), set_favorite: true } });

  await page.getByRole("link", { name: "All photos" }).click();
  await expect(page.getByRole("heading", { name: /All photos · 6/ })).toBeVisible();
  await page.getByRole("button", { name: "Filters" }).click();
  const panel = page.getByRole("complementary", { name: "Filters" });
  await expect(panel.getByText("6 match")).toBeVisible();
  await expect(panel.getByRole("checkbox", { name: /JPEG\s*6/ })).toBeVisible();

  await panel.getByRole("checkbox", { name: /Beach\s*3/ }).check();
  await expect(page.getByRole("heading", { name: /All photos · 3/ })).toBeVisible();
  // Favourite counts are within the 3 beach photos; ticking "Yes" gives exactly that number.
  await expect(panel.getByRole("radio", { name: /Yes\s*1/ }).first()).toBeVisible();
  await panel.getByRole("radiogroup", { name: "Favourite" }).getByRole("radio", { name: /Yes/ }).click();
  await expect(page.getByRole("heading", { name: /All photos · 1/ })).toBeVisible();
  await expect(page.getByLabel("Active filters")).toContainText("#Beach");
  await expect(page.getByLabel("Active filters")).toContainText("Favourites");
  await panel.locator(".filter-body").evaluate((el) => (el.scrollTop = 0));
  await shot(page, "12-filters");

  await page.getByLabel("Active filters").getByRole("button", { name: "Clear all" }).click();
  await expect(page.getByRole("heading", { name: /All photos · 6/ })).toBeVisible();
  expect(errors).toEqual([]);
});
