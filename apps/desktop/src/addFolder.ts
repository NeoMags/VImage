import { useAddSource } from "@imagevault/api-client";
import { useCallback, useState } from "react";

import { errorText } from "./components/States";
import { pickFolder } from "./native";

/** Folder picker → POST /v1/sources (the engine validates the path and starts the first scan). */
export function useAddFolder() {
  const add = useAddSource();
  const [result, setResult] = useState<{ tone: "ok" | "danger"; text: string } | null>(null);
  const start = useCallback(async () => {
    setResult(null);
    const path = await pickFolder();
    if (!path) return;
    try {
      await add.mutateAsync({ path });
      setResult({ tone: "ok", text: `Added ${path}. Scanning has started.` });
    } catch (e) {
      setResult({ tone: "danger", text: `Could not add ${path}: ${errorText(e)}` });
    }
  }, [add]);
  return { start, busy: add.isPending, result, clear: () => setResult(null) };
}
