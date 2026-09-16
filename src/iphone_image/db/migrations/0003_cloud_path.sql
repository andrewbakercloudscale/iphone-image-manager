-- Where in the cloud a verified copy lives.
--
-- cloud_status said whether a copy existed but never where, so a reconcile
-- could prove an asset was uploaded and still not say what to look at. It also
-- makes the release step possible: an archive file cannot be let go until the
-- ledger can name the copy that replaces it.
ALTER TABLE assets ADD COLUMN cloud_path TEXT;
