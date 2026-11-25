import type { AssetFilter, AssetQuery } from "@imagevault/shared-types";

/** Query keys in one place, so live events and mutations refresh exactly what screens read. */
export const keys = {
  health: ["health"] as const,
  stats: ["stats"] as const,
  jobStats: ["jobStats"] as const,
  sources: ["sources"] as const,
  removal: (sourceId: number) => ["sources", sourceId, "removal"] as const,
  jobs: {
    all: ["jobs"] as const,
    list: (filter: JobFilter) => ["jobs", filter] as const,
  },
  assets: {
    all: ["assets"] as const,
    list: (q: Omit<AssetQuery, "cursor">) => ["assets", q] as const,
    months: (f: AssetFilter) => ["assets", "months", f] as const,
  },
  asset: {
    all: ["asset"] as const,
    one: (id: number) => ["asset", id] as const,
  },
  tags: ["tags"] as const,
  collections: ["collections"] as const,
  settings: {
    resources: ["settings", "resources"] as const,
    scan: ["settings", "scan"] as const,
  },
};

export interface JobFilter {
  status?: "queued" | "running" | "succeeded" | "failed" | "skipped" | "dead";
  stage?: string;
}
