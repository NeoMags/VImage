import { existsSync, mkdirSync, mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

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

test("backup: back up, change a tag, restore it, export", async ({ page, request }) => {
  const photos = makePhotos(3);
  const out = mkdtempSync(join(tmpdir(), "iv-e2e-export-"));
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const api = (path: string) => `http://127.0.0.1:${engine.port}${path}`;
  const headers = { Authorization: `Bearer ${engine.token}` };
  // The browser has no Windows dialogs: the folder and save pickers fall back to prompt().
  let answer = photos;
  page.on("dialog", (d) => void d.accept(answer));

  await page.goto(`/?port=${engine.port}&token=${engine.token}#/sources`);
  await page.getByRole("button", { name: "+ Add folder" }).click();
  await expect(page.getByRole("row", { name: /iv-e2e-photos/ })).toContainText("3", { timeout: 30_000 });
  const first = (await (await request.get(api("/v1/assets?limit=1"), { headers })).json()).items[0].id as number;
  await request.post(api(`/v1/assets/${first}/tags`), { headers, data: { names: ["kept"] } });

  await page.getByRole("link", { name: "Settings" }).click();
  await page.getByRole("button", { name: "Back up now" }).click();
  await expect(page.getByText(/Backup made/)).toBeVisible();

  // A change after the backup...
  const detail = async () => (await (await request.get(api(`/v1/assets/${first}`), { headers })).json()) as { tags: { id: number; name: string }[] };
  const kept = (await detail()).tags.find((t) => t.name === "kept")!;
  await request.delete(api(`/v1/assets/${first}/tags/${kept.id}`), { headers });
  expect((await detail()).tags).toEqual([]);

  // ...is undone by restoring it.
  const table = page.getByRole("table", { name: "Backups" });
  await table.getByRole("row", { name: /Made by you/ }).getByRole("button", { name: "Restore…" }).click();
  await page.getByRole("alertdialog", { name: "Confirm restore" }).getByRole("button", { name: "Restore" }).click();
  await expect(page.getByText(/Restored from library-manual-/)).toBeVisible({ timeout: 30_000 });
  expect((await detail()).tags.map((t) => t.name)).toEqual(["kept"]);
  await expect(table.getByRole("row", { name: /Before a restore/ })).toBeVisible();
  await shot(page, "11-settings-backup");

  answer = join(out, "library.json");
  await page.getByRole("button", { name: "Export…" }).click();
  await expect(page.getByText(/Exported 3 photos/)).toBeVisible();
  expect(existsSync(answer)).toBe(true);
  const data = JSON.parse(readFileSync(answer, "utf8")) as { items: { tags: { name: string }[] }[] };
  expect(data.items.flatMap((i) => i.tags.map((t) => t.name))).toEqual(["kept"]);
  expect(errors).toEqual([]);
});
