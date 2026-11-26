-- 0007 (D1): duplicate finder.
-- * Groups use the tables from 0001: level 'perceptual' = Copies (pHash), 'visual' = Similar shots
--   (CLIP + taken close together). 'exact' stays unused: identical files are already one photo
--   with several locations.
-- * duplicate_dismissals: pairs you marked "Not duplicates"; they are never grouped again.
CREATE TABLE duplicate_dismissals (
  asset_lo   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  asset_hi   INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
  level      TEXT NOT NULL CHECK (level IN ('perceptual','visual')),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (asset_lo, asset_hi, level),
  CHECK (asset_lo < asset_hi)
) STRICT, WITHOUT ROWID;
CREATE INDEX duplicate_dismissals_hi ON duplicate_dismissals (asset_hi);

ALTER TABLE duplicate_groups ADD COLUMN updated_at TEXT;
CREATE INDEX duplicate_groups_open ON duplicate_groups (level, status, id);
CREATE INDEX duplicate_group_members_asset ON duplicate_group_members (asset_id);
