import type {
  AiOptions,
  AssetFilter,
  AssetPatch,
  AssetQuery,
  BackupOptions,
  BulkBody,
  Resources,
  ScanOptions,
  SmartRule,
  TaskProgressEvent,
} from "@imagevault/shared-types";
import {
  keepPreviousData,
  type QueryClient,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { createContext, type ReactNode, useContext, useEffect, useState } from "react";

import { type ApiClient, unwrap } from "./client";
import { applyPlan, LiveEvents, type LiveState, planFor } from "./events";
import { type JobFilter, keys } from "./keys";

const ApiContext = createContext<ApiClient | null>(null);

/** Supplies the engine client to every hook below. Remount it (new `key`) when the engine restarts. */
export function ApiProvider({ client, children }: { client: ApiClient; children: ReactNode }) {
  return <ApiContext.Provider value={client}>{children}</ApiContext.Provider>;
}

export function useApi(): ApiClient {
  const api = useContext(ApiContext);
  if (!api) throw new Error("useApi needs an <ApiProvider>");
  return api;
}

// ------------------------------------------------------------------------------ live updates
/** Keeps the cache fresh from WS /v1/events. Returns the connection state for the status bar. */
export function useLiveEvents(WebSocketImpl?: typeof WebSocket): LiveState {
  const api = useApi();
  const qc = useQueryClient();
  const [state, setState] = useState<LiveState>("connecting");
  useEffect(() => {
    const live = new LiveEvents(api.endpoint, {
      onEvents: (events) => applyPlan(qc, planFor(events)),
      onConnected: (reconnect) => {
        if (reconnect) void qc.invalidateQueries(); // changes made while we were away were not sent
      },
      onState: setState,
      ...(WebSocketImpl ? { WebSocketImpl } : {}),
    });
    return () => live.close();
  }, [api, qc, WebSocketImpl]);
  return state;
}

// ------------------------------------------------------------------------------ reads
export function useHealth() {
  const api = useApi();
  return useQuery({ queryKey: keys.health, queryFn: () => unwrap(api.GET("/v1/health")) });
}

export function useStats() {
  const api = useApi();
  return useQuery({ queryKey: keys.stats, queryFn: () => unwrap(api.GET("/v1/stats")) });
}

export function useJobStats() {
  const api = useApi();
  return useQuery({ queryKey: keys.jobStats, queryFn: () => unwrap(api.GET("/v1/jobs/stats")) });
}

export function useSources() {
  const api = useApi();
  return useQuery({ queryKey: keys.sources, queryFn: () => unwrap(api.GET("/v1/sources")) });
}

export function useRemovalPreview(sourceId: number | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.removal(sourceId ?? 0),
    queryFn: () =>
      unwrap(api.GET("/v1/sources/{source_id}/removal", { params: { path: { source_id: sourceId ?? 0 } } })),
    enabled: sourceId !== null,
    staleTime: 0,
  });
}

/** Newest first, 100 per page; `fetchNextPage` loads older jobs. */
export function useJobs(filter: JobFilter = {}) {
  const api = useApi();
  return useInfiniteQuery({
    queryKey: keys.jobs.list(filter),
    initialPageParam: undefined as number | undefined,
    queryFn: ({ pageParam }) =>
      unwrap(api.GET("/v1/jobs", { params: { query: { ...filter, before_id: pageParam, limit: 100 } } })),
    getNextPageParam: (last) => last.next_before_id ?? undefined,
  });
}

/** Keyset-paged photo list (B8a). The cursor belongs to the sort, so a new sort starts over. */
export function useAssets(query: Omit<AssetQuery, "cursor"> = {}) {
  const api = useApi();
  return useInfiniteQuery({
    queryKey: keys.assets.list(query),
    initialPageParam: undefined as string | undefined,
    queryFn: ({ pageParam }) => unwrap(api.GET("/v1/assets", { params: { query: { ...query, cursor: pageParam } } })),
    getNextPageParam: (last) => last.next_cursor ?? undefined,
  });
}

