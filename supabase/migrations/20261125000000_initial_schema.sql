-- ImageVault initial schema (DRAFT for review, not applied to any Supabase project).
-- Target: Supabase Postgres 17 + pgvector 0.8.x. Load-tested on postgres:17 + pgvector 0.8.7
-- with a stub `auth` schema (supabase/tests/auth_stub.sql).
-- Conventions:
--   * every tenant-owned row carries workspace_id; RLS keys on it.
--   * provenance: metadata_origin = source | ai | user. Workers never overwrite origin='user'.
--   * row_version + change_log feed incremental sync (docs/05-local-indexing-and-sync.md).

create extension if not exists vector with schema extensions;
create extension if not exists pg_trgm with schema extensions;
create schema if not exists private;  -- helper functions, not exposed through PostgREST

-- ---------------------------------------------------------------- enums
create type public.member_role      as enum ('owner','admin','editor','viewer');
create type public.workspace_kind   as enum ('personal','family','business');
create type public.device_platform  as enum ('web','windows','android','ios');
create type public.provider_kind    as enum ('upload','local_fs','external_drive','network_share',
  'google_drive','google_photos_picker','onedrive','dropbox','s3','r2','mobile_gallery','browser_extension');
create type public.connection_status as enum ('active','paused','error','revoked');
create type public.asset_kind       as enum ('image','screenshot','video','pdf','drawing','document','other');
create type public.asset_status     as enum ('pending','processing','ready','failed','quarantined');
create type public.review_state     as enum ('unreviewed','reviewed');
create type public.location_state   as enum ('present','moved','missing','deleted_at_source','unreachable');
create type public.metadata_origin  as enum ('source','ai','user');
create type public.derivative_kind  as enum ('thumb_256','thumb_512','preview_1600','video_poster');
create type public.region_kind      as enum ('face','object','text_block','user_selection');
create type public.entity_type      as enum ('url','email','phone','date','address','code','command',
  'error_message','product','technical_term','person_name','organization','amount','other');
create type public.job_stage        as enum ('identify','metadata','hash','thumbnail','ocr','classify',
  'embed','describe','knowledge','faces','duplicates');
create type public.job_status       as enum ('queued','running','succeeded','failed','cancelled','dead');
create type public.duplicate_level  as enum ('exact','perceptual','visual');
create type public.group_status     as enum ('open','resolved','dismissed');
create type public.collection_kind  as enum ('manual','smart');
create type public.share_permission as enum ('view','edit');
create type public.consent_kind     as enum ('face_processing','cloud_ai_processing');
create type public.card_status      as enum ('ai_generated','user_reviewed');
create type public.embedding_modality as enum ('image','text');

-- ---------------------------------------------------------------- identity & tenancy
create table public.users (                       -- 1:1 profile for auth.users
  id           uuid primary key references auth.users(id) on delete cascade,
  display_name text,
  avatar_url   text,
  created_at   timestamptz not null default now()
);

create table public.organizations (
  id         uuid primary key default gen_random_uuid(),
  name       text not null check (length(name) between 1 and 120),
  owner_id   uuid not null references public.users(id),
  created_at timestamptz not null default now()
);

create table public.workspaces (
  id              uuid primary key default gen_random_uuid(),
  organization_id uuid not null references public.organizations(id) on delete cascade,
  kind            public.workspace_kind not null default 'personal',
  name            text not null check (length(name) between 1 and 120),
  created_by      uuid not null references public.users(id),
  created_at      timestamptz not null default now()
);

create table public.memberships (
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id      uuid not null references public.users(id) on delete cascade,
  role         public.member_role not null,
  created_at   timestamptz not null default now(),
  primary key (workspace_id, user_id)
);
create index on public.memberships (user_id);

create table public.devices (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references public.users(id) on delete cascade,
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  platform      public.device_platform not null,
  name          text not null,
  app_version   text,
  last_seen_at  timestamptz,
  last_pulled_seq bigint not null default 0,
  revoked_at    timestamptz,
  created_at    timestamptz not null default now()
);
create index on public.devices (workspace_id, user_id);

