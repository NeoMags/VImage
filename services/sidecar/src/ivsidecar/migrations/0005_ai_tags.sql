-- 0005 (T3): "Is this tag yours?" must be cheap for the filter panel, which splits tags into
-- yours (from you, from a file, or an AI tag you accepted) and AI tags. Few rows qualify, so the
-- partial index stays small even with millions of AI tags.
CREATE INDEX asset_tags_mine ON asset_tags (tag_id)
  WHERE rejected = 0 AND (origin != 'ai' OR confirmed_at IS NOT NULL);
