-- 0003 (B6): which scan run last saw each location. A complete scan marks every location it did
-- not see as missing. Comparing timestamps is not exact (two runs can start in the same
-- millisecond); the run id is.
ALTER TABLE file_locations ADD COLUMN seen_run_id INTEGER;
