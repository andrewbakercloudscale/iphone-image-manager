-- An index on where each cloud copy lives.
--
-- Every upload plan asks, per file, whether another asset already owns its
-- remote path (`cloud._remote_path_owner`). Without this each question read the
-- whole table: 19,184 waiting WhatsApp photos took about 15 minutes of silence
-- before the first byte went up, and the app showed nothing happening.
CREATE INDEX IF NOT EXISTS idx_assets_cloud_path ON assets (cloud_path);
