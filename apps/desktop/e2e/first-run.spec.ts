import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { type Engine, makePhotos, startEngine } from "./engine";

let engine: Engine;
test.beforeAll(async () => {
  engine = await startEngine();
});
test.afterAll(async () => {
  await engine?.stop();
});

const SHOTS = process.env.IV_E2E_SHOTS; // optional folder for screenshots
async function shot(page: import("@playwright/test").Page, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: resolve(SHOTS, `${name}.png`) });
}

test("first run: add a folder, counters rise live, folder shows Live", async ({ page }) => {
  const photos = makePhotos(6);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept(photos)); // the browser stand-in for the Windows folder picker

  await page.goto(`/?port=${engine.port}&token=${engine.token}#/dashboard`);
  await expect(page.getByRole("heading", { name: "Add your first folder" })).toBeVisible();
  await shot(page, "1-first-run");

  await page.getByRole("button", { name: "Add folder…" }).click();
  // No reload: the scan and processing reach the page through the live event stream.
  const photosStat = page.locator("section", { hasText: "Photos" }).first();
  await expect(photosStat).toContainText("6", { timeout: 30_000 });
  await expect(page.locator("section", { hasText: "Processing" }).first()).toContainText("all done", { timeout: 60_000 });
  await shot(page, "2-dashboard");

  await page.getByRole("link", { name: "Sources" }).click();
  const row = page.getByRole("row", { name: /iv-e2e-photos/ });
  await expect(row).toContainText("Live");
  await expect(row).toContainText("6");
  await shot(page, "3-sources");

  await page.getByRole("link", { name: /Processing/ }).click();
  await expect(page.locator("section", { hasText: "Done" }).first()).not.toContainText(/^Done0$/);
  await expect(page.getByRole("heading", { name: "No failures" })).toBeVisible();
  await shot(page, "4-processing");

  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByLabel("Dark").check();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByText("database v")).toBeVisible({ timeout: 30_000 });
  await shot(page, "5-settings-dark");

  expect(errors).toEqual([]);
});

test("removing the folder asks first and empties the library", async ({ page }) => {
  await page.goto(`/?port=${engine.port}&token=${engine.token}#/sources`);
  await page.getByRole("button", { name: "Remove" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("6 photos");
  await expect(dialog).toContainText("Your files on disk are not touched.");
  await dialog.getByRole("button", { name: "Remove folder" }).click();
  await expect(page.getByText(/6 photos left the library/)).toBeVisible();
  await page.getByRole("link", { name: "Dashboard" }).click();
  await expect(page.getByRole("heading", { name: "Add your first folder" })).toBeVisible();
});