create table public.user_preferences (
  user_id    uuid primary key references public.users(id) on delete cascade,
  prefs      jsonb not null default '{}'::jsonb,   -- UI prefs only; validated by API schema
  updated_at timestamptz not null default now()
);

create table public.consents (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id      uuid not null references public.users(id) on delete cascade,
  kind         public.consent_kind not null,
  granted_at   timestamptz not null default now(),
  revoked_at   timestamptz,
  policy_version text not null
);
create unique index consents_one_active on public.consents (workspace_id, user_id, kind) where revoked_at is null;

-- ---------------------------------------------------------------- sources
create table public.storage_connections (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  provider        public.provider_kind not null,
  display_name    text not null,
  status          public.connection_status not null default 'active',
  device_id       uuid references public.devices(id) on delete set null,  -- local/drive/NAS sources
  config          jsonb not null default '{}'::jsonb,  -- non-secret settings (root path, exclusions)
  credential_ref  uuid,          -- Supabase Vault secret id; tokens never stored in this row
  sync_cursor     text,          -- provider delta token / last scan marker
  last_synced_at  timestamptz,
  last_error      text,
  created_by      uuid not null references public.users(id),
  created_at      timestamptz not null default now(),
  check ((provider in ('local_fs','external_drive','network_share','mobile_gallery')) = (device_id is not null)
         or status = 'revoked')
);
create index on public.storage_connections (workspace_id);

-- ---------------------------------------------------------------- assets
create table public.assets (
  id                 uuid primary key default gen_random_uuid(),
  workspace_id       uuid not null references public.workspaces(id) on delete cascade,
  kind               public.asset_kind not null default 'image',
  status             public.asset_status not null default 'pending',
  sha256             bytea not null check (length(sha256) = 32),
  phash              bit(64),
  mime_type          text not null,
  original_filename  text not null,
  title              text,
  byte_size          bigint not null check (byte_size >= 0),
  width              int check (width > 0),
  height             int check (height > 0),
  duration_ms        int,
  taken_at           timestamptz,
  source_created_at  timestamptz,
  source_modified_at timestamptz,
  imported_at        timestamptz not null default now(),
  imported_by        uuid references public.users(id),
  original_storage_key text,     -- null when original is reference-only (stays at source)
  is_favorite        boolean not null default false,
  review_state       public.review_state not null default 'unreviewed',
  exclude_faces      boolean not null default false,
  search_tsv         tsvector,   -- maintained by worker: filename, title, tags, OCR, description
  row_version        bigint not null default 1,
  updated_at         timestamptz not null default now(),
  deleted_at         timestamptz    -- soft delete inside ImageVault only; never touches the source file
);
create unique index assets_ws_sha256 on public.assets (workspace_id, sha256) where deleted_at is null;
create index assets_ws_imported on public.assets (workspace_id, imported_at desc, id) where deleted_at is null;
create index assets_ws_taken    on public.assets (workspace_id, taken_at desc nulls last, id) where deleted_at is null;
create index assets_ws_kind     on public.assets (workspace_id, kind) where deleted_at is null;
create index assets_search_tsv  on public.assets using gin (search_tsv);
create index assets_phash       on public.assets using hnsw ((phash) bit_hamming_ops) where phash is not null;

create table public.storage_locations (            -- where an asset's bytes live at the source
  id               uuid primary key default gen_random_uuid(),
  workspace_id     uuid not null references public.workspaces(id) on delete cascade,
  connection_id    uuid not null references public.storage_connections(id) on delete cascade,
  asset_id         uuid not null references public.assets(id) on delete cascade,
  provider_item_id text not null,      -- Drive fileId, Dropbox id, NTFS file id, S3 key, etc.
  path             text,
  state            public.location_state not null default 'present',
  etag             text,
  size_bytes       bigint,
  source_modified_at timestamptz,
  first_seen_at    timestamptz not null default now(),
  last_seen_at     timestamptz not null default now(),
  unique (connection_id, provider_item_id)
);
create index on public.storage_locations (asset_id);
create index on public.storage_locations (workspace_id, state);

