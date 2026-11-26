import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

import { expect, type Page, test } from "@playwright/test";

import { type Engine, startEngine } from "./engine";

// D2: the Duplicates screen against a real engine. A photo, a smaller PNG copy of it and an
// unrelated photo: the copy is found by the thumbnail stage, resolved here, and only hidden.
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

function makeCopies(): string {
  const dir = mkdtempSync(join(tmpdir(), "iv-e2e-dups-"));
  const script = `
import sys
import numpy as np
from PIL import Image, ImageFilter
def texture(seed):
    rng = np.random.default_rng(seed)
    small = rng.integers(0, 256, size=(6, 8, 3), dtype=np.uint8)
    return Image.fromarray(small).resize((640, 480), Image.BICUBIC).filter(ImageFilter.GaussianBlur(1))
a = texture(1)
a.save(f"{sys.argv[1]}/beach.jpg", quality=92)
a.resize((320, 240)).save(f"{sys.argv[1]}/beach small.png")
texture(2).save(f"{sys.argv[1]}/forest.jpg", quality=92)
`;
  const out = spawnSync("uv", ["run", "--quiet", "python", "-c", script, dir], {
    cwd: resolve(import.meta.dirname, "../../../services/sidecar"),
    encoding: "utf8",
  });
  if (out.status !== 0) throw new Error(out.stderr);
  return dir;
}

test("Duplicates: a copy is grouped, the suggested photo is kept and the copy is only hidden", async ({ page, request }) => {
  const photos = makeCopies();
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("dialog", (d) => void d.accept(photos));
  const api = (path: string) => `http://127.0.0.1:${engine.port}${path}`;
  const headers = { Authorization: `Bearer ${engine.token}` };

  await page.goto(`/?port=${engine.port}&token=${engine.token}#/sources`);
  await page.getByRole("button", { name: "+ Add folder" }).click();
  await expect
    .poll(async () => (await (await request.get(api("/v1/duplicates:summary"), { headers })).json()).copies, { timeout: 30_000 })
    .toBe(1);

  const nav = page.getByRole("navigation", { name: "Main" });
  await expect(nav.getByRole("link", { name: /Duplicates\s*1/ })).toBeVisible();
  await nav.getByRole("link", { name: /Duplicates/ }).click();
  await expect(page.getByRole("heading", { name: "Duplicates" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Copies (1)" })).toHaveAttribute("aria-pressed", "true");

  const group = page.getByRole("list", { name: "Group of 2 photos" });
  const members = group.getByRole("listitem");
  await expect(members).toHaveCount(2);
  // the bigger original is suggested and kept; the small copy will be hidden
  await expect(members.first()).toContainText("Suggested");
  await expect(members.first()).toContainText("640 × 480");
  await expect(group.getByRole("button", { name: /beach\.jpg, kept/ })).toHaveAttribute("aria-pressed", "true");
  await expect(group.getByRole("button", { name: /beach small\.png, will be hidden/ })).toHaveAttribute("aria-pressed", "false");
  await shot(page, "d2-duplicates");

  await page.getByRole("button", { name: "Keep selected, hide 1 other" }).click();
  await expect(page.getByText("No copies found")).toBeVisible();
  await expect(nav.getByRole("link", { name: /Duplicates\s*1/ })).toHaveCount(0);

  // nothing was deleted: the copy is hidden, two photos stay visible
  const count = async (hidden: boolean) =>
    (await (await request.get(api(`/v1/assets:months?hidden=${hidden}`), { headers })).json()).total as number;
  expect(await count(false)).toBe(2);
  expect(await count(true)).toBe(1);

  await page.getByRole("button", { name: "Same file in several folders (0)" }).click();
  await expect(page.getByText("No file is in more than one place")).toBeVisible();
  expect(errors).toEqual([]);
});