/** How many assets each month holds, for the timeline (same filters as the list). */
export function useAssetMonths(filter: AssetFilter) {
  const api = useApi();
  return useQuery({
    queryKey: keys.assets.months(filter),
    queryFn: () => unwrap(api.GET("/v1/assets:months", { params: { query: filter } })),
  });
}

/** Counts per filter option for the filter panel (B15). Keeps the last counts while new ones load. */
export function useAssetFacets(filter: AssetFilter, tagQ = "", enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: keys.assets.facets(filter, tagQ),
    queryFn: () =>
      unwrap(api.GET("/v1/assets:facets", { params: { query: { ...filter, ...(tagQ ? { tag_q: tagQ } : {}) } } })),
    placeholderData: keepPreviousData,
    enabled,
  });
}

export function useAsset(id: number | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.asset.one(id ?? 0),
    queryFn: () => unwrap(api.GET("/v1/assets/{asset_id}", { params: { path: { asset_id: id ?? 0 } } })),
    enabled: id !== null,
  });
}

export function useTags() {
  const api = useApi();
  return useQuery({ queryKey: keys.tags, queryFn: () => unwrap(api.GET("/v1/tags")) });
}

export function useCollections() {
  const api = useApi();
  return useQuery({ queryKey: keys.collections, queryFn: () => unwrap(api.GET("/v1/collections")) });
}

export function useResources() {
  const api = useApi();
  return useQuery({ queryKey: keys.settings.resources, queryFn: () => unwrap(api.GET("/v1/settings/resources")) });
}

export function useScanOptions() {
  const api = useApi();
  return useQuery({ queryKey: keys.settings.scan, queryFn: () => unwrap(api.GET("/v1/settings/scan")) });
}

// ------------------------------------------------------------------------------ writes
// Each mutation refreshes what it changed on success; the live events refresh it again for
// other windows and for changes the engine makes later (scans, processing).
function refresh(qc: QueryClient, ...queryKeys: readonly (readonly unknown[])[]) {
  return Promise.all(queryKeys.map((queryKey) => qc.invalidateQueries({ queryKey })));
}

export function useAddSource() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { path: string; display_name?: string | null }) =>
      unwrap(api.POST("/v1/sources", { body })),
    onSuccess: () => refresh(qc, keys.sources, keys.stats),
  });
}

export function useScanSource() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: number) =>
      unwrap(api.POST("/v1/sources/{source_id}/scan", { params: { path: { source_id: sourceId } } })),
    onSuccess: () => refresh(qc, keys.sources),
  });
}

export function useCancelScan() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceId: number) =>
      unwrap(api.POST("/v1/sources/{source_id}/scan/cancel", { params: { path: { source_id: sourceId } } })),
    onSuccess: () => refresh(qc, keys.sources),
  });
}

/** `confirm` must be the assets_only_here count the user saw in the removal preview. */
export function useRemoveSource() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ sourceId, confirm }: { sourceId: number; confirm: number }) =>
      unwrap(
        api.DELETE("/v1/sources/{source_id}", { params: { path: { source_id: sourceId }, query: { confirm } } }),
      ),
    onSuccess: () => refresh(qc, keys.sources, keys.stats, keys.assets.all, keys.asset.all),
  });
}

export function useRetryJob() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: number) =>
      unwrap(api.POST("/v1/jobs/{job_id}/retry", { params: { path: { job_id: jobId } } })),
    onSuccess: () => refresh(qc, keys.jobs.all, keys.jobStats, keys.stats),
  });
}

export function useRetryAllFailed() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/v1/jobs/retry")),
    onSuccess: () => refresh(qc, keys.jobs.all, keys.jobStats, keys.stats),
  });
}

export function useSetPaused() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (paused: boolean) => unwrap(api.POST(paused ? "/v1/jobs/pause" : "/v1/jobs/resume")),
    onSuccess: () => refresh(qc, keys.jobStats, keys.stats, keys.health),
  });
}

