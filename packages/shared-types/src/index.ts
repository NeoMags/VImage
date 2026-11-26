// Short names for the generated API types. The shapes live in ./api.ts (generated, do not edit).
import type { components, operations, paths } from "./api";

export type { components, operations, paths };
export type * from "./events";

type S = components["schemas"];

export type AssetSummary = S["AssetSummary"];
export type AssetPage = S["AssetPage"];
export type AssetDetail = S["AssetDetail"];
export type AssetMonths = S["AssetMonths"];
export type AssetFacets = S["AssetFacets"];
export type MonthCount = S["MonthCount"];
export type AssetPatch = S["AssetPatch"];
export type BulkBody = S["BulkBody"];
export type BulkResult = S["BulkResult"];
export type Tag = S["Tag"];
export type Collection = S["Collection"];
export type Note = S["Note"];
export type Job = S["Job"];
export type JobPage = S["JobPage"];
export type JobStats = S["JobStats"];
export type LibraryStats = S["LibraryStats"];
export type Health = S["Health"];
export type Source = S["Source"];
export type ScanRun = S["ScanRun"];
export type RemovalPreview = S["RemovalPreview"];
export type Resources = S["Resources"];
export type ScanOptions = S["ScanOptions"];
export type QueueRuntime = S["QueueRuntime"];
export type Backup = S["Backup"];
export type Backups = S["Backups"];
export type BackupMade = S["BackupMade"];
export type BackupOptions = S["BackupOptions"];
export type Restored = S["Restored"];
export type Exported = S["Exported"];
export type AiStatus = S["AiStatus"];
export type SearchHit = S["SearchHit"];
export type SearchResults = S["SearchResults"];
export type AiOptions = S["AiOptions"];

export type AssetKind = AssetSummary["kind"];
export type AssetStatus = AssetSummary["status"];
export type Availability = AssetSummary["availability"];
export type JobStatus = Job["status"];
export type SourceStatus = Source["status"];

/** Query parameters of GET /v1/assets (filters, sort, cursor). */
export type AssetQuery = NonNullable<paths["/v1/assets"]["get"]["parameters"]["query"]>;

/** Every error from the engine (doc 04 §1, application/problem+json). */
export interface Problem {
  type: string;
  title: string;
  status: number;
  code:
    | "validation_failed"
    | "unauthorized"
    | "forbidden"
    | "not_found"
    | "method_not_allowed"
    | "conflict"
    | "unsupported_media"
    | "busy"
    | "internal"
    | "error";
  detail: string;
  errors?: { loc: string[]; msg: string }[];
}

/** The filters shared by GET /v1/assets and GET /v1/assets:months (no paging or sort). */
export type AssetFilter = NonNullable<paths["/v1/assets:months"]["get"]["parameters"]["query"]>;
