// Gallery performance on a synthetic 100k-asset library (B12). Runs only with IV_PERF=1.
// Chromium in CI stands in for WebView2: the numbers are indicative; the real check is B21 on Neo's PC.
import { writeFileSync } from "node:fs";
import { resolve } from "node:path";

import { expect, test } from "@playwright/test";

import { type Engine, makeSyntheticLibrary, startEngine } from "../engine";

const COUNT = Number(process.env.IV_PERF_COUNT ?? 100_000);
const HEAP_LIMIT_MB = 300; // plan target for the page's JS heap (whole-app RAM < 500 MB is checked in B21)

test.skip(!process.env.IV_PERF, "set IV_PERF=1 to run the performance check");
test.setTimeout(240_000);

let engine: Engine;
test.beforeAll(async () => {
  engine = await startEngine(makeSyntheticLibrary(COUNT));
});
test.afterAll(async () => {
  await engine?.stop();
});

test(`gallery stays fast and small with ${COUNT.toLocaleString("en")} photos`, async ({ page }) => {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send("Performance.enable");
  const heapMB = async () => {
    await cdp.send("HeapProfiler.collectGarbage");
    const { metrics } = await cdp.send("Performance.getMetrics");
    return Math.round((metrics.find((m) => m.name === "JSHeapUsedSize")!.value / 1e6) * 10) / 10;
  };
  const loadedThumbs = () =>
    page.locator(".gallery img").evaluateAll((els) => els.filter((e) => (e as HTMLImageElement).naturalWidth > 0).length);

  const t0 = Date.now();
  await page.goto(`/?port=${engine.port}&token=${engine.token}#/photos`);
  await expect(page.getByRole("heading", { name: new RegExp(`All photos · ${COUNT.toLocaleString("en")}`) })).toBeVisible();
  await expect.poll(loadedThumbs, { timeout: 30_000 }).toBeGreaterThan(10);
  const firstScreenMs = Date.now() - t0;

  // Fling from top to bottom, 2,000 px per frame, recording every frame's duration.
  const fling = await page.evaluate(async () => {
    const el = document.querySelector(".gallery") as HTMLElement;
    const frames: number[] = [];
    let last = performance.now();
    while (el.scrollTop + el.clientHeight < el.scrollHeight - 1) {
      el.scrollTop += 2000;
      await new Promise((r) => requestAnimationFrame(r));
      const now = performance.now();
      frames.push(now - last);
      last = now;
    }
    frames.sort((a, b) => a - b);
    const pct = (p: number) => Math.round(frames[Math.min(frames.length - 1, Math.floor(frames.length * p))]! * 10) / 10;
    return { frames: frames.length, height: el.scrollHeight, p50: pct(0.5), p95: pct(0.95), max: pct(1) };
  });
  await expect.poll(loadedThumbs, { timeout: 30_000 }).toBeGreaterThan(10); // the last month loads
  const heapAfterFling = await heapMB();

  // Jump to an old year with the scrubber: photos there show without scrolling through the rest.
  const years = page.getByRole("navigation", { name: "Jump to year" }).getByRole("button");
  const target = years.nth(Math.floor((await years.count()) / 2));
  const year = await target.textContent();
  const t1 = Date.now();
  await target.click();
  await expect(page.locator(".g-header h2").first()).toContainText(year!);
  await expect.poll(loadedThumbs, { timeout: 30_000 }).toBeGreaterThan(10);
  const jumpMs = Date.now() - t1;

  const result = { count: COUNT, firstScreenMs, scroll: fling, heapAfterFlingMB: heapAfterFling, jumpToYear: year, jumpMs };
  console.log(JSON.stringify(result, null, 2));
  if (process.env.IV_E2E_SHOTS) writeFileSync(resolve(process.env.IV_E2E_SHOTS, "perf.json"), JSON.stringify(result, null, 2));
  expect(heapAfterFling).toBeLessThan(HEAP_LIMIT_MB);
});