export function useSaveResources() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Resources) => unwrap(api.PUT("/v1/settings/resources", { body })),
    onSuccess: (saved) => qc.setQueryData(keys.settings.resources, saved),
  });
}

export function useSaveScanOptions() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: ScanOptions) => unwrap(api.PUT("/v1/settings/scan", { body })),
    onSuccess: (saved) => qc.setQueryData(keys.settings.scan, saved),
  });
}

export function usePatchAsset() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: number; patch: AssetPatch }) =>
      unwrap(api.PATCH("/v1/assets/{asset_id}", { params: { path: { asset_id: id } }, body: patch })),
    onSuccess: (detail) => {
      qc.setQueryData(keys.asset.one(detail.id), detail);
      return refresh(qc, keys.assets.all);
    },
  });
}

export function useBulk() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BulkBody) => unwrap(api.POST("/v1/assets:bulk", { body })),
    onSuccess: () => refresh(qc, keys.assets.all, keys.asset.all, keys.stats, keys.tags, keys.collections),
  });
}

// ------------------------------------------------------------------------------ asset detail (B13)
/** The 1600 px preview: made by the engine the first time a photo is opened, then reused. */
export function useAssetPreview(id: number | null) {
  const api = useApi();
  return useQuery({
    queryKey: keys.asset.preview(id ?? 0),
    queryFn: () => unwrap(api.POST("/v1/assets/{asset_id}/preview", { params: { path: { asset_id: id ?? 0 } } })),
    enabled: id !== null,
    staleTime: Infinity,
    retry: false, // 409/422 mean "show the thumbnail instead", not "try again"
  });
}

/** Edits from the detail panel: each refreshes that photo (and what else it shows). */
export function useAssetEdits(id: number) {
  const api = useApi();
  const qc = useQueryClient();
  const path = { asset_id: id };
  const done = (...more: (readonly unknown[])[]) => () => refresh(qc, keys.asset.one(id), ...more);
  return {
    addTags: useMutation({
      mutationFn: (names: string[]) => unwrap(api.POST("/v1/assets/{asset_id}/tags", { params: { path }, body: { names } })),
      onSuccess: done(keys.tags),
    }),
    removeTag: useMutation({
      mutationFn: (tagId: number) =>
        unwrap(api.DELETE("/v1/assets/{asset_id}/tags/{tag_id}", { params: { path: { ...path, tag_id: tagId } } })),
      onSuccess: done(keys.tags),
    }),
    addNote: useMutation({
      mutationFn: (body: string) => unwrap(api.POST("/v1/assets/{asset_id}/notes", { params: { path }, body: { body } })),
      onSuccess: done(),
    }),
    editNote: useMutation({
      mutationFn: ({ noteId, body }: { noteId: number; body: string }) =>
        unwrap(api.PATCH("/v1/assets/{asset_id}/notes/{note_id}", { params: { path: { ...path, note_id: noteId } }, body: { body } })),
      onSuccess: done(),
    }),
    deleteNote: useMutation({
      mutationFn: (noteId: number) =>
        unwrap(api.DELETE("/v1/assets/{asset_id}/notes/{note_id}", { params: { path: { ...path, note_id: noteId } } })),
      onSuccess: done(),
    }),
    addToCollection: useMutation({
      mutationFn: (collId: number) =>
        unwrap(api.POST("/v1/collections/{coll_id}/assets", { params: { path: { coll_id: collId } }, body: { ids: [id] } })),
      onSuccess: done(keys.collections),
    }),
    removeFromCollection: useMutation({
      mutationFn: (collId: number) =>
        unwrap(api.DELETE("/v1/collections/{coll_id}/assets", { params: { path: { coll_id: collId } }, body: { ids: [id] } })),
      onSuccess: done(keys.collections),
    }),
    setHidden: useMutation({
      mutationFn: (hide: boolean) =>
        unwrap(api.POST(hide ? "/v1/assets/{asset_id}/hide" : "/v1/assets/{asset_id}/unhide", { params: { path } })),
      onSuccess: done(keys.assets.all, keys.stats),
    }),
  };
}

