// Starts a real engine (python -m ivsidecar) on a fresh library for one test file.
import { type ChildProcess, spawn, spawnSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { createInterface } from "node:readline";

const SIDECAR_DIR = resolve(import.meta.dirname, "../../../services/sidecar");

export interface Engine {
  port: number;
  token: string;
  library: string;
  stop: () => Promise<void>;
}

/** Starts the engine on a new empty library, or on `library` when given (e.g. a synthetic one). */
export async function startEngine(library = mkdtempSync(join(tmpdir(), "iv-e2e-lib-"))): Promise<Engine> {
  const token = "e2e".padEnd(32, "x");
  const proc: ChildProcess = spawn("uv", ["run", "--quiet", "python", "-m", "ivsidecar", "--library", library], {
    cwd: SIDECAR_DIR,
    env: { ...process.env, IV_TOKEN: token, IV_DEV_ORIGINS: "http://localhost:1421" },
    stdio: ["ignore", "pipe", "inherit"],
  });
  const line = await new Promise<string>((ok, fail) => {
    const rl = createInterface({ input: proc.stdout! });
    rl.once("line", ok);
    proc.once("exit", (code) => fail(new Error(`engine exited (${code}) before ready`)));
  });
  const ready = JSON.parse(line) as { event: string; port: number };
  if (ready.event !== "ready") throw new Error(`engine did not start: ${line}`);
  return {
    port: ready.port,
    token,
    library,
    stop: async () => {
      proc.kill();
      await new Promise((r) => proc.once("exit", r));
    },
  };
}

/** A folder with `n` small real JPEGs (made with Pillow from the sidecar's environment). */
export function makePhotos(n: number): string {
  const dir = mkdtempSync(join(tmpdir(), "iv-e2e-photos-"));
  const script = `
import sys
from PIL import Image
for i in range(${n}):
    Image.new("RGB", (640, 480), (40 * i % 256, 90, 160)).save(f"{sys.argv[1]}/photo_{i:02d}.jpg", quality=85)
`;
  const out = spawnSync("uv", ["run", "--quiet", "python", "-c", script, dir], { cwd: SIDECAR_DIR, encoding: "utf8" });
  if (out.status !== 0) throw new Error(out.stderr);
  return dir;
}

/** Like makePhotos, but file dates spread one month apart going back from today (no EXIF date,
 * so the engine dates them by the file's modified time). */
export function makeDatedPhotos(n: number, perMonth = 2): string {
  const dir = makePhotos(n);
  const script = `
import os, sys, time
for i in range(${n}):
    t = time.time() - (i // ${perMonth}) * 31 * 86400
    os.utime(f"{sys.argv[1]}/photo_{i:02d}.jpg", (t, t))
`;
  const out = spawnSync("uv", ["run", "--quiet", "python", "-c", script, dir], { cwd: SIDECAR_DIR, encoding: "utf8" });
  if (out.status !== 0) throw new Error(out.stderr);
  return dir;
}

/** A library of `count` synthetic assets (rows and shared thumbnails only) for performance runs. */
export function makeSyntheticLibrary(count: number): string {
  const library = mkdtempSync(join(tmpdir(), "iv-e2e-perf-"));
  const script = resolve(import.meta.dirname, "perf/synthetic_library.py");
  const out = spawnSync("uv", ["run", "--quiet", "python", script, library, String(count)], {
    cwd: SIDECAR_DIR,
    encoding: "utf8",
  });
  if (out.status !== 0) throw new Error(out.stderr);
  return library;
}
