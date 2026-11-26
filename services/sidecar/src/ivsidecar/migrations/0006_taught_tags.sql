-- 0006 (T5): tags you taught by example. A taught tag remembers the description's CLIP vector and
-- the score of the weakest match you accepted; new photos at or above it get the tag (origin ai).
CREATE TABLE taught_tags (
  tag_id     INTEGER PRIMARY KEY REFERENCES tags(id) ON DELETE CASCADE,
  prompt     TEXT NOT NULL,
  vector     BLOB NOT NULL,              -- float32[512], CLIP text vector of the prompt
  threshold  REAL NOT NULL CHECK (threshold BETWEEN -1 AND 1),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
) STRICT;