// ------------------------------------------------------------------------------ backups (B19)
export function useBackups() {
  const api = useApi();
  return useQuery({ queryKey: keys.backups, queryFn: () => unwrap(api.GET("/v1/backups")) });
}

export function useBackupOptions() {
  const api = useApi();
  return useQuery({ queryKey: keys.settings.backup, queryFn: () => unwrap(api.GET("/v1/settings/backup")) });
}

export function useBackupNow() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/v1/backups")),
    onSuccess: () => refresh(qc, keys.backups),
  });
}

export function useSaveBackupOptions() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: BackupOptions) => unwrap(api.PUT("/v1/settings/backup", { body })),
    onSuccess: () => refresh(qc, keys.settings.backup),
  });
}

/** Replaces the library with a backup (by name) or a .db file (by path); then everything is refetched. */
export function useRestore() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string } | { path: string }) => unwrap(api.POST("/v1/backups:restore", { body })),
    onSuccess: () => qc.invalidateQueries(),
  });
}

export function useExport() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { format: "json" | "csv"; path: string; include_hidden: boolean }) => {
      qc.removeQueries({ queryKey: keys.task("export") });
      return unwrap(api.POST("/v1/export", { body }));
    },
  });
}

/** Latest export progress from live events, or undefined. */
export function useTaskProgress(name: "export" | "ai_models"): TaskProgressEvent | undefined {
  const qc = useQueryClient();
  return useQuery({
    queryKey: keys.task(name),
    queryFn: () => qc.getQueryData<TaskProgressEvent>(keys.task(name)) ?? null,
    enabled: false,
    staleTime: Infinity,
  }).data ?? undefined;
}

// ------------------------------------------------------------------ collections (B16)
export function useCreateCollection() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; parent_id?: number | null; rule?: SmartRule | null }) =>
      unwrap(api.POST("/v1/collections", { body })),
    onSuccess: () => refresh(qc, keys.collections),
  });
}

export function useUpdateCollection() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; name?: string; parent_id?: number | null; rule?: SmartRule | null }) =>
      unwrap(api.PATCH("/v1/collections/{coll_id}", { params: { path: { coll_id: id } }, body })),
    // a new rule changes which photos a smart collection shows, so its photo lists refetch too
    onSuccess: (_c, vars) => (vars.rule !== undefined ? refresh(qc, keys.collections, keys.assets.all) : refresh(qc, keys.collections)),
  });
}

/** Photos stay; collections inside it move up one level. */
export function useDeleteCollection() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => unwrap(api.DELETE("/v1/collections/{coll_id}", { params: { path: { coll_id: id } } })),
    onSuccess: () => refresh(qc, keys.collections, keys.assets.all, keys.asset.all),
  });
}

// ------------------------------------------------------------------ automatic tags (T2)
/** Model download, the on/off switch and how far tagging has got. Polled while work is going on. */
export function useAi() {
  const api = useApi();
  return useQuery({
    queryKey: keys.ai,
    queryFn: () => unwrap(api.GET("/v1/ai")),
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (d.download.state === "downloading" || (d.enabled && d.photos_waiting > 0)) ? 3000 : false;
    },
  });
}

/** "Sorted by AI" (T3): the most common AI tags, each a virtual group (the tag filter). */
export function useAiGroups(enabled = true) {
  const api = useApi();
  return useQuery({
    queryKey: keys.assets.aiGroups,
    queryFn: () => unwrap(api.GET("/v1/ai/groups")),
    enabled,
  });
}

