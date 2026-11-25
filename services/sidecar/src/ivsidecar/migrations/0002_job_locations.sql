-- 0002 (B5): jobs that run before an asset exists.
-- An asset is defined by its SHA-256, so a newly found file has no asset until it is hashed.
-- * file_locations.asset_id becomes nullable (NULL = found, not hashed yet) and gains mime_type
--   (stage 1 result).
-- * jobs gain location_id: stages 1 (identify) and 3 (hash) belong to a location, later stages
--   to an asset. Exactly one of the two is set. Uniqueness is enforced per owner with partial
--   indexes, because UNIQUE (asset_id, ...) does not catch duplicates when asset_id is NULL.
-- * jobs.crashes counts worker-process crashes; after 2 the job fails instead of looping.
-- SQLite cannot change constraints in place, so both tables are rebuilt (db.py runs migrations
-- with foreign keys off and checks them before commit).

CREATE TABLE file_locations_new (
  id               INTEGER PRIMARY KEY,
  source_id        INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  asset_id         INTEGER REFERENCES assets(id) ON DELETE CASCADE,   -- NULL until hashed
  file_key         TEXT NOT NULL,
  rel_path         TEXT NOT NULL,
  state            TEXT NOT NULL DEFAULT 'present' CHECK (state IN ('present','moved','missing','unreachable')),
  size_bytes       INTEGER NOT NULL,
  mtime            TEXT NOT NULL,
  mime_type        TEXT,                                              -- from stage 1
  first_seen_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  last_seen_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  UNIQUE (source_id, file_key)
) STRICT;
INSERT INTO file_locations_new (id, source_id, asset_id, file_key, rel_path, state, size_bytes, mtime, first_seen_at, last_seen_at)
  SELECT id, source_id, asset_id, file_key, rel_path, state, size_bytes, mtime, first_seen_at, last_seen_at FROM file_locations;
DROP TABLE file_locations;
ALTER TABLE file_locations_new RENAME TO file_locations;
CREATE INDEX file_locations_asset ON file_locations (asset_id);
CREATE INDEX file_locations_path  ON file_locations (source_id, rel_path);

CREATE TABLE jobs_new (
  id INTEGER PRIMARY KEY,
  asset_id    INTEGER REFERENCES assets(id) ON DELETE CASCADE,
  location_id INTEGER REFERENCES file_locations(id) ON DELETE CASCADE,
  stage TEXT NOT NULL CHECK (stage IN ('identify','metadata','hash','thumbnail','ocr','classify','embed',
                                       'entities','describe','knowledge','faces','duplicates')),
  status TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','running','succeeded','failed','skipped','dead')),
  priority INTEGER NOT NULL DEFAULT 100,     -- lower runs first; 10 = user is looking at it
  pipeline_version TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0, max_attempts INTEGER NOT NULL DEFAULT 5,
  crashes  INTEGER NOT NULL DEFAULT 0,
  run_after TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  last_error TEXT, engine TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  started_at TEXT, finished_at TEXT,
  CHECK ((asset_id IS NULL) <> (location_id IS NULL))
) STRICT;
INSERT INTO jobs_new (id, asset_id, stage, status, priority, pipeline_version, attempts, max_attempts, run_after,
                      last_error, engine, created_at, started_at, finished_at)
  SELECT id, asset_id, stage, status, priority, pipeline_version, attempts, max_attempts, run_after,
         last_error, engine, created_at, started_at, finished_at FROM jobs WHERE asset_id IS NOT NULL;
DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;
CREATE UNIQUE INDEX jobs_asset_once    ON jobs (asset_id, stage, pipeline_version)    WHERE asset_id IS NOT NULL;
CREATE UNIQUE INDEX jobs_location_once ON jobs (location_id, stage, pipeline_version) WHERE location_id IS NOT NULL;
CREATE INDEX jobs_ready  ON jobs (priority, run_after, id) WHERE status = 'queued';
CREATE INDEX jobs_status ON jobs (status, stage);
