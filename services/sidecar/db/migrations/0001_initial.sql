-- ImageVault local library schema (SQLite 3.45+ with FTS5 and sqlite-vec 0.1.x).
-- One library = one SQLite file (default %LOCALAPPDATA%\ImageVault\library.db). Single user, no tenancy.
-- The sidecar is the only process that opens this file. Applied with PRAGMA user_version = 1.
-- Conventions:
--   * INTEGER PRIMARY KEY for speed; `uid` (UUIDv7 text) is the stable id kept for any future sync/export.
--   * origin: 'source' | 'ai' | 'user'. Pipeline never overwrites origin='user' rows or re-adds rejected ones.
--   * Original files are never written, moved or deleted by ImageVault.

PRAGMA foreign_keys = ON;

-- ------------------------------------------------------------ settings & models
CREATE TABLE settings (                      -- user_preferences equivalent
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL                        -- JSON
) STRICT;

CREATE TABLE embedding_models (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,                   -- 'open_clip:ViT-B-32:laion2b_s34b_b79k'
  version   TEXT NOT NULL,
  modality  TEXT NOT NULL CHECK (modality IN ('image','text','face')),
  dims      INTEGER NOT NULL,
  is_active INTEGER NOT NULL DEFAULT 0 CHECK (is_active IN (0,1)),
  UNIQUE (name, version, modality)
) STRICT;

