import { useQuery } from "@tanstack/react-query";

import { type AppPaths, getAppPaths } from "./native";

/** Library and log folders from the shell (null outside the desktop app). */
export function useAppPaths(): AppPaths | null {
  return useQuery({ queryKey: ["appPaths"], queryFn: getAppPaths, staleTime: Infinity }).data ?? null;
}
