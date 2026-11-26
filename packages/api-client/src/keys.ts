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
    facets: (f: AssetFilter, tagQ: string) => ["assets", "facets", f, tagQ] as const,
    /** "Sorted by AI" groups: under "assets" so newly tagged photos refresh them. */
    aiGroups: ["assets", "aiGroups"] as const,
  },
  asset: {
    all: ["asset"] as const,
    one: (id: number) => ["asset", id] as const,
    preview: (id: number) => ["preview", id] as const, // not under "asset": live events must not re-request it
  },
  tags: ["tags"] as const,
  collections: ["collections"] as const,
  settings: {
    resources: ["settings", "resources"] as const,
    scan: ["settings", "scan"] as const,
    backup: ["settings", "backup"] as const,
    ai: ["settings", "ai"] as const,
  },
  ai: ["ai"] as const,
  /** Under "ai" so the ai.updated event refreshes it too. */
  aiTaught: ["ai", "taught"] as const,
  /** T4. Not refreshed by live events: re-running a search while photos are tagged would churn. */
  search: {
    text: (text: string, limit: number) => ["search", "text", text, limit] as const,
    similar: (id: number, limit: number) => ["search", "similar", id, limit] as const,
  },
  backups: ["backups"] as const,
  /** Progress of a long request, written by live events (never fetched). */
  task: (name: string) => ["task", name] as const,
};

export interface JobFilter {
  status?: "queued" | "running" | "succeeded" | "failed" | "skipped" | "dead";
  stage?: string;
}
