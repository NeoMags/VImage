import { mkdirSync } from "node:fs";
import { resolve } from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { type Engine, makeDatedPhotos, startEngine } from "./engine";

let engine: Engine;
test.describe.configure({ mode: "serial" }); // the detail test uses the photos the gallery test added
test.beforeAll(async () => {
  engine = await startEngine();
});
test.afterAll(async () => {
  await engine?.stop();
});

const SHOTS = process.env.IV_E2E_SHOTS;
async function shot(page: Page, name: string) {
  if (!SHOTS) return;
  mkdirSync(SHOTS, { recursive: true });
  await page.screenshot({ path: resolve(SHOTS, `${name}.png`) });
}

test("gallery: months, real thumbnails, select, tag, hide and unhide", async ({ page }) => {
  const photos = makeDatedPhotos(8, 3); // 3 months: 3 + 3 + 2 photos
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept(photos));

  await page.goto(`/?port=${engine.port}&token=${engine.token}#/sources`);
  await page.getByRole("button", { name: "+ Add folder" }).click();
  await expect(page.getByRole("row", { name: /iv-e2e-photos/ })).toContainText("8", { timeout: 30_000 });

  await page.getByRole("link", { name: "All photos" }).click();
  await expect(page.getByRole("heading", { name: /All photos · 8/ })).toBeVisible();
  const grid = page.getByRole("grid", { name: "Photos" });
  await expect(grid.getByRole("heading", { level: 2 })).toHaveCount(3);
  // thumbnails are produced in the background and appear without a reload
  const imgs = grid.locator("img");
  await expect(imgs).toHaveCount(8, { timeout: 60_000 });
  await expect
    .poll(() => imgs.evaluateAll((els) => els.filter((e) => (e as HTMLImageElement).naturalWidth > 0).length), { timeout: 30_000 })
    .toBe(8);
  await shot(page, "6-gallery-timeline");

  const cells = grid.getByRole("gridcell");
  await cells.nth(0).click();
  await cells.nth(2).click({ modifiers: ["Shift"] });
  const bar = page.getByRole("toolbar", { name: "Selected photos" });
  await expect(bar).toContainText("3 photos selected");
  await bar.getByLabel("Tag name").fill("facade");
  await bar.getByRole("button", { name: "Add tag" }).click();
  await expect(page.getByText("Tagged “facade”: 3 photos.")).toBeVisible();
  await shot(page, "7-gallery-selected");

  await bar.getByRole("button", { name: "Hide" }).click();
  await expect(page.getByRole("heading", { name: /All photos · 5/ })).toBeVisible();
  await expect(grid.getByRole("gridcell")).toHaveCount(5);

  await page.getByLabel("Show hidden").check();
  await expect(page.getByRole("heading", { name: /Hidden photos · 3/ })).toBeVisible();
  await grid.getByRole("gridcell").first().click();
  await page.keyboard.press("Control+a");
  await page.getByRole("toolbar", { name: "Selected photos" }).getByRole("button", { name: "Unhide" }).click();
  await expect(page.getByRole("heading", { name: "No hidden photos" })).toBeVisible();

  await page.getByLabel("Show hidden").uncheck();
  await page.getByRole("button", { name: "List" }).click();
  await expect(page.getByRole("grid", { name: "Photos" }).getByRole("gridcell")).toHaveCount(8);
  await page.getByRole("grid", { name: "Photos" }).focus();
  await page.keyboard.press("f"); // favourite the focused photo
  await expect(page.getByText("Added to favourites: 1 photo.")).toBeVisible();
  await expect(page.getByRole("grid", { name: "Photos" }).getByTitle("Favourite")).toHaveCount(1);
  await shot(page, "8-gallery-list");

  expect(errors).toEqual([]);
});

test("detail: sharper preview, tag, note, step and close", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(`/?port=${engine.port}&token=${engine.token}#/photos`);
  const grid = page.getByRole("grid", { name: "Photos" });
  const first = grid.getByRole("gridcell").first();
  const name = await first.getAttribute("aria-label");
  await first.click();
  await page.keyboard.press("Enter");

  const panel = page.getByRole("complementary", { name: "Photo details" });
  await expect(panel).toBeVisible();
  const img = panel.locator(".preview-frame img");
  await expect(img).toHaveAttribute("src", /_1600\.webp/, { timeout: 30_000 });
  await expect.poll(() => img.evaluate((e) => (e as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  await expect(panel.getByText("Canon")).toHaveCount(0); // synthetic photos carry no camera
  await expect(panel.getByRole("heading", { name: "Locations" })).toBeVisible();

  await panel.getByLabel("Add a tag").fill("porch");
  await panel.getByLabel("Add a tag").press("Enter");
  await expect(panel.getByRole("button", { name: "Remove tag porch" })).toBeVisible();
  await panel.getByLabel("New note").fill("Check the railing height");
  await panel.getByRole("button", { name: "Add note" }).click();
  await expect(panel.getByText("Check the railing height")).toBeVisible();
  await shot(page, "9-detail-panel");

  await panel.getByRole("button", { name: "Full window" }).click();
  await expect(panel).toHaveClass(/full/);
  await shot(page, "10-detail-full");
  await panel.getByRole("button", { name: "Next photo" }).click();
  await expect(panel.getByText("Check the railing height")).toHaveCount(0);
  await page.keyboard.press("ArrowLeft");
  await expect(panel.getByText("Check the railing height")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(panel).toHaveCount(0);

  // Saved in the engine, not just on screen.
  await page.reload();
  await grid.getByRole("gridcell", { name: name! }).dblclick();
  await expect(page.getByRole("complementary", { name: "Photo details" }).getByText("Check the railing height")).toBeVisible();
  expect(errors).toEqual([]);
});
