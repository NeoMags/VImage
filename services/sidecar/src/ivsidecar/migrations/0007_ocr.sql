-- 0007 (K2): OCR text for every image. Existing photos are queued once here (screenshots-looking
-- PNGs first); new photos are queued by the engine after their thumbnail. Jobs are idempotent per
-- (asset, stage, pipeline version), so running this on a library that already has OCR jobs adds none.
CREATE INDEX extracted_text_asset ON extracted_text (asset_id, origin);

INSERT INTO jobs (asset_id, stage, priority, pipeline_version)
  SELECT a.id, 'ocr', CASE WHEN a.mime_type = 'image/png' THEN 120 ELSE 130 END, '1'
  FROM assets a
  WHERE a.status = 'ready' AND a.kind = 'image'
    AND EXISTS (SELECT 1 FROM derivatives d WHERE d.asset_id = a.id AND d.kind = 'thumb_512')
  ON CONFLICT (asset_id, stage, pipeline_version) WHERE asset_id IS NOT NULL DO NOTHING;
