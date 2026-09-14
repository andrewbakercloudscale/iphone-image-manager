-- Fields PhotoKit supplies that the USB-era schema never anticipated.
--
-- The original schema was designed against ImageCaptureCore, which exposed
-- almost none of this. See spikes/P0-transport.md for what it did and did not
-- give us, and spikes/P0b-photokit.md for what replaced it.

-- Media subtypes as a JSON array: screenshot, live, panorama, hdr, depthEffect,
-- timelapse, highFrameRate, streamed. The screenshot flag cross-checks against
-- the com.apple.springboard source app, and on a real library the two agreed on
-- 7,679 of 7,681 assets.
ALTER TABLE assets ADD COLUMN subtypes TEXT;

-- Favourites are never removed by default, so this is a protection, not a filter.
ALTER TABLE assets ADD COLUMN is_favourite INTEGER NOT NULL DEFAULT 0;

ALTER TABLE assets ADD COLUMN is_hidden INTEGER NOT NULL DEFAULT 0;

-- PHAssetSourceType: user library, cloud shared, or iTunes synced.
ALTER TABLE assets ADD COLUMN source_type INTEGER;

-- Which scan last saw this asset, so "disappeared from the library" is a fact
-- with a timestamp rather than an inference.
ALTER TABLE assets ADD COLUMN library_path TEXT;

CREATE INDEX idx_assets_subtypes    ON assets (subtypes);
CREATE INDEX idx_assets_favourite   ON assets (is_favourite);
CREATE INDEX idx_assets_source      ON assets (source_bundle_id);
CREATE INDEX idx_assets_media_type  ON assets (media_type);
