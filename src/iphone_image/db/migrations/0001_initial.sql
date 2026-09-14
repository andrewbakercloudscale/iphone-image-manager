-- iPhone Image Manager, initial schema.
--
-- The database is the ledger, not a cache. Every claim the tool makes about an
-- asset ("backed up", "verified", "safe to remove") must be answerable from a
-- row here, and every destructive action must be recorded before it happens.

CREATE TABLE devices (
    id                      INTEGER PRIMARY KEY,
    ic_uuid                 TEXT UNIQUE,          -- ICDevice.UUIDString
    ic_persistent_id        TEXT,                 -- ICDevice.persistentIDString
    udid                    TEXT,                 -- libimobiledevice, independent identity
    name                    TEXT,
    product_kind            TEXT,
    serial_number           TEXT,
    ios_version             TEXT,
    icloud_photos_enabled   INTEGER,              -- ICCameraDevice.iCloudPhotosEnabled
    can_delete_one_file     INTEGER,
    capabilities            TEXT,                 -- JSON array, as reported
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

-- One row per scan run. present_on_phone is derived by comparing an asset's
-- last_seen_scan_id against the newest completed scan, so "disappeared from the
-- device" is a fact with a timestamp rather than a guess.
CREATE TABLE scans (
    id                      INTEGER PRIMARY KEY,
    device_id               INTEGER NOT NULL REFERENCES devices(id),
    started_at              TEXT NOT NULL,
    finished_at             TEXT,
    status                  TEXT NOT NULL,        -- RUNNING | COMPLETED | FAILED | ABORTED
    media_presentation      TEXT,                 -- original | converted, what was in effect
    assets_seen             INTEGER NOT NULL DEFAULT 0,
    bytes_seen              INTEGER NOT NULL DEFAULT 0,
    error                   TEXT
);

-- A logical asset may be several files: HEIC + MOV + AAE, or RAW + JPEG.
CREATE TABLE asset_groups (
    id                      INTEGER PRIMARY KEY,
    device_id               INTEGER NOT NULL REFERENCES devices(id),
    group_uuid              TEXT,
    kind                    TEXT NOT NULL,        -- LIVE_PHOTO | BURST | RAW_PAIR | EDITED | SINGLE
    member_count            INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE (device_id, group_uuid)
);

CREATE TABLE locations (
    id                      INTEGER PRIMARY KEY,
    lat_rounded             REAL NOT NULL,        -- rounded, so nearby photos share one lookup
    lon_rounded             REAL NOT NULL,
    country                 TEXT,
    region                  TEXT,
    city                    TEXT,
    suburb                  TEXT,
    source                  TEXT NOT NULL,        -- which geocoder answered
    looked_up_at            TEXT NOT NULL,
    UNIQUE (lat_rounded, lon_rounded)
);

CREATE TABLE duplicate_groups (
    id                      INTEGER PRIMARY KEY,
    sha256                  TEXT NOT NULL UNIQUE,
    content_size            INTEGER NOT NULL,
    canonical_asset_id      INTEGER,              -- set after the group is resolved
    member_count            INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE campaigns (
    id                      INTEGER PRIMARY KEY,
    device_id               INTEGER NOT NULL REFERENCES devices(id),
    name                    TEXT NOT NULL,
    status                  TEXT NOT NULL,        -- OPEN | CLOSED
    started_at              TEXT NOT NULL,
    closed_at               TEXT,
    initial_asset_count     INTEGER NOT NULL DEFAULT 0,
    notes                   TEXT
);

CREATE TABLE assets (
    id                      INTEGER PRIMARY KEY,
    device_id               INTEGER NOT NULL REFERENCES devices(id),

    -- Identity. device_asset_id is ICCameraFile.originatingAssetID where the
    -- device supplies one. identity_key is that, or the device path when it does
    -- not, and is what makes a rescan an update rather than a duplicate row.
    identity_key            TEXT NOT NULL,
    device_asset_id         TEXT,
    device_path             TEXT,
    ic_object_handle        INTEGER,              -- ptpObjectHandle: NOT stable across sessions

    filename                TEXT,
    original_filename       TEXT,
    created_filename        TEXT,

    media_type              TEXT,                 -- PHOTO | VIDEO | AUDIO | OTHER
    uti                     TEXT,
    mime_type               TEXT,

    created_at_device       TEXT,
    modified_at_device      TEXT,
    exif_created_at         TEXT,
    file_created_at         TEXT,

    size_bytes              INTEGER,
    width                   INTEGER,
    height                  INTEGER,
    duration_seconds        REAL,

    sha256                  TEXT,
    ic_fingerprint          TEXT,                 -- device-computed, may allow dedupe with no download

    latitude                REAL,
    longitude               REAL,
    gps_string              TEXT,
    location_id             INTEGER REFERENCES locations(id),

    camera_make             TEXT,
    camera_model            TEXT,
    lens                    TEXT,

    -- Not available over USB. Kept so the absence is explicit in the schema
    -- rather than looking like an oversight. See docs/PLAN.md conflict 4.
    source_application      TEXT,
    source_bundle_id        TEXT,
    album_names             TEXT,

    classification          TEXT,
    classification_confidence TEXT,               -- HIGH | MEDIUM | LOW | UNKNOWN

    asset_group_id          INTEGER REFERENCES asset_groups(id),
    group_uuid              TEXT,
    burst_uuid              TEXT,
    related_uuid            TEXT,
    is_raw                  INTEGER NOT NULL DEFAULT 0,
    first_picked            INTEGER NOT NULL DEFAULT 0,
    burst_picked            INTEGER NOT NULL DEFAULT 0,
    burst_favorite          INTEGER NOT NULL DEFAULT 0,

    -- Which presentation this record was captured under. A row captured as
    -- 'converted' describes a transcode, not the original, and must never
    -- satisfy a verification requirement. See docs/SAFETY.md section 2b.
    media_presentation      TEXT,

    proxy_suspicion         REAL NOT NULL DEFAULT 0,
    proxy_evidence          TEXT,                 -- JSON array of the signals that fired

    present_on_phone        INTEGER NOT NULL DEFAULT 1,
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    last_seen_scan_id       INTEGER REFERENCES scans(id),

    local_status            TEXT NOT NULL DEFAULT 'DISCOVERED',
    local_path              TEXT,
    local_verified_at       TEXT,

    cloud_status            TEXT NOT NULL DEFAULT 'NONE',
    cloud_verified_at       TEXT,

    duplicate_group_id      INTEGER REFERENCES duplicate_groups(id),
    campaign_id             INTEGER REFERENCES campaigns(id),

    removed_from_phone_at   TEXT,

    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,

    UNIQUE (device_id, identity_key)
);

-- The individual files behind one logical asset. Group-aware removal reads this
-- table: an asset is only removable when every required resource is verified.
CREATE TABLE asset_resources (
    id                      INTEGER PRIMARY KEY,
    asset_id                INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    resource_type           TEXT NOT NULL,        -- PRIMARY_IMAGE | LIVE_PHOTO_VIDEO | RAW | SIDECAR | ...
    required                INTEGER NOT NULL DEFAULT 1,
    device_path             TEXT,
    filename                TEXT,
    size_bytes              INTEGER,
    sha256                  TEXT,
    local_path              TEXT,
    local_status            TEXT NOT NULL DEFAULT 'DISCOVERED',
    local_verified_at       TEXT,
    cloud_status            TEXT NOT NULL DEFAULT 'NONE',
    cloud_verified_at       TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    UNIQUE (asset_id, resource_type, filename)
);

-- Every classifier's verdict, with the evidence that produced it, so any
-- classification shown to a user can be explained rather than asserted.
CREATE TABLE classifications (
    id                      INTEGER PRIMARY KEY,
    asset_id                INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    classifier              TEXT NOT NULL,
    category                TEXT NOT NULL,
    confidence              TEXT NOT NULL,
    evidence                TEXT,                 -- JSON array of strings
    created_at              TEXT NOT NULL,
    UNIQUE (asset_id, classifier)
);

CREATE TABLE cloud_objects (
    id                      INTEGER PRIMARY KEY,
    asset_id                INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    resource_id             INTEGER REFERENCES asset_resources(id) ON DELETE CASCADE,
    provider                TEXT NOT NULL,
    account                 TEXT,
    remote                  TEXT,
    object_id               TEXT,
    path                    TEXT,
    size_bytes              INTEGER,
    hash                    TEXT,
    status                  TEXT NOT NULL DEFAULT 'QUEUED',
    uploaded_at             TEXT,
    verified_at             TEXT,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL
);

CREATE TABLE campaign_assets (
    campaign_id             INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
    asset_id                INTEGER NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
    included_at             TEXT NOT NULL,
    include_reason          TEXT NOT NULL,        -- AT_START | EXPLICIT | INCLUDE_NEW
    eligible_for_removal    INTEGER NOT NULL DEFAULT 0,
    ineligible_reason       TEXT,
    removed_at              TEXT,
    PRIMARY KEY (campaign_id, asset_id)
);

-- Append-only journal. Written before an action and updated after it, so a
-- crash leaves a STARTED row that says what was in flight.
CREATE TABLE operations (
    id                      INTEGER PRIMARY KEY,
    operation               TEXT NOT NULL,        -- SCAN_STARTED, DOWNLOAD_VERIFIED, REMOVE_FAILED, ...
    status                  TEXT NOT NULL,        -- STARTED | COMPLETED | FAILED
    device_id               INTEGER REFERENCES devices(id),
    asset_id                INTEGER REFERENCES assets(id),
    campaign_id             INTEGER REFERENCES campaigns(id),
    scan_id                 INTEGER REFERENCES scans(id),
    command                 TEXT,
    started_at              TEXT NOT NULL,
    finished_at             TEXT,
    duration_ms             INTEGER,
    retry_count             INTEGER NOT NULL DEFAULT 0,
    detail                  TEXT,                 -- JSON
    error                   TEXT
);

-- One row per asset actually removed from a device. Written before the device
-- call, so an interrupted removal is recoverable rather than ambiguous.
CREATE TABLE deletion_events (
    id                      INTEGER PRIMARY KEY,
    asset_id                INTEGER NOT NULL REFERENCES assets(id),
    device_id               INTEGER NOT NULL REFERENCES devices(id),
    campaign_id             INTEGER REFERENCES campaigns(id),
    operation_id            INTEGER REFERENCES operations(id),
    requested_at            TEXT NOT NULL,
    completed_at            TEXT,
    status                  TEXT NOT NULL,        -- REQUESTED | REMOVED | FAILED | ALREADY_GONE
    policy                  TEXT NOT NULL,        -- the policy in force at the time
    evidence                TEXT NOT NULL,        -- JSON: what made it eligible
    size_bytes              INTEGER,
    error                   TEXT
);

-- The Mac-side undo. An entry is written and flushed before the device is told
-- to delete anything. See docs/SAFETY.md section 5.
CREATE TABLE recycle_bin_entries (
    id                      INTEGER PRIMARY KEY,
    asset_id                INTEGER NOT NULL REFERENCES assets(id),
    deletion_event_id       INTEGER REFERENCES deletion_events(id),
    campaign_id             INTEGER REFERENCES campaigns(id),
    entry_path              TEXT NOT NULL,        -- the hard link or copy on disk
    archive_path            TEXT,                 -- where the verified archive copy lives
    manifest                TEXT NOT NULL,        -- JSON snapshot of the asset row
    link_type               TEXT NOT NULL,        -- HARDLINK | COPY
    size_bytes              INTEGER,
    created_at              TEXT NOT NULL,
    expires_at              TEXT,
    restored_at             TEXT,
    purged_at               TEXT
);

CREATE TABLE settings (
    key                     TEXT PRIMARY KEY,
    value                   TEXT,
    updated_at              TEXT NOT NULL
);

-- Indexes: docs/SPEC.md section 47, plus the joins the eligibility query makes.
CREATE INDEX idx_assets_device_asset_id  ON assets (device_asset_id);
CREATE INDEX idx_assets_sha256           ON assets (sha256);
CREATE INDEX idx_assets_fingerprint      ON assets (ic_fingerprint);
CREATE INDEX idx_assets_created_device   ON assets (created_at_device);
CREATE INDEX idx_assets_classification   ON assets (classification, classification_confidence);
CREATE INDEX idx_assets_present          ON assets (present_on_phone);
CREATE INDEX idx_assets_local_status     ON assets (local_status);
CREATE INDEX idx_assets_cloud_status     ON assets (cloud_status);
CREATE INDEX idx_assets_campaign         ON assets (campaign_id);
CREATE INDEX idx_assets_dupe_group       ON assets (duplicate_group_id);
CREATE INDEX idx_assets_group            ON assets (asset_group_id);
CREATE INDEX idx_assets_proxy            ON assets (proxy_suspicion);
CREATE INDEX idx_assets_removed          ON assets (removed_from_phone_at);
CREATE INDEX idx_resources_asset         ON asset_resources (asset_id);
CREATE INDEX idx_resources_status        ON asset_resources (local_status, cloud_status);
CREATE INDEX idx_classifications_asset   ON classifications (asset_id);
CREATE INDEX idx_cloud_objects_asset     ON cloud_objects (asset_id);
CREATE INDEX idx_cloud_objects_status    ON cloud_objects (status);
CREATE INDEX idx_operations_started      ON operations (started_at);
CREATE INDEX idx_operations_asset        ON operations (asset_id);
CREATE INDEX idx_operations_status       ON operations (operation, status);
CREATE INDEX idx_deletion_asset          ON deletion_events (asset_id);
CREATE INDEX idx_recycle_asset           ON recycle_bin_entries (asset_id);
CREATE INDEX idx_recycle_expires         ON recycle_bin_entries (expires_at);
CREATE INDEX idx_campaign_assets_asset   ON campaign_assets (asset_id);