// ------------------------------------------------------------------ search (T4)
/** Photos that look like a description (CLIP, on this PC). Empty text runs nothing. */
export function useSearch(text: string, limit = 120) {
  const api = useApi();
  const q = text.trim();
  return useQuery({
    queryKey: keys.search.text(q, limit),
    queryFn: () => unwrap(api.POST("/v1/search", { body: { text: q, limit } })),
    enabled: q.length > 0,
    staleTime: 30_000,
  });
}

/** Photos that look like this one. A 409 means it has not been analysed yet. */
export function useSimilar(id: number, limit = 120) {
  const api = useApi();
  return useQuery({
    queryKey: keys.search.similar(id, limit),
    queryFn: () => unwrap(api.GET("/v1/assets/{asset_id}/similar", { params: { path: { asset_id: id }, query: { limit } } })),
    staleTime: 30_000,
  });
}

// ------------------------------------------------------------------ duplicates (D2)
export type DuplicateTab = "copies" | "similar";

export function useDuplicateSummary() {
  const api = useApi();
  return useQuery({
    queryKey: keys.assets.duplicateSummary,
    queryFn: () => unwrap(api.GET("/v1/duplicates:summary")),
    // while a scan runs, poll as a fallback to the duplicates.updated event
    refetchInterval: (q) => (q.state.data?.scanning ? 2000 : false),
  });
}

export function useDuplicateGroups(tab: DuplicateTab, limit = 50) {
  const api = useApi();
  return useQuery({
    queryKey: keys.assets.duplicateGroups(tab, limit),
    queryFn: () => unwrap(api.GET("/v1/duplicates", { params: { query: { tab, limit } } })),
  });
}

/** Keep `keep` (default: the suggested photo) and hide the others. Files are never touched. */
export function useResolveDuplicates() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ groupId, keep }: { groupId: number; keep?: number[] }) =>
      unwrap(api.POST("/v1/duplicates/{group_id}/resolve", { params: { path: { group_id: groupId } }, body: keep ? { keep } : {} })),
    onSuccess: () => refresh(qc, keys.assets.all, keys.asset.all, keys.stats, keys.tags, keys.collections),
  });
}

export function useDismissDuplicates() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (groupId: number) =>
      unwrap(api.POST("/v1/duplicates/{group_id}/dismiss", { params: { path: { group_id: groupId } } })),
    onSuccess: () => refresh(qc, keys.assets.duplicates),
  });
}

export function useResolveAllDuplicates() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tab: DuplicateTab) => unwrap(api.POST("/v1/duplicates:resolve-all", { body: { tab } })),
    onSuccess: () => refresh(qc, keys.assets.all, keys.asset.all, keys.stats, keys.tags, keys.collections),
  });
}

export function useRescanDuplicates() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/v1/duplicates:rescan")),
    onSuccess: (data) => qc.setQueryData(keys.assets.duplicateSummary, data),
  });
}

// ------------------------------------------------------------------ teach a tag (T5)
export function useTaughtTags() {
  const api = useApi();
  return useQuery({ queryKey: keys.aiTaught, queryFn: () => unwrap(api.GET("/v1/ai/taught")) });
}

export function useTeachTag() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; prompt: string; threshold: number }) => unwrap(api.POST("/v1/ai/teach", { body })),
    onSuccess: () => refresh(qc, keys.aiTaught, keys.tags, keys.assets.all, keys.asset.all),
  });
}

export function useUntrainTag() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (tagId: number) => unwrap(api.DELETE("/v1/ai/taught/{tag_id}", { params: { path: { tag_id: tagId } } })),
    onSuccess: () => refresh(qc, keys.aiTaught, keys.tags, keys.assets.all, keys.asset.all),
  });
}

export function useInstallAi() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => unwrap(api.POST("/v1/ai:install")),
    onSuccess: (data) => qc.setQueryData(keys.ai, data),
  });
}

export function useSaveAiOptions() {
  const api = useApi();
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: AiOptions) => unwrap(api.PUT("/v1/settings/ai", { body })),
    onSuccess: () => refresh(qc, keys.ai),
  });
}
