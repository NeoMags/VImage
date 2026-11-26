-- 0008 (K3): what kind of picture a photo is (screenshot, scan or camera photo), and fast lookup of
-- the things found in its text by their line box.
INSERT INTO categories (domain, parent_id, slug, name, is_system)
  SELECT 'source_type', NULL, v.slug, v.name, 1
  FROM (SELECT 'screenshot' AS slug, 'Screenshot' AS name UNION ALL SELECT 'scan', 'Scan'
        UNION ALL SELECT 'photo', 'Photo') v
  WHERE NOT EXISTS (SELECT 1 FROM categories c WHERE c.domain = 'source_type' AND c.slug = v.slug);
CREATE INDEX entities_region ON entities (text_region_id);
