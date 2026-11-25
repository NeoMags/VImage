-- 0004 (B8): indexes for browsing the library, and the full-text index filled for existing photos.
-- * file_modified_at held the raw mtime in nanoseconds; it becomes local wall-clock time
--   (YYYY-MM-DDTHH:MM:SS), the same form as taken_at, so the two can be sorted together.
-- * sort_date: date taken, else the file's modified date, else when it was added (decision 4).
--   A VIRTUAL generated column costs no storage; the indexes below make every sort a range scan.
-- * asset_fts: file paths, title, tags and notes, one row per asset (rowid = asset id).

UPDATE assets SET file_modified_at = strftime('%Y-%m-%dT%H:%M:%S', CAST(file_modified_at AS INTEGER) / 1e9, 'unixepoch', 'localtime')
  WHERE file_modified_at GLOB '[0-9]*' AND file_modified_at NOT GLOB '*[^0-9]*';

ALTER TABLE assets ADD COLUMN sort_date TEXT GENERATED ALWAYS AS (coalesce(taken_at, file_modified_at, indexed_at)) VIRTUAL;

DROP INDEX assets_taken;
DROP INDEX assets_indexed;
CREATE INDEX assets_by_date    ON assets (is_hidden, sort_date, id);
CREATE INDEX assets_by_indexed ON assets (is_hidden, indexed_at, id);
CREATE INDEX assets_by_size    ON assets (is_hidden, byte_size, id);
CREATE INDEX file_locations_asset_state ON file_locations (asset_id, state);
CREATE INDEX notes_asset_time ON notes (asset_id, created_at);
DROP INDEX notes_asset;

DELETE FROM asset_fts;
INSERT INTO asset_fts (rowid, filename, title, tags, notes)
  SELECT a.id,
         (SELECT group_concat(rel_path, ' ') FROM file_locations WHERE asset_id = a.id),
         a.title,
         (SELECT group_concat(t.name, ' ') FROM asset_tags at JOIN tags t ON t.id = at.tag_id
           WHERE at.asset_id = a.id AND at.rejected = 0),
         (SELECT group_concat(body, ' ') FROM notes WHERE asset_id = a.id)
  FROM assets a;
