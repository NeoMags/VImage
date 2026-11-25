// The few things the UI needs from Windows, through the Tauri shell (B10). Outside Tauri (the
// end-to-end test in a plain browser) the engine endpoint comes from the URL instead, and the
// OS features report that they are unavailable.
import type { Endpoint } from "@imagevault/api-client";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export const inTauri = (): boolean => {
  try {
    return isTauri();
  } catch {
    return false;
  }
};

/** Thrown while the engine is starting or restarting: keep waiting. */
export class NotReady extends Error {}

export async function getEndpoint(): Promise<Endpoint> {
  if (inTauri()) {
    try {
      return await invoke<Endpoint>("get_sidecar_endpoint");
    } catch (e) {
      const msg = String(e);
      if (msg.includes("not ready")) throw new NotReady(msg);
      throw new Error(msg); // the shell gave up: shown on the error screen
    }
  }
  if (import.meta.env.DEV || import.meta.env.VITE_IV_E2E) {
    const q = new URLSearchParams(window.location.search);
    const port = Number(q.get("port"));
    const token = q.get("token");
    if (port && token) return { port, token };
  }
  throw new Error("ImageVault must be started from its desktop app.");
}

export interface AppPaths {
  library: string | null;
  logs: string | null;
  thumbs: string | null;
}

export async function getAppPaths(): Promise<AppPaths | null> {
  return inTauri() ? invoke<AppPaths>("get_app_paths") : null;
}

/** Windows folder picker; null when cancelled or outside the app. */
export async function pickFolder(): Promise<string | null> {
  if (!inTauri()) return window.prompt("Folder path") || null; // e2e / browser only
  const { open } = await import("@tauri-apps/plugin-dialog");
  const picked = await open({ directory: true, multiple: false, title: "Add a folder to ImageVault" });
  return typeof picked === "string" ? picked : null;
}

/** Show a file or folder in Explorer (the only opener permission the shell grants). */
export async function revealInExplorer(path: string): Promise<void> {
  if (!inTauri()) return;
  const { revealItemInDir } = await import("@tauri-apps/plugin-opener");
  await revealItemInDir(path);
}

export function onEngineEvent(
  handlers: { restarted: () => void; failed: (message: string) => void },
): () => void {
  if (!inTauri()) return () => {};
  const offs: Promise<UnlistenFn>[] = [
    listen("engine-restarted", () => handlers.restarted()),
    listen<string>("engine-failed", (e) => handlers.failed(e.payload)),
  ];
  return () => offs.forEach((p) => void p.then((off) => off()));
}
