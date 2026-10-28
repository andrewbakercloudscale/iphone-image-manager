-- An extra copy in Google Photos, for looking at.
--
-- Recorded so the same item is not sent twice and so the app can say what is
-- there. It is not evidence of anything: Google Photos re-encodes what it is
-- sent and its API reports no hash, so a SENT here proves only that rclone
-- said it uploaded the file. Release and removal never read these columns;
-- they check Google Drive alone (docs/SAFETY.md).
--
-- gphotos_status is 'SENT' or NULL. NULL means not sent, never "not wanted".
ALTER TABLE assets ADD COLUMN gphotos_status TEXT;
ALTER TABLE assets ADD COLUMN gphotos_album TEXT;
ALTER TABLE assets ADD COLUMN gphotos_at TEXT;

CREATE INDEX idx_assets_gphotos_status ON assets (gphotos_status);