create table public.asset_versions (               -- content revisions of one logical asset
  id          uuid primary key default gen_random_uuid(),
  asset_id    uuid not null references public.assets(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  version_no  int not null,
  sha256      bytea not null check (length(sha256) = 32),
  byte_size   bigint not null,
  storage_key text,
  reason      text not null default 'source_changed',
  created_at  timestamptz not null default now(),
  unique (asset_id, version_no)
);

create table public.asset_derivatives (
  asset_id    uuid not null references public.assets(id) on delete cascade,
  kind        public.derivative_kind not null,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  storage_key text not null,
  width int not null, height int not null,
  format      text not null,
  byte_size   bigint not null,
  created_at  timestamptz not null default now(),
  primary key (asset_id, kind)
);

create table public.asset_metadata (               -- original (source) metadata, never AI
  asset_id      uuid primary key references public.assets(id) on delete cascade,
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  camera_make   text, camera_model text, lens text,
  iso int, exposure_s numeric, f_number numeric, focal_length_mm numeric,
  orientation   smallint, color_profile text,
  gps_lat numeric(9,6) check (gps_lat between -90 and 90),
  gps_lon numeric(9,6) check (gps_lon between -180 and 180),
  gps_alt_m numeric,
  raw_exif      jsonb,        -- full ExifTool dump; GPS also redacted here when location sharing is off
  extractor     text not null,
  extracted_at  timestamptz not null default now()
);

create table public.asset_regions (
  id          uuid primary key default gen_random_uuid(),
  asset_id    uuid not null references public.assets(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  kind        public.region_kind not null,
  x real not null, y real not null, w real not null, h real not null,  -- normalized 0..1
  label       text,
  confidence  real,
  origin      public.metadata_origin not null,
  created_at  timestamptz not null default now(),
  check (x >= 0 and y >= 0 and w > 0 and h > 0 and x + w <= 1.0001 and y + h <= 1.0001)
);
create index on public.asset_regions (asset_id, kind);

-- ---------------------------------------------------------------- AI outputs
create table public.embedding_models (
  id        smallint primary key,
  name      text not null,           -- e.g. 'open_clip:ViT-B-32:laion2b_s34b_b79k'
  version   text not null,
  modality  public.embedding_modality not null,
  dims      int not null,
  is_active boolean not null default false,
  unique (name, version, modality)
);

create table public.asset_embeddings (
  id          bigint generated always as identity primary key,
  asset_id    uuid not null references public.assets(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  model_id    smallint not null references public.embedding_models(id),
  region_id   uuid references public.asset_regions(id) on delete cascade,  -- null = whole image
  embedding   vector not null,   -- dims fixed per model; indexed per model below
  created_at  timestamptz not null default now(),
  unique nulls not distinct (asset_id, model_id, region_id)
);
create index on public.asset_embeddings (workspace_id, model_id);
-- One partial HNSW index per active model (dims must be fixed for an index). Model id 1 = 512-d CLIP:
create index asset_embeddings_m1_hnsw on public.asset_embeddings
  using hnsw ((embedding::vector(512)) vector_cosine_ops) where model_id = 1;

create table public.asset_descriptions (
  id          uuid primary key default gen_random_uuid(),
  asset_id    uuid not null references public.assets(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  origin      public.metadata_origin not null,
  model_name  text,
  body        text not null,
  confidence  real,
  is_current  boolean not null default true,
  created_at  timestamptz not null default now()
);
create unique index asset_descriptions_current on public.asset_descriptions (asset_id) where is_current;

create table public.extracted_text (
  id          uuid primary key default gen_random_uuid(),
  asset_id    uuid not null references public.assets(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  engine      text not null,          -- 'paddleocr' | 'tesseract' | 'user'
  engine_version text not null,
  language    text,
  full_text   text not null,
  tsv         tsvector generated always as (to_tsvector('simple', full_text)) stored,
  confidence  real,
  origin      public.metadata_origin not null default 'ai',
  is_current  boolean not null default true,
  created_at  timestamptz not null default now()
);
create unique index extracted_text_current on public.extracted_text (asset_id) where is_current;
create index extracted_text_tsv on public.extracted_text using gin (tsv);
create index extracted_text_trgm on public.extracted_text using gin (full_text gin_trgm_ops); -- exact/substring

create table public.text_regions (                 -- OCR line/word boxes
  id               uuid primary key default gen_random_uuid(),
  extracted_text_id uuid not null references public.extracted_text(id) on delete cascade,
  workspace_id     uuid not null references public.workspaces(id) on delete cascade,
  line_no          int not null,
  text             text not null,
  x real not null, y real not null, w real not null, h real not null,
  confidence       real
);
create index on public.text_regions (extracted_text_id, line_no);

create table public.extracted_entities (
  id             uuid primary key default gen_random_uuid(),
  asset_id       uuid not null references public.assets(id) on delete cascade,
  workspace_id   uuid not null references public.workspaces(id) on delete cascade,
  text_region_id uuid references public.text_regions(id) on delete set null,  -- provenance link
  entity_type    public.entity_type not null,
  value          text not null,
  normalized_value text,
  confidence     real,
  origin         public.metadata_origin not null,
  is_sensitive   boolean not null default false,   -- phone/email/address: excluded from logs
  created_at     timestamptz not null default now()
);
create index on public.extracted_entities (workspace_id, entity_type, normalized_value);
create index on public.extracted_entities (asset_id);

-- ---------------------------------------------------------------- taxonomy
create table public.categories (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid references public.workspaces(id) on delete cascade,  -- null = system taxonomy
  parent_id    uuid references public.categories(id) on delete cascade,
  domain       text not null,     -- 'screenshot' | 'architecture' | 'material' | 'style' | ...
  slug         text not null,
  name         text not null,
  unique nulls not distinct (workspace_id, parent_id, slug)
);

create table public.tags (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name         text not null,
  slug         text not null,
  unique (workspace_id, slug)
);

create table public.asset_tags (
  asset_id     uuid not null references public.assets(id) on delete cascade,
  tag_id       uuid not null references public.tags(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  origin       public.metadata_origin not null,
  confidence   real,
  confirmed_by uuid references public.users(id),
  confirmed_at timestamptz,
  rejected     boolean not null default false,  -- user said "not this tag"; AI must not re-add
  primary key (asset_id, tag_id)
);
create index on public.asset_tags (tag_id);

create table public.asset_categories (
  asset_id     uuid not null references public.assets(id) on delete cascade,
  category_id  uuid not null references public.categories(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  origin       public.metadata_origin not null,
  confidence   real,
  confirmed_by uuid references public.users(id),
  rejected     boolean not null default false,
  primary key (asset_id, category_id)
);
create index on public.asset_categories (category_id);

-- ---------------------------------------------------------------- people (Phase 7, opt-in)
create table public.people (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  display_name text not null,
  notes        text,
  created_by   uuid not null references public.users(id),
  created_at   timestamptz not null default now()
);

create table public.face_clusters (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  person_id    uuid references public.people(id) on delete set null,   -- set only by a user action
  model_id     smallint not null references public.embedding_models(id),
  member_count int not null default 0,
  created_at   timestamptz not null default now()
);

create table public.asset_faces (
  id           uuid primary key default gen_random_uuid(),
  asset_id     uuid not null references public.assets(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  region_id    uuid not null references public.asset_regions(id) on delete cascade,
  cluster_id   uuid references public.face_clusters(id) on delete set null,
  person_id    uuid references public.people(id) on delete set null,
  confirmed_by uuid references public.users(id),     -- identity only with explicit confirmation
  model_id     smallint not null references public.embedding_models(id),
  embedding    vector not null,                       -- biometric: purge on consent revoke
  created_at   timestamptz not null default now(),
  check (person_id is null or confirmed_by is not null)
);
create index on public.asset_faces (asset_id);
create index on public.asset_faces (person_id);

-- ---------------------------------------------------------------- organization
create table public.collections (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  parent_id    uuid references public.collections(id) on delete cascade,
  kind         public.collection_kind not null default 'manual',
  name         text not null check (length(name) between 1 and 200),
  rule         jsonb,            -- smart collections: validated FilterExpression (docs/04)
  created_by   uuid not null references public.users(id),
  row_version  bigint not null default 1,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  check ((kind = 'smart') = (rule is not null))
);
create index on public.collections (workspace_id, parent_id);

create table public.collection_assets (
  collection_id uuid not null references public.collections(id) on delete cascade,
  asset_id      uuid not null references public.assets(id) on delete cascade,
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  position      double precision not null default 0,
  added_by      uuid references public.users(id),
  added_at      timestamptz not null default now(),
  primary key (collection_id, asset_id)
);
create index on public.collection_assets (asset_id);

create table public.collection_shares (
  id              uuid primary key default gen_random_uuid(),
  collection_id   uuid not null references public.collections(id) on delete cascade,
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  grantee_user_id uuid references public.users(id) on delete cascade,
  link_token_hash bytea,          -- sha256 of share token; raw token shown once
  permission      public.share_permission not null default 'view',
  expires_at      timestamptz,
  created_by      uuid not null references public.users(id),
  created_at      timestamptz not null default now(),
  check ((grantee_user_id is null) <> (link_token_hash is null))
);

create table public.asset_notes (
  id           uuid primary key default gen_random_uuid(),
  asset_id     uuid not null references public.assets(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  author_id    uuid not null references public.users(id),
  body         text not null check (length(body) <= 20000),
  row_version  bigint not null default 1,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index on public.asset_notes (asset_id);

create table public.saved_searches (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  user_id      uuid not null references public.users(id) on delete cascade,
  name         text not null,
  query        jsonb not null,    -- SearchRequest (docs/04)
  created_at   timestamptz not null default now()
);

-- ---------------------------------------------------------------- knowledge (Phase 2 / 8)
create table public.knowledge_cards (
  id              uuid primary key default gen_random_uuid(),
  asset_id        uuid not null references public.assets(id) on delete cascade,
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  status          public.card_status not null default 'ai_generated',
  title           text not null,
  short_description text,
  summary         text,
  category_id     uuid references public.categories(id) on delete set null,
  subcategory_id  uuid references public.categories(id) on delete set null,
  confidence      real,
  model_name      text,
  prompt_version  text,
  tsv             tsvector generated always as (
                    to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(short_description,'') || ' ' || coalesce(summary,''))) stored,
  row_version     bigint not null default 1,
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now(),
  unique (asset_id)
);
create index knowledge_cards_tsv on public.knowledge_cards using gin (tsv);

create table public.knowledge_card_entities (
  card_id   uuid not null references public.knowledge_cards(id) on delete cascade,
  entity_id uuid not null references public.extracted_entities(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  primary key (card_id, entity_id)
);

create table public.concepts (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  name         text not null,
  slug         text not null,
  unique (workspace_id, slug)
);

-- Graph edges. Polymorphic by design (asset|concept|collection|person|card); integrity enforced in API.
create table public.knowledge_relationships (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  from_type    text not null check (from_type in ('asset','concept','collection','person','card')),
  from_id      uuid not null,
  to_type      text not null check (to_type in ('asset','concept','collection','person','card')),
  to_id        uuid not null,
  relation     text not null,     -- 'related_to','mentions','depicts','part_of', ...
  weight       real,
  origin       public.metadata_origin not null,
  created_at   timestamptz not null default now(),
  unique (workspace_id, from_type, from_id, to_type, to_id, relation)
);
create index on public.knowledge_relationships (workspace_id, to_type, to_id);

-- ---------------------------------------------------------------- duplicates
create table public.duplicate_groups (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  level        public.duplicate_level not null,
  status       public.group_status not null default 'open',
  created_at   timestamptz not null default now(),
  resolved_by  uuid references public.users(id),
  resolved_at  timestamptz
);
create index on public.duplicate_groups (workspace_id, status);

create table public.duplicate_group_members (
  group_id     uuid not null references public.duplicate_groups(id) on delete cascade,
  asset_id     uuid not null references public.assets(id) on delete cascade,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  similarity   real not null,
  suggested_keep boolean not null default false,
  decision     text check (decision in ('keep','hide')),   -- 'hide' = soft-hide in ImageVault only
  primary key (group_id, asset_id)
);

-- ---------------------------------------------------------------- jobs & sync
create table public.processing_jobs (
  id              bigint generated always as identity primary key,
  workspace_id    uuid not null references public.workspaces(id) on delete cascade,
  asset_id        uuid references public.assets(id) on delete cascade,
  stage           public.job_stage not null,
  status          public.job_status not null default 'queued',
  priority        smallint not null default 100,     -- lower runs first
  pipeline_version text not null,
  idempotency_key text not null unique,              -- e.g. asset:<id>:ocr:<pipeline_version>
  attempts        smallint not null default 0,
  max_attempts    smallint not null default 5,
  run_after       timestamptz not null default now(),
  locked_by       text,
  heartbeat_at    timestamptz,
  last_error      text,
  executor        text,          -- 'cloud' | 'device:<uuid>'
  model_name      text,
  cost_usd        numeric(10,6),
  created_at      timestamptz not null default now(),
  started_at      timestamptz,
  finished_at     timestamptz
);
create index processing_jobs_ready on public.processing_jobs (priority, run_after, id) where status = 'queued';
create index on public.processing_jobs (workspace_id, status);
create index on public.processing_jobs (asset_id);

create table public.sync_jobs (
  id            bigint generated always as identity primary key,
  workspace_id  uuid not null references public.workspaces(id) on delete cascade,
  connection_id uuid not null references public.storage_connections(id) on delete cascade,
  kind          text not null check (kind in ('full_scan','incremental','upload_batch')),
  status        public.job_status not null default 'queued',
  cursor_before text, cursor_after text,
  items_seen int not null default 0, items_new int not null default 0,
  items_changed int not null default 0, items_missing int not null default 0,
  last_error    text,
  started_at    timestamptz, finished_at timestamptz,
  created_at    timestamptz not null default now()
);
create index on public.sync_jobs (connection_id, created_at desc);

create table public.change_log (                    -- ordered feed for device pull-sync
  seq          bigint generated always as identity primary key,
  workspace_id uuid not null references public.workspaces(id) on delete cascade,
  entity       text not null,
  entity_id    uuid not null,
  op           text not null check (op in ('upsert','delete')),
  row_version  bigint,
  origin_device uuid,
  changed_at   timestamptz not null default now()
);
create index on public.change_log (workspace_id, seq);

create table public.audit_logs (                    -- append-only
  id            bigint generated always as identity primary key,
  workspace_id  uuid references public.workspaces(id) on delete set null,
  actor_user_id uuid references public.users(id) on delete set null,
  actor_device_id uuid,
  action        text not null,    -- 'asset.hide','duplicate.resolve','face.purge','share.create', ...
  entity        text not null,
  entity_id     text,
  before        jsonb,
  after         jsonb,            -- sensitive entity values are redacted before insert
  created_at    timestamptz not null default now()
);
create index on public.audit_logs (workspace_id, created_at desc);

-- ---------------------------------------------------------------- helpers & RLS
create or replace function private.is_member(ws uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.memberships m where m.workspace_id = ws and m.user_id = auth.uid());
$$;

create or replace function private.can_write(ws uuid) returns boolean
language sql stable security definer set search_path = '' as $$
  select exists (select 1 from public.memberships m
                 where m.workspace_id = ws and m.user_id = auth.uid()
                   and m.role in ('owner','admin','editor'));
$$;

-- Generic policies for every tenant table that has workspace_id.
do $$
declare t text;
begin
  foreach t in array array['devices','consents','storage_connections','assets','storage_locations','asset_versions',
    'asset_derivatives','asset_metadata','asset_regions','asset_embeddings','asset_descriptions','extracted_text',
    'text_regions','extracted_entities','tags','asset_tags','asset_categories','people','face_clusters','asset_faces',
    'collections','collection_assets','collection_shares','asset_notes','saved_searches','knowledge_cards',
    'knowledge_card_entities','concepts','knowledge_relationships','duplicate_groups','duplicate_group_members',
    'processing_jobs','sync_jobs','change_log']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('create policy %I on public.%I for select to authenticated using (private.is_member(workspace_id))', t||'_read', t);
    execute format('create policy %I on public.%I for insert to authenticated with check (private.can_write(workspace_id))', t||'_insert', t);
    execute format('create policy %I on public.%I for update to authenticated using (private.can_write(workspace_id)) with check (private.can_write(workspace_id))', t||'_update', t);
    execute format('create policy %I on public.%I for delete to authenticated using (private.can_write(workspace_id))', t||'_delete', t);
  end loop;
end $$;

-- Tighter rules on top of the generic set:
-- jobs, change_log, sync_jobs, embeddings are written by the service role only (bypasses RLS).
drop policy processing_jobs_insert on public.processing_jobs;  drop policy processing_jobs_update on public.processing_jobs;
drop policy processing_jobs_delete on public.processing_jobs;
drop policy change_log_insert on public.change_log; drop policy change_log_update on public.change_log; drop policy change_log_delete on public.change_log;
drop policy sync_jobs_insert on public.sync_jobs; drop policy sync_jobs_update on public.sync_jobs; drop policy sync_jobs_delete on public.sync_jobs;
drop policy asset_embeddings_insert on public.asset_embeddings; drop policy asset_embeddings_update on public.asset_embeddings;
-- saved_searches and devices are private to their owner.
drop policy saved_searches_read on public.saved_searches;
create policy saved_searches_read_own on public.saved_searches for select to authenticated
  using (user_id = auth.uid() and private.is_member(workspace_id));
drop policy devices_read on public.devices;
create policy devices_read_own on public.devices for select to authenticated using (user_id = auth.uid());

alter table public.users enable row level security;
create policy users_self on public.users for select to authenticated using (id = auth.uid());
create policy users_self_update on public.users for update to authenticated using (id = auth.uid());

alter table public.user_preferences enable row level security;
create policy prefs_self on public.user_preferences for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

alter table public.workspaces enable row level security;
create policy workspaces_read on public.workspaces for select to authenticated using (private.is_member(id));

alter table public.memberships enable row level security;
create policy memberships_read on public.memberships for select to authenticated using (private.is_member(workspace_id));

alter table public.organizations enable row level security;
create policy organizations_read on public.organizations for select to authenticated
  using (exists (select 1 from public.workspaces w where w.organization_id = id and private.is_member(w.id)));

alter table public.embedding_models enable row level security;
create policy embedding_models_read on public.embedding_models for select to authenticated using (true);

alter table public.categories enable row level security;
create policy categories_read on public.categories for select to authenticated
  using (workspace_id is null or private.is_member(workspace_id));
create policy categories_write on public.categories for all to authenticated
  using (workspace_id is not null and private.can_write(workspace_id))
  with check (workspace_id is not null and private.can_write(workspace_id));

alter table public.audit_logs enable row level security;   -- read for admins; writes via service role only
create policy audit_read on public.audit_logs for select to authenticated using (
  exists (select 1 from public.memberships m where m.workspace_id = audit_logs.workspace_id
          and m.user_id = auth.uid() and m.role in ('owner','admin')));
revoke update, delete on public.audit_logs from authenticated, anon;

-- ---------------------------------------------------------------- bootstrap on sign-up
create or replace function private.handle_new_user() returns trigger
language plpgsql security definer set search_path = '' as $$
declare org uuid; ws uuid;
begin
  insert into public.users (id, display_name) values (new.id, coalesce(new.raw_user_meta_data->>'full_name', new.email));
  insert into public.organizations (name, owner_id) values ('Personal', new.id) returning id into org;
  insert into public.workspaces (organization_id, kind, name, created_by) values (org, 'personal', 'My Vault', new.id) returning id into ws;
  insert into public.memberships (workspace_id, user_id, role) values (ws, new.id, 'owner');
  return new;
end $$;
create trigger on_auth_user_created after insert on auth.users
  for each row execute function private.handle_new_user();

-- ---------------------------------------------------------------- seed
insert into public.embedding_models (id, name, version, modality, dims, is_active)
values (1, 'open_clip:ViT-B-32:laion2b_s34b_b79k', '1', 'image', 512, true);
