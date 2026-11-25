import { useApi } from "@imagevault/api-client";
import { convertFileSrc } from "@tauri-apps/api/core";
import { useCallback } from "react";

import { inTauri } from "../native";
import { useAppPaths } from "../paths";

/**
 * Turns a derivative path (relative to the library folder) into an <img> URL.
 * In the app: the Tauri asset protocol, whose scope is only <library>/thumbs (B10).
 * In a development browser: the engine's dev-only route (never present in the installed app).
 */
export function useThumbUrl(): (relPath: string | null) => string | null {
  const paths = useAppPaths();
  const api = useApi();
  const tauri = inTauri();
  return useCallback(
    (rel) => {
      if (!rel) return null;
      if (tauri) {
        if (!paths?.library) return null;
        const sep = paths.library.includes("\\") ? "\\" : "/";
        return convertFileSrc(`${paths.library}${sep}${rel.split("/").join(sep)}`);
      }
      const { port, token } = api.endpoint;
      return `http://127.0.0.1:${port}/dev/thumbs/${rel.split("/").map(encodeURIComponent).join("/")}?t=${encodeURIComponent(token)}`;
    },
    [tauri, paths?.library, api.endpoint],
  );
}