-- ------------------------------------------------------------ sources
CREATE TABLE sources (                       -- storage_connections equivalent
  id             INTEGER PRIMARY KEY,
  uid            TEXT NOT NULL UNIQUE,
  kind           TEXT NOT NULL CHECK (kind IN ('local_folder','external_drive','network_share','takeout_import','clipboard','drag_drop')),
  display_name   TEXT NOT NULL,
  root_path      TEXT NOT NULL,              -- as configured; drive letter may change for removable drives
  volume_id      TEXT,                       -- volume serial/GUID; used to re-find external drives
  status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','paused','unreachable','removed')),
  include_globs  TEXT NOT NULL DEFAULT '[]', -- JSON arrays
  exclude_globs  TEXT NOT NULL DEFAULT '[]',
  watch          INTEGER NOT NULL DEFAULT 1 CHECK (watch IN (0,1)),
  process_faces  INTEGER NOT NULL DEFAULT 0 CHECK (process_faces IN (0,1)),
  last_scan_at   TEXT,
  last_error     TEXT,
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

-- ------------------------------------------------------------ assets
CREATE TABLE assets (                        -- one row per unique content (sha256)
  id                 INTEGER PRIMARY KEY,
  uid                TEXT NOT NULL UNIQUE,
  sha256             BLOB NOT NULL UNIQUE CHECK (length(sha256) = 32),
  phash              BLOB CHECK (phash IS NULL OR length(phash) = 8),
  kind               TEXT NOT NULL DEFAULT 'image' CHECK (kind IN ('image','screenshot','video','pdf','drawing','document','other')),
  status             TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','ready','failed','unsupported')),
  mime_type          TEXT NOT NULL,
  byte_size          INTEGER NOT NULL CHECK (byte_size >= 0),
  width              INTEGER, height INTEGER, duration_ms INTEGER,
  title              TEXT,
  title_origin       TEXT CHECK (title_origin IN ('source','ai','user')),
  taken_at           TEXT,                   -- ISO 8601
  taken_at_origin    TEXT CHECK (taken_at_origin IN ('source','ai','user')),
  file_created_at    TEXT,
  file_modified_at   TEXT,
  indexed_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  is_favorite        INTEGER NOT NULL DEFAULT 0 CHECK (is_favorite IN (0,1)),
  is_reviewed        INTEGER NOT NULL DEFAULT 0 CHECK (is_reviewed IN (0,1)),
  is_hidden          INTEGER NOT NULL DEFAULT 0 CHECK (is_hidden IN (0,1)),   -- hidden in ImageVault only
  exclude_faces      INTEGER NOT NULL DEFAULT 0 CHECK (exclude_faces IN (0,1)),
  error              TEXT,
  updated_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE INDEX assets_taken   ON assets (taken_at DESC, id) WHERE is_hidden = 0;
CREATE INDEX assets_indexed ON assets (indexed_at DESC, id) WHERE is_hidden = 0;
CREATE INDEX assets_kind    ON assets (kind, taken_at DESC);
CREATE INDEX assets_status  ON assets (status);

CREATE TABLE file_locations (               -- storage_locations: every place this content exists
  id               INTEGER PRIMARY KEY,
  source_id        INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  asset_id         INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  file_key         TEXT NOT NULL,            -- NTFS volume serial + file id; path+size+mtime for shares
  rel_path         TEXT NOT NULL,            -- relative to source root
  state            TEXT NOT NULL DEFAULT 'present' CHECK (state IN ('present','moved','missing','unreachable')),
  size_bytes       INTEGER NOT NULL,
  mtime            TEXT NOT NULL,
  first_seen_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  last_seen_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (source_id, file_key)
) STRICT;
CREATE INDEX file_locations_asset ON file_locations (asset_id);
CREATE INDEX file_locations_path  ON file_locations (source_id, rel_path);

CREATE TABLE asset_versions (                -- previous content seen at the same location
  id         INTEGER PRIMARY KEY,
  asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  sha256     BLOB NOT NULL,
  byte_size  INTEGER NOT NULL,
  seen_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE derivatives (                   -- thumbnails/previews in the library cache folder
  asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('thumb_256','thumb_512','preview_1600','video_poster')),
  rel_path   TEXT NOT NULL,                  -- relative to cache dir
  width INTEGER NOT NULL, height INTEGER NOT NULL, byte_size INTEGER NOT NULL,
  PRIMARY KEY (asset_id, kind)
) STRICT, WITHOUT ROWID;

CREATE TABLE asset_metadata (                -- original (source) metadata only
  asset_id       INTEGER PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
  camera_make TEXT, camera_model TEXT, lens TEXT,
  iso INTEGER, exposure_s REAL, f_number REAL, focal_length_mm REAL,
  orientation INTEGER, color_profile TEXT,
  gps_lat REAL CHECK (gps_lat BETWEEN -90 AND 90),
  gps_lon REAL CHECK (gps_lon BETWEEN -180 AND 180),
  gps_alt_m REAL,
  raw_exif       TEXT,                       -- JSON from ExifTool
  extractor      TEXT NOT NULL,
  extracted_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE regions (
  id         INTEGER PRIMARY KEY,
  asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  kind       TEXT NOT NULL CHECK (kind IN ('face','object','text_block','user_selection')),
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,   -- normalized 0..1
  label      TEXT, confidence REAL,
  origin     TEXT NOT NULL CHECK (origin IN ('source','ai','user')),
  CHECK (x >= 0 AND y >= 0 AND w > 0 AND h > 0 AND x + w <= 1.0001 AND y + h <= 1.0001)
) STRICT;
CREATE INDEX regions_asset ON regions (asset_id, kind);

-- ------------------------------------------------------------ vectors (sqlite-vec)
-- Embedding row metadata; vectors live in per-model vec0 tables keyed by embeddings.id.
CREATE TABLE embeddings (
  id        INTEGER PRIMARY KEY,
  asset_id  INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  model_id  INTEGER NOT NULL REFERENCES embedding_models(id),
  region_id INTEGER REFERENCES regions(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE UNIQUE INDEX embeddings_unique ON embeddings (asset_id, model_id, ifnull(region_id, 0));
CREATE VIRTUAL TABLE vec_clip_b32 USING vec0(embedding float[512] distance_metric=cosine);   -- rowid = embeddings.id
CREATE VIRTUAL TABLE vec_phash    USING vec0(phash bit[64]);                                 -- rowid = assets.id
CREATE TRIGGER embeddings_del AFTER DELETE ON embeddings BEGIN
  DELETE FROM vec_clip_b32 WHERE rowid = old.id;
END;
CREATE TRIGGER assets_del_phash AFTER DELETE ON assets BEGIN
  DELETE FROM vec_phash WHERE rowid = old.id;
END;

-- ------------------------------------------------------------ AI outputs
CREATE TABLE descriptions (
  id         INTEGER PRIMARY KEY,
  asset_id   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  origin     TEXT NOT NULL CHECK (origin IN ('ai','user')),
  model_name TEXT, body TEXT NOT NULL, confidence REAL,
  is_current INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE UNIQUE INDEX descriptions_current ON descriptions (asset_id) WHERE is_current = 1;

CREATE TABLE extracted_text (
  id          INTEGER PRIMARY KEY,
  asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  engine      TEXT NOT NULL, engine_version TEXT NOT NULL,
  language    TEXT,
  full_text   TEXT NOT NULL,
  confidence  REAL,
  origin      TEXT NOT NULL DEFAULT 'ai' CHECK (origin IN ('ai','user')),
  is_current  INTEGER NOT NULL DEFAULT 1,
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE UNIQUE INDEX extracted_text_current ON extracted_text (asset_id) WHERE is_current = 1;

CREATE TABLE text_regions (                  -- OCR line boxes
  id                INTEGER PRIMARY KEY,
  extracted_text_id INTEGER NOT NULL REFERENCES extracted_text(id) ON DELETE CASCADE,
  line_no INTEGER NOT NULL, text TEXT NOT NULL,
  x REAL NOT NULL, y REAL NOT NULL, w REAL NOT NULL, h REAL NOT NULL,
  confidence REAL
) STRICT;
CREATE INDEX text_regions_parent ON text_regions (extracted_text_id, line_no);

CREATE TABLE entities (
  id             INTEGER PRIMARY KEY,
  asset_id       INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  text_region_id INTEGER REFERENCES text_regions(id) ON DELETE SET NULL,
  entity_type    TEXT NOT NULL CHECK (entity_type IN ('url','email','phone','date','address','code','command',
                   'error_message','product','technical_term','person_name','organization','amount','other')),
  value TEXT NOT NULL, normalized_value TEXT, confidence REAL,
  origin TEXT NOT NULL CHECK (origin IN ('ai','user')),
  is_sensitive INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE INDEX entities_lookup ON entities (entity_type, normalized_value);
CREATE INDEX entities_asset  ON entities (asset_id);

-- ------------------------------------------------------------ taxonomy & organization
CREATE TABLE categories (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES categories(id) ON DELETE CASCADE,
  domain TEXT NOT NULL, slug TEXT NOT NULL, name TEXT NOT NULL,
  is_system INTEGER NOT NULL DEFAULT 1,
  UNIQUE (domain, parent_id, slug)
) STRICT;

CREATE TABLE tags (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE asset_tags (
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  tag_id   INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
  origin   TEXT NOT NULL CHECK (origin IN ('source','ai','user')),
  confidence REAL,
  rejected INTEGER NOT NULL DEFAULT 0,       -- user said no: AI must not re-add
  confirmed_at TEXT,
  PRIMARY KEY (asset_id, tag_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX asset_tags_tag ON asset_tags (tag_id);

CREATE TABLE asset_categories (
  asset_id    INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
  origin      TEXT NOT NULL CHECK (origin IN ('ai','user')),
  confidence  REAL,
  rejected    INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (asset_id, category_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX asset_categories_cat ON asset_categories (category_id);

CREATE TABLE collections (
  id        INTEGER PRIMARY KEY,
  uid       TEXT NOT NULL UNIQUE,
  parent_id INTEGER REFERENCES collections(id) ON DELETE CASCADE,
  kind      TEXT NOT NULL DEFAULT 'manual' CHECK (kind IN ('manual','smart')),
  name      TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 200),
  rule      TEXT,                            -- JSON FilterExpression for smart collections
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  CHECK ((kind = 'smart') = (rule IS NOT NULL))
) STRICT;

CREATE TABLE collection_assets (
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  asset_id      INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  position      REAL NOT NULL DEFAULT 0,
  added_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (collection_id, asset_id)
) STRICT, WITHOUT ROWID;
CREATE INDEX collection_assets_asset ON collection_assets (asset_id);

CREATE TABLE notes (
  id INTEGER PRIMARY KEY,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  body TEXT NOT NULL CHECK (length(body) <= 20000),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE INDEX notes_asset ON notes (asset_id);

CREATE TABLE saved_searches (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, query TEXT NOT NULL,   -- JSON SearchRequest
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

-- ------------------------------------------------------------ knowledge
CREATE TABLE knowledge_cards (
  id INTEGER PRIMARY KEY,
  asset_id INTEGER NOT NULL UNIQUE REFERENCES assets(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'ai_generated' CHECK (status IN ('ai_generated','user_reviewed')),
  title TEXT NOT NULL, short_description TEXT, summary TEXT,
  category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  subcategory_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
  confidence REAL, model_name TEXT, prompt_version TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;

CREATE TABLE knowledge_card_entities (
  card_id INTEGER NOT NULL REFERENCES knowledge_cards(id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
  PRIMARY KEY (card_id, entity_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE concepts (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE
) STRICT;

CREATE TABLE relationships (                 -- knowledge graph edges (polymorphic; validated in sidecar)
  id INTEGER PRIMARY KEY,
  from_type TEXT NOT NULL CHECK (from_type IN ('asset','concept','collection','person','card')),
  from_id INTEGER NOT NULL,
  to_type TEXT NOT NULL CHECK (to_type IN ('asset','concept','collection','person','card')),
  to_id INTEGER NOT NULL,
  relation TEXT NOT NULL, weight REAL,
  origin TEXT NOT NULL CHECK (origin IN ('ai','user')),
  UNIQUE (from_type, from_id, to_type, to_id, relation)
) STRICT;
CREATE INDEX relationships_to ON relationships (to_type, to_id);

-- ------------------------------------------------------------ people (opt-in, later phase)
CREATE TABLE people (
  id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, notes TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE TABLE face_clusters (
  id INTEGER PRIMARY KEY,
  person_id INTEGER REFERENCES people(id) ON DELETE SET NULL,   -- set only by a user action
  member_count INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TABLE asset_faces (
  id INTEGER PRIMARY KEY,
  asset_id  INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  region_id INTEGER NOT NULL REFERENCES regions(id) ON DELETE CASCADE,
  cluster_id INTEGER REFERENCES face_clusters(id) ON DELETE SET NULL,
  person_id  INTEGER REFERENCES people(id) ON DELETE SET NULL,
  confirmed_at TEXT,
  model_id INTEGER NOT NULL REFERENCES embedding_models(id),
  CHECK (person_id IS NULL OR confirmed_at IS NOT NULL)          -- identity only with explicit confirmation
) STRICT;

-- ------------------------------------------------------------ duplicates
CREATE TABLE duplicate_groups (
  id INTEGER PRIMARY KEY,
  level TEXT NOT NULL CHECK (level IN ('exact','perceptual','visual')),
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved','dismissed')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  resolved_at TEXT
) STRICT;
CREATE TABLE duplicate_group_members (
  group_id INTEGER NOT NULL REFERENCES duplicate_groups(id) ON DELETE CASCADE,
  asset_id INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  similarity REAL NOT NULL,
  suggested_keep INTEGER NOT NULL DEFAULT 0,
  decision TEXT CHECK (decision IN ('keep','hide')),            -- never 'delete'
  PRIMARY KEY (group_id, asset_id)
) STRICT, WITHOUT ROWID;

-- ------------------------------------------------------------ jobs & scans
CREATE TABLE jobs (                          -- processing_jobs: persistent local queue
  id INTEGER PRIMARY KEY,
  asset_id INTEGER REFERENCES assets(id) ON DELETE CASCADE,
  stage TEXT NOT NULL CHECK (stage IN ('identify','metadata','hash','thumbnail','ocr','classify','embed',
                                       'entities','describe','knowledge','faces','duplicates')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','skipped','dead')),
  priority INTEGER NOT NULL DEFAULT 100,     -- lower runs first; 10 = user is looking at it
  pipeline_version TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
  run_after TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  last_error TEXT, engine TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  started_at TEXT, finished_at TEXT,
  UNIQUE (asset_id, stage, pipeline_version)  -- idempotency
) STRICT;
CREATE INDEX jobs_ready ON jobs (priority, run_after, id) WHERE status = 'queued';

CREATE TABLE scan_runs (                     -- sync_jobs equivalent for local sources
  id INTEGER PRIMARY KEY,
  source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('full','incremental','watch_batch')),
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','succeeded','failed','cancelled')),
  files_seen INTEGER NOT NULL DEFAULT 0, files_new INTEGER NOT NULL DEFAULT 0,
  files_changed INTEGER NOT NULL DEFAULT 0, files_missing INTEGER NOT NULL DEFAULT 0,
  resume_token TEXT,                         -- last directory completed, for resume after restart
  last_error TEXT,
  started_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), finished_at TEXT
) STRICT;

CREATE TABLE audit_log (                     -- append-only
  id INTEGER PRIMARY KEY,
  action TEXT NOT NULL, entity TEXT NOT NULL, entity_id TEXT,
  before TEXT, after TEXT,                   -- JSON; sensitive entity values redacted
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
CREATE TRIGGER audit_log_no_update BEFORE UPDATE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;
CREATE TRIGGER audit_log_no_delete BEFORE DELETE ON audit_log BEGIN SELECT RAISE(ABORT, 'audit_log is append-only'); END;

-- ------------------------------------------------------------ full-text search (FTS5)
-- One document per asset: filename(s), title, tags, notes, OCR text, description, card. Maintained by the sidecar.
CREATE VIRTUAL TABLE asset_fts USING fts5(
  filename, title, tags, notes, ocr_text, description, card,
  tokenize = 'unicode61 remove_diacritics 2'
);                                           -- rowid = assets.id
-- Trigram index for exact substrings (error codes, phone numbers, partial words).
CREATE VIRTUAL TABLE ocr_trigram USING fts5(ocr_text, tokenize = 'trigram');   -- rowid = assets.id
CREATE TRIGGER assets_del_fts AFTER DELETE ON assets BEGIN
  DELETE FROM asset_fts WHERE rowid = old.id;
  DELETE FROM ocr_trigram WHERE rowid = old.id;
END;

-- ------------------------------------------------------------ seed
INSERT INTO embedding_models (id, name, version, modality, dims, is_active)
VALUES (1, 'open_clip:ViT-B-32:laion2b_s34b_b79k', '1', 'image', 512, 1);

PRAGMA user_version = 1;
