# iPhone Image Manager
## Complete Product and Technical Specification

**Project name:** iPhone Image Manager
**Publisher:** CloudScale
**Project type:** Open source macOS utility
**Primary purpose:** Safely inventory, clean up, back up, organize, deduplicate, sync, verify, and optionally remove images and videos from an iPhone.

> This document is the product specification as authored. It is deliberately kept
> verbatim as the source of truth for intent. Where implementation reality differs
> from this document, the difference is recorded in `docs/PLAN.md` under
> "Spec conflicts and proposed resolutions" rather than by silently editing here.

---

# 1. Product Summary

iPhone Image Manager is an open source macOS utility for users who want direct control over the images and videos stored on their iPhone without relying on the Apple Photos ecosystem.

The utility connects an iPhone to a Mac, builds a persistent inventory of media on the device, classifies that media, synchronizes it to local storage, optionally uploads it to cloud storage such as Google Drive, verifies that the copies are safe, and only then allows the user to explicitly remove selected media from the iPhone.

The project is designed for large libraries that may take many hours or days to process. Every operation must therefore be resumable, repeatable, and safe to run multiple times.

The central design principle is:

> **Inventory first. Sync second. Verify third. Remove only when explicitly requested.**

The default behavior is always:

> **Keep everything on the iPhone.**

No image or video is removed from the iPhone merely because it was backed up.

---

# 2. Primary Use Cases

The first version should support the following core workflows.

## 2.1 Screenshot cleanup

The user can define a screenshot retention period.

Example:

```yaml
retention:
  screenshots: 60d
```

The system identifies screenshots older than 60 days and presents them as cleanup candidates.

Nothing is removed automatically unless the user explicitly runs a removal operation.

---

## 2.2 WhatsApp image cleanup

The user can define a retention period for WhatsApp images saved into the iPhone photo library.

Example:

```yaml
retention:
  whatsapp: 30d
```

The system should identify WhatsApp imported images with the highest confidence available.

The system should distinguish between:

1. WhatsApp images saved into the iPhone photo library.
2. Media stored only inside the private WhatsApp application container.

Version 1 is primarily concerned with media visible in the iPhone photo library.

---

## 2.3 Local backup

The user can download all selected images and videos from the iPhone to a local Mac folder.

The download must:

- Resume after interruption.
- Skip files already downloaded and verified.
- Detect new media added since the previous sync.
- Maintain a persistent database of all known media.
- Preserve metadata.
- Verify transferred files before marking them complete.

---

## 2.4 Date based organization

The user can organize exported media by capture date.

Example:

```text
iPhone Archive/
├── 2024/
│   ├── 01/
│   ├── 02/
│   └── ...
├── 2025/
│   ├── 01/
│   ├── 02/
│   └── ...
└── 2026/
    ├── 01/
    ├── 02/
    └── ...
```

Default pattern:

```text
{year}/{month}
```

---

## 2.5 Geolocation based organization

The user can optionally organize media using GPS information.

Example:

```text
iPhone Archive/
├── South Africa/
│   ├── Johannesburg/
│   │   └── 2026/
│   │       └── 09/
│   └── Cape Town/
│       └── 2026/
│           └── 07/
├── United Kingdom/
│   └── London/
│       └── 2026/
│           └── 04/
└── Unknown Location/
```

Possible patterns:

```text
{country}/{city}/{year}/{month}
```

or:

```text
{year}/{country}/{city}
```

The layout must be user configurable.

---

## 2.6 Exact deduplication

The system should identify media files that are byte for byte identical.

The canonical mechanism should be a cryptographic content hash, preferably SHA256.

If:

```text
SHA256(A) == SHA256(B)
```

the files are exact duplicates.

Exact deduplication should be deterministic and require no AI.

---

## 2.7 Cloud synchronization

The user can synchronize verified local media to a cloud destination.

Version 1 should support Google Drive.

The architecture should allow additional cloud providers later.

Examples:

- Google Drive
- S3 compatible storage
- OneDrive
- Dropbox
- Backblaze B2
- NAS targets

Cloud synchronization should be incremental and resumable.

---

## 2.8 Explicit removal from iPhone

Removal is a separate operation.

Backup must never imply deletion.

The user must explicitly request:

```bash
iphone-image remove-from-iphone
```

and then explicitly apply the removal:

```bash
iphone-image remove-from-iphone --apply
```

The system must only offer media for removal when it can prove that the configured safety conditions are satisfied.

---

# 3. Product Principles

The project should follow these principles.

## 3.1 Keep by default

The default behavior is:

```text
KEEP ON IPHONE
```

This remains true even after successful local or cloud backup.

---

## 3.2 Sync is always incremental

There should not be a separate special command called `resync`.

Running:

```bash
iphone-image sync
```

should always:

1. Re-scan the iPhone.
2. Reconcile the current device state against the SQLite database.
3. Detect new media.
4. Detect missing or incomplete local files.
5. Resume interrupted transfers.
6. Skip files already verified.

Therefore, `sync` is inherently a re-sync operation.

---

## 3.3 Operations are idempotent

Running the same command repeatedly should not corrupt the archive or duplicate work.

Examples:

```bash
iphone-image sync
iphone-image sync
iphone-image sync
```

should simply converge toward a fully synchronized state.

The same applies to cloud synchronization and verification.

---

## 3.4 The database is the ledger

SQLite is not merely a cache.

It is the authoritative local ledger describing what has been observed and what has happened to each asset.

The database should answer questions such as:

- Is this asset still on the iPhone?
- Has it been downloaded?
- Has the local copy been verified?
- Has it been uploaded?
- Has the cloud copy been verified?
- Was it part of a cleanup campaign?
- Is it eligible for removal?
- Was it removed?
- When was it last seen?
- Has it changed?

---

## 3.5 Removal must be evidence based

An image must never be considered safe to remove based solely on filename or path.

Removal eligibility should be derived from evidence recorded in the database.

---

# 4. High Level Workflow

The normal lifecycle is:

```text
iPhone
   │
   │ scan
   ▼
SQLite Inventory
   │
   │ sync
   ▼
Local Archive
   │
   │ verify
   ▼
Locally Verified Archive
   │
   │ cloud-sync
   ▼
Google Drive
   │
   │ cloud verify
   ▼
Cloud Verified Archive

        NOTHING REMOVED YET

                 │
                 │ explicit user request
                 ▼
       remove-from-iphone
```

The workflow may run repeatedly over many days.

---

# 5. Multi Day Cleanup Workflow

A large iPhone library may require days to process.

During this time, new photos and videos will continue to be created.

The utility must handle this naturally.

Example:

## Day 1

```bash
iphone-image scan
iphone-image sync
```

40,000 assets are discovered.

30,000 are transferred before the phone is disconnected.

## Day 2

The user reconnects the phone and runs:

```bash
iphone-image sync
```

The system reads the SQLite database and resumes the remaining 10,000 assets.

## Day 5

The user has taken another 200 photos.

Running:

```bash
iphone-image sync
```

reconciles the phone and reports:

```text
Previously known:       40,000
New assets found:          200
Already verified:       40,000
Downloads required:        200
```

Only new or incomplete media is transferred.

## Day 7

The user runs:

```bash
iphone-image cloud-sync
iphone-image verify
```

The system reconciles local and cloud state.

The user may then inspect the archive for as long as they want.

No deletion has occurred.

When satisfied:

```bash
iphone-image remove-from-iphone
```

generates a removal plan.

Only after:

```bash
iphone-image remove-from-iphone --apply
```

does destructive action begin.

---

# 6. Cleanup Campaigns

A cleanup campaign should define the initial set of media that the user intends to archive and potentially remove.

Example:

```bash
iphone-image campaign start
```

Output:

```text
Campaign created

Name: September Cleanup
Started: 2026-09-14 09:00
Assets currently on iPhone: 40,213
```

Any media added to the phone after the campaign begins is still synchronized and backed up.

However, by default, new media is not automatically included in the removal set for that campaign.

Example:

```text
Campaign assets:       40,213
New assets since start:   137
```

A default removal operation removes only eligible campaign assets.

New media remains on the iPhone.

The user may explicitly include newer assets:

```bash
iphone-image remove-from-iphone --include-new
```

---

# 7. Functional Requirements

## 7.1 Device discovery

The utility must:

- Detect a connected iPhone.
- Identify the device reliably.
- Detect disconnects.
- Reconnect cleanly.
- Support one active iPhone at a time in version 1.
- Maintain device specific state.

---

## 7.2 Pairing and trust

The utility should detect when the Mac is not trusted by the iPhone and provide clear instructions.

It must never attempt to bypass iOS trust or security controls.

---

## 7.3 Inventory scan

A scan should discover all accessible media assets and capture metadata including where available:

- Device identifier
- Device asset identifier
- Device path
- Filename
- Original filename
- File size
- Creation date
- Modification date
- Media type
- Width
- Height
- Duration
- MIME type
- Image format
- Video format
- GPS latitude
- GPS longitude
- Camera make
- Camera model
- Lens data
- Screenshot classification
- Source application metadata
- Album metadata
- Live Photo relationships
- Burst relationships
- Edited version relationships

The scan must not modify the iPhone.

---

## 7.4 Classification

Each asset should be classified into one or more logical categories.

Initial categories:

```text
CAMERA
SCREENSHOT
WHATSAPP
SAVED_IMAGE
VIDEO
LIVE_PHOTO
BURST
RAW
EDITED
UNKNOWN
```

Classification must support confidence levels:

```text
HIGH
MEDIUM
LOW
UNKNOWN
```

Destructive policies should default to HIGH confidence classifications only.

---

## 7.5 Screenshots

The system should identify screenshots as accurately as possible using native asset metadata where available.

Retention rules:

```yaml
retention:
  screenshots: 60d
```

Supported values:

```text
7d
30d
60d
90d
180d
365d
never
```

Arbitrary durations should also be supported.

---

## 7.6 WhatsApp media

The system should attempt to identify images imported or saved by WhatsApp.

Possible evidence, in descending order of preference:

1. Source application bundle identifier.
2. Photo library import metadata.
3. Album membership.
4. Known metadata signatures.
5. Filename heuristics.

Automatic destructive operations should require HIGH confidence unless the user explicitly overrides this.

---

## 7.7 Camera media

The system should identify likely camera generated media separately from screenshots and imported images.

Where possible, this should use original capture metadata rather than filename patterns.

---

# 8. Asset Groups

Some iPhone media consists of multiple related files.

Examples include:

- Live Photos
- RAW + JPEG pairs
- Edited photos
- Burst photographs
- Sidecar metadata files
- Video plus metadata resources

The system should model these as logical asset groups.

Example:

```text
ASSET GROUP
├── IMG_1234.HEIC
├── IMG_1234.MOV
└── IMG_1234.AAE
```

A group must not be considered fully backed up unless all required components are safe.

Removal must operate on the logical asset rather than blindly deleting one file from a multi file object.

---

# 9. Local Synchronization

Command:

```bash
iphone-image sync
```

The sync process should:

1. Scan the device.
2. Compare device assets with SQLite.
3. Identify new assets.
4. Identify incomplete transfers.
5. Identify missing local files.
6. Transfer required files.
7. Compute SHA256.
8. Verify local integrity.
9. Mark each asset state in SQLite.

Example:

```text
Scanning iPhone...

Known assets:              40,213
New assets:                   137
Incomplete transfers:          12
Missing local files:            3
Already verified:          40,061

Transfers required:           152
```

---

# 10. Resume Support

Resume support is mandatory.

At minimum, resume must operate at asset level.

If the application is interrupted after 25,000 files, a future sync must not restart those 25,000 transfers.

For large individual video files, chunk level resume should be considered.

Possible state:

```text
DISCOVERED
CLASSIFIED
QUEUED
DOWNLOADING
LOCAL_COMPLETE
LOCAL_VERIFIED
```

A partially transferred file must never be mistaken for a verified file.

Temporary files should use a distinct extension, for example:

```text
IMG_1234.MOV.partial
```

Only after successful verification should the file be renamed to the final path.

---

# 11. Local Archive Layout

The user must be able to choose the archive structure.

## Date mode

```yaml
organization:
  mode: date
  pattern: "{year}/{month}"
```

## Location mode

```yaml
organization:
  mode: location
  pattern: "{country}/{city}/{year}/{month}"
```

## Custom mode

```yaml
organization:
  mode: custom
  pattern: "{year}/{country}/{city}/{month}"
```

Unknown metadata must be handled safely.

Examples:

```text
Unknown Date/
Unknown Location/
Unknown City/
```

---

# 12. Geolocation

If GPS coordinates exist, the system should store them in SQLite.

Reverse geocoding may then enrich the record with:

- Country
- Region
- Province or state
- City
- Suburb
- Locality

Reverse geocoding should be cached locally so identical coordinates or nearby images do not repeatedly call external services.

The system should allow reverse geocoding to be disabled.

No geolocation lookup should be required for date based organization.

---

# 13. Exact Deduplication

Exact duplicates should be determined using SHA256.

Database fields:

```text
sha256
content_size
duplicate_group_id
canonical_asset_id
```

The system should distinguish:

```text
EXACT_DUPLICATE
NEAR_DUPLICATE
UNIQUE
UNKNOWN
```

Version 1 should automatically deal only with exact duplicates.

Near duplicate analysis belongs to a later phase.

---

# 14. Near Duplicate Detection

This is explicitly not a version 1 requirement.

Future versions may detect:

- Burst style images
- Multiple photos of the same subject
- Slightly different crops
- Re-encoded versions
- Lower resolution copies
- Visually similar photos

Potential techniques:

- Perceptual hashing
- Image embeddings
- Face quality scoring
- Blur detection
- Exposure scoring
- Resolution scoring
- AI assisted best shot ranking

Near duplicate deletion should never initially be automatic.

The first implementation should present grouped candidates for review.

---

# 15. Cloud Synchronization

Command:

```bash
iphone-image cloud-sync
```

Version 1 should support Google Drive.

A practical implementation may use rclone as the cloud transfer engine.

The cloud sync should:

1. Find locally verified assets not yet cloud verified.
2. Upload them.
3. Record remote object identity.
4. Verify the remote result where possible.
5. Mark the asset as cloud verified.

Cloud state should be independent from local state.

Possible fields:

```text
cloud_provider
cloud_account
cloud_object_id
cloud_path
cloud_size
cloud_hash
cloud_status
cloud_uploaded_at
cloud_verified_at
```

---

# 16. Cloud Provider Abstraction

The architecture should not hard code Google Drive into core logic.

Define a cloud provider interface such as:

```text
CloudProvider
├── list()
├── exists()
├── upload()
├── verify()
├── metadata()
└── delete()
```

Potential future implementations:

```text
GoogleDriveProvider
S3Provider
OneDriveProvider
DropboxProvider
LocalNASProvider
```

---

# 17. Verification

Command:

```bash
iphone-image verify
```

Verification should reconcile:

```text
IPHONE
LOCAL ARCHIVE
CLOUD ARCHIVE
DATABASE
```

Example result:

```text
Verification Summary

Assets known:               40,350
Present on iPhone:          40,350
Local complete:             40,350
Local verified:             40,350
Cloud uploaded:             40,350
Cloud verified:             40,344

Safe for cloud based removal:
                             40,344

Blocked from removal:
                                  6
```

The six blocked assets must be identifiable.

---

# 18. Removal From iPhone

Removal is a first class feature.

It must remain separate from backup and synchronization.

Commands:

```bash
iphone-image remove-from-iphone
```

shows a plan.

```bash
iphone-image remove-from-iphone --apply
```

executes it.

The default command must not remove anything.

---

# 19. Removal Safety Levels

The user should be able to configure what qualifies an asset for removal.

Possible policies:

## Never remove

```yaml
remove_from_iphone:
  policy: never
```

This should be the default.

## Remove after local verification

```yaml
remove_from_iphone:
  policy: local_verified
```

## Remove after cloud verification

```yaml
remove_from_iphone:
  policy: cloud_verified
```

Recommended default configuration:

```yaml
remove_from_iphone:
  policy: never
```

The user must explicitly change or override this.

---

# 20. Final Reconciliation Before Removal

Immediately before creating a removal plan, the system should perform a fresh device scan.

This is mandatory.

The goal is to catch:

- New images created since the previous sync.
- Files deleted manually by the user.
- Files that changed.
- Interrupted transfers.
- Assets missing from cloud storage.
- Database state that is no longer current.

Flow:

```text
final scan
   ↓
reconcile SQLite
   ↓
recalculate eligibility
   ↓
show removal plan
   ↓
user applies
```

---

# 21. Removal Plan

Example:

```text
IPHONE REMOVAL PLAN

Campaign:
September Cleanup

Campaign assets:             40,213

Verified locally:            40,213
Verified in Google Drive:    40,207

Blocked from removal:             6

Eligible for removal:        40,207

New assets since campaign:
                               137

New assets will remain on the iPhone.

Estimated storage recovered:
183.7 GB

NO FILES HAVE BEEN REMOVED.

Run:

iphone-image remove-from-iphone --apply
```

---

# 22. Interrupted Removal

Removal itself must be resumable.

Every successfully removed asset must be recorded immediately.

If the iPhone disconnects:

```text
Removed:      18,721
Remaining:    21,486
```

The next execution:

```bash
iphone-image remove-from-iphone --apply
```

should resume from the remaining eligible set.

Already removed assets should not generate errors.

---

# 23. Cleanup Commands

The application should support policy specific cleanup planning.

Examples:

```bash
iphone-image clean screenshots --older-than 60d
```

```bash
iphone-image clean whatsapp --older-than 30d
```

These commands should display candidates and storage estimates.

Actual device removal should still use the explicit removal mechanism.

Possible workflow:

```bash
iphone-image clean screenshots --older-than 60d
iphone-image clean whatsapp --older-than 30d

iphone-image remove-from-iphone
iphone-image remove-from-iphone --apply
```

---

# 24. Dry Run

All destructive operations must support dry run behavior.

Example:

```bash
iphone-image remove-from-iphone --dry-run
```

However, because the unqualified `remove-from-iphone` command already only displays a plan, the main safety model is:

```text
command without --apply = preview
command with --apply = execute
```

---

# 25. CLI Specification

Primary executable:

```bash
iphone-image
```

## Device

```bash
iphone-image device
iphone-image device info
```

## Scan

```bash
iphone-image scan
```

Inventory only.

## Sync

```bash
iphone-image sync
```

Incremental local synchronization.

## Cloud sync

```bash
iphone-image cloud-sync
```

Upload locally verified assets to cloud storage.

## Verify

```bash
iphone-image verify
```

Reconcile device, local, database and cloud state.

## Status

```bash
iphone-image status
```

Example:

```text
iPhone Image Manager

Device: Andrew's iPhone

Phone assets:            40,350
Local verified:          40,350
Cloud verified:          40,344

Screenshots:              8,912
WhatsApp:                10,833
Camera assets:           20,605

Exact duplicates:         2,108

Storage on phone:        217.4 GB
Eligible for removal:    181.9 GB
```

## Campaign

```bash
iphone-image campaign start
iphone-image campaign status
iphone-image campaign close
```

## Cleanup

```bash
iphone-image clean screenshots --older-than 60d
iphone-image clean whatsapp --older-than 30d
iphone-image clean duplicates
```

## Remove

```bash
iphone-image remove-from-iphone
iphone-image remove-from-iphone --apply
iphone-image remove-from-iphone --include-new
```

## Config

```bash
iphone-image config show
iphone-image config validate
```

---

# 26. Configuration File

Example:

```yaml
version: 1

archive:
  local_path: "~/Pictures/iPhoneArchive"

organization:
  mode: date
  pattern: "{year}/{month}"

geolocation:
  enabled: true
  reverse_geocode: true

retention:
  screenshots: 60d
  whatsapp: 30d

deduplication:
  exact:
    enabled: true
    algorithm: sha256
  near_duplicates:
    enabled: false

cloud:
  enabled: true
  provider: google_drive
  destination: "iPhone Archive"

remove_from_iphone:
  policy: never
  include_new_campaign_assets: false

safety:
  require_final_scan: true
  require_local_verification: true
  require_cloud_verification: true

database:
  path: "~/.iphone-image/iphone-image.sqlite"

logging:
  level: info
  path: "~/.iphone-image/logs"
```

---

# 27. SQLite Data Model

Suggested tables:

```text
devices
assets
asset_resources
asset_groups
classifications
locations
local_objects
cloud_objects
campaigns
campaign_assets
operations
deletion_events
duplicate_groups
settings
```

---

# 28. Devices Table

Example fields:

```text
id
device_udid
device_name
model
ios_version
first_seen_at
last_seen_at
created_at
updated_at
```

---

# 29. Assets Table

Suggested fields:

```text
id

device_id
device_asset_id
device_path

filename
original_filename

media_type
mime_type

created_at_device
modified_at_device

first_seen_at
last_seen_at

size_bytes

width
height
duration_seconds

sha256

latitude
longitude

country
region
city
suburb

source_application
source_bundle_id

classification
classification_confidence

asset_group_id

present_on_phone

local_status
local_verified_at

cloud_status
cloud_verified_at

campaign_id

removed_from_phone_at

created_at
updated_at
```

---

# 30. Asset Resources Table

This table supports compound media.

Fields:

```text
id
asset_id
resource_type
device_path
filename
size_bytes
sha256
local_path
local_status
cloud_status
```

Example resource types:

```text
PRIMARY_IMAGE
LIVE_PHOTO_VIDEO
ORIGINAL
EDITED
SIDECAR
RAW
JPEG_COMPANION
VIDEO
THUMBNAIL
```

---

# 31. Campaigns Table

Fields:

```text
id
name
started_at
closed_at
device_id
status
initial_asset_count
notes
```

---

# 32. Campaign Assets Table

Fields:

```text
campaign_id
asset_id
included_at
include_reason
eligible_for_removal
removed_at
```

---

# 33. Operation Journal

Every significant operation should be journaled.

Examples:

```text
SCAN_STARTED
SCAN_COMPLETED

DOWNLOAD_STARTED
DOWNLOAD_COMPLETED
DOWNLOAD_VERIFIED

CLOUD_UPLOAD_STARTED
CLOUD_UPLOAD_COMPLETED
CLOUD_UPLOAD_VERIFIED

REMOVE_STARTED
REMOVE_COMPLETED
REMOVE_FAILED
```

The journal should support debugging and recovery.

---

# 34. State Machine

A logical asset may progress through:

```text
DISCOVERED
    ↓
CLASSIFIED
    ↓
LOCAL_QUEUED
    ↓
LOCAL_TRANSFERRING
    ↓
LOCAL_COMPLETE
    ↓
LOCAL_VERIFIED
    ↓
CLOUD_QUEUED
    ↓
CLOUD_TRANSFERRING
    ↓
CLOUD_COMPLETE
    ↓
CLOUD_VERIFIED
    ↓
REMOVAL_ELIGIBLE
    ↓
REMOVAL_REQUESTED
    ↓
REMOVED_FROM_DEVICE
```

An asset may remain indefinitely at any non destructive stage.

---

# 35. Exact Duplicate Handling

When two assets share the same SHA256:

```text
duplicate_group_id = X
```

One local canonical file may be retained.

However, the database should preserve separate logical asset records because multiple iPhone assets may point to identical content.

This matters if the user later removes one asset but not another.

---

# 36. File Naming Collisions

The archive must safely handle duplicate filenames.

Example:

```text
IMG_1234.HEIC
IMG_1234.HEIC
```

Possible strategy:

```text
IMG_1234.HEIC
IMG_1234__A1B2C3D4.HEIC
```

where the suffix derives from the asset identifier or content hash.

Never overwrite an unrelated file.

---

# 37. Metadata Preservation

The system should preserve:

- Original capture date.
- GPS coordinates.
- Camera metadata.
- Orientation.
- Original filename.
- Live Photo relationships.
- Original and edited relationships where possible.
- File timestamps where useful.

Metadata should not be stripped during transfer.

---

# 38. Implementation Architecture

Suggested structure:

```text
iphone-image-manager/
│
├── cli/
│   ├── commands/
│   └── output/
│
├── device/
│   ├── discovery/
│   ├── pairing/
│   ├── afc/
│   └── deletion/
│
├── inventory/
│   ├── scanner/
│   ├── metadata/
│   └── database/
│
├── classifiers/
│   ├── camera/
│   ├── screenshot/
│   ├── whatsapp/
│   └── saved_media/
│
├── transfer/
│   ├── local/
│   ├── resume/
│   └── verify/
│
├── dedupe/
│   ├── exact/
│   └── near/
│
├── organize/
│   ├── date/
│   ├── location/
│   └── custom/
│
├── geocode/
│
├── cloud/
│   ├── base/
│   ├── google_drive/
│   └── rclone/
│
├── campaigns/
│
├── cleanup/
│   ├── screenshots/
│   ├── whatsapp/
│   ├── duplicates/
│   └── removal/
│
├── journal/
│
└── tests/
```

---

# 39. Recommended Technology

## Primary language

Python is suitable for:

- CLI.
- Workflow orchestration.
- SQLite.
- Metadata processing.
- Hashing.
- Configuration.
- Cloud integration.
- Reporting.
- Geolocation processing.

## Native macOS helper

Swift may be used for operations that are materially safer or easier through native macOS frameworks.

In particular, deletion should be evaluated through a native Apple device API rather than directly deleting arbitrary files from the iPhone filesystem.

## iPhone transport

The project should evaluate:

```text
libimobiledevice
ifuse
AFC
ImageCaptureCore
```

The preferred architecture is direct programmatic access rather than depending on the Apple Photos application.

## Metadata

Possible tooling:

```text
ExifTool
Pillow
pillow-heif
ffprobe
```

## Cloud

Possible tooling:

```text
rclone
```

## Database

```text
SQLite
```

---

# 40. Error Handling

The utility must handle:

- USB disconnect.
- iPhone lock.
- Trust revoked.
- Mac sleep.
- Partial file transfer.
- Full local disk.
- Cloud quota exceeded.
- Network interruption.
- Cloud authentication expiry.
- Permission errors.
- Duplicate filenames.
- Corrupted source file.
- Corrupted local copy.
- Hash mismatch.
- Missing cloud object.
- Device file disappearing mid transfer.
- iOS metadata changes.
- Unsupported media types.

Every error should be recoverable where possible.

---

# 41. Device Lock Handling

If the phone becomes inaccessible because it is locked, the utility should pause gracefully.

Example:

```text
iPhone is locked.

Unlock the device to continue.

Current progress has been saved.
```

It must not lose completed work.

---

# 42. Storage Safety

Before synchronization, the utility should estimate required local storage.

Example:

```text
Local storage required:   184.3 GB
Available storage:        212.8 GB
Status:                   OK
```

If insufficient:

```text
Local storage required:   184.3 GB
Available storage:         94.1 GB

Sync cannot safely continue.
```

Partial synchronization may optionally be allowed if explicitly requested.

---

# 43. Reporting

The utility should generate useful human readable summaries.

Example:

```text
IPHONE IMAGE MANAGER

Device
------
Andrew's iPhone

Inventory
---------
Total assets:             40,350
Photos:                   32,201
Videos:                    4,102
Screenshots:               2,914
WhatsApp imports:          1,133

Archive
-------
Local verified:           40,350
Cloud verified:           40,344

Cleanup
-------
Exact duplicates:          2,108
Old screenshots:           1,492
Old WhatsApp images:         821

Removal
-------
Eligible:                 38,941
Blocked:                       6
New since campaign:          137
```

---

# 44. Logging

Logs should include:

- Timestamp.
- Command.
- Device identifier.
- Asset identifier.
- Operation.
- Result.
- Error message.
- Retry count.
- Duration.

Sensitive authentication tokens must never be written to logs.

---

# 45. Privacy

The project should be privacy first.

By default:

- Image content remains local except when the user configures a cloud provider.
- No analytics are required.
- No image metadata is sent to CloudScale.
- Reverse geocoding can be disabled.
- AI analysis should be optional.
- Credentials remain under user control.

---

# 46. Security

The system should:

- Avoid storing cloud passwords directly.
- Prefer provider tokens or OS keychain integration.
- Never log authentication secrets.
- Validate archive paths.
- Prevent path traversal.
- Avoid blindly executing filenames as shell arguments.
- Treat metadata as untrusted input.
- Verify hashes before destructive operations.

---

# 47. Performance

The application should be designed for libraries with:

```text
100,000+ assets
500+ GB
multi day processing
```

The database should be indexed appropriately.

Likely indexes:

```text
device_asset_id
sha256
created_at_device
classification
present_on_phone
local_status
cloud_status
campaign_id
```

---

# 48. Concurrency

The system may use controlled parallelism for file transfer, hashing, metadata extraction and cloud upload.

Concurrency should be configurable.

Example:

```yaml
performance:
  local_transfer_workers: 2
  hash_workers: 4
  cloud_upload_workers: 4
```

Device stability should take priority over maximum throughput.

---

# 49. Progress Display

Long running commands should display progress.

Example:

```text
Downloading

18,721 / 40,213 assets
46.6%

Data transferred:
92.1 GB / 184.3 GB

Current:
IMG_8392.MOV

Speed:
31.4 MB/s

ETA:
48m
```

Progress should not be required for correctness.

---

# 50. Removal Progress

Example:

```text
Removing from iPhone

18,721 / 40,207 assets

Already removed:
18,720

Current:
IMG_8392.HEIC

Remaining:
21,486

Recovered:
86.7 GB

Database checkpoint saved.
```

---

# 51. Testing Strategy

Testing should include:

## Unit tests

- Hashing.
- Path generation.
- Date organization.
- Location organization.
- Retention policies.
- Removal eligibility.
- Campaign membership.
- Filename collision handling.

## Integration tests

- Device scan.
- Interrupted transfer.
- Resume.
- Cloud upload.
- Cloud retry.
- Cloud verification.
- Interrupted removal.
- Final reconciliation.

## Media tests

Include fixtures for:

- JPEG.
- HEIC.
- MOV.
- MP4.
- RAW.
- Live Photo.
- Screenshot.
- Edited image.
- Burst.
- WhatsApp imported image.
- Duplicate image.
- Video larger than 1 GB.

---

# 52. Destructive Test Harness

Removal code should never first be tested against a user's primary iPhone library.

The project should include a test harness and preferably a dedicated test device workflow.

Deletion must have tests for:

- Successful removal.
- Removal rejected due to failed verification.
- Disconnect during removal.
- Repeat removal.
- Asset already gone.
- New asset appearing during campaign.
- Compound asset deletion.
- Database write failure.

---

# 53. Version 1 Scope

Version 1 should include:

1. macOS support.
2. iPhone discovery.
3. SQLite inventory.
4. Resumable local sync.
5. Re-sync by rerunning `sync`.
6. Date based organization.
7. GPS metadata capture.
8. Optional location based organization.
9. Screenshot classification.
10. WhatsApp image classification.
11. Exact SHA256 duplicate detection.
12. Google Drive sync.
13. Local and cloud verification.
14. Cleanup campaigns.
15. Explicit removal from iPhone.
16. Resume interrupted removal.
17. Dry run and removal planning.
18. CLI.
19. Configuration file.
20. Human readable status reports.

---

# 54. Explicitly Out of Scope for Version 1

The following should not block the first release:

- AI best photo selection.
- Automatic near duplicate removal.
- Facial recognition.
- Full photo management UI.
- iOS application.
- Windows support.
- Linux support.
- Direct manipulation of private WhatsApp app storage.
- Automatic deletion without explicit user request.
- Automatic photo editing.
- Photo enhancement.

---

# 55. Future Features

Possible future work:

## AI best shot selection

Group similar images and recommend the best based on:

- Sharpness.
- Eyes open.
- Face quality.
- Exposure.
- Motion blur.
- Resolution.
- Composition.

## Trip clustering

Infer trips based on:

- Time.
- GPS movement.
- Country changes.
- City changes.

Example:

```text
2026-07 Italy Holiday/
```

## Web UI

Optional local browser based interface for:

- Review.
- Filtering.
- Duplicate comparison.
- Campaign status.
- Removal approval.

## Additional cloud providers

- S3.
- OneDrive.
- Dropbox.
- NAS.
- Backblaze B2.

## Smart retention

Examples:

```text
Keep screenshots for 30 days.
Keep WhatsApp images for 14 days.
Keep camera photos until cloud verified.
Never remove Favorites.
Never remove photos with rating or manual protection.
```

---

# 56. Suggested README Positioning

## Name

# iPhone Image Manager

**by CloudScale**

## Tagline

> Open source iPhone image backup, cleanup, organization and safe offload for macOS.

## Short description

iPhone Image Manager is an open source macOS utility that inventories, backs up, organizes, deduplicates and verifies images and videos from an iPhone. It supports resumable USB sync, date and location based organization, Google Drive backup, screenshot and WhatsApp cleanup policies, exact duplicate detection, and explicit safe removal from the iPhone.

The default behavior is always to keep media on the iPhone.

---

# 57. SEO Positioning

Primary search themes:

```text
iPhone image backup
iPhone image cleanup
iPhone image manager
open source iPhone image backup
backup iPhone images to Mac
backup iPhone images to Google Drive
remove iPhone images after backup
clean up iPhone screenshots
clean up WhatsApp images iPhone
iPhone photo backup without Apple Photos
iPhone image deduplication
iPhone image export Mac
```

Possible repository name:

```text
iphone-image-manager
```

Possible CLI package name:

```text
iphone-image
```

---

# 58. User Experience Goal

The project should make this workflow safe and boring:

```text
Connect iPhone
     ↓
Scan
     ↓
Sync
     ↓
Disconnect if needed
     ↓
Reconnect
     ↓
Sync again
     ↓
Repeat for days if required
     ↓
Cloud sync
     ↓
Verify
     ↓
Inspect
     ↓
Final sync
     ↓
Final verification
     ↓
Preview removal plan
     ↓
Explicitly remove from iPhone
```

At every point the system should know exactly what has happened to every asset.

---

# 59. Core Safety Promise

The project should be able to make the following promise:

> **iPhone Image Manager will never remove an image or video merely because it has been seen, copied, or uploaded. Removal is a separate, explicit operation and is only offered when the configured verification requirements have been satisfied.**

---

# 60. Definition of Done for Version 1

Version 1 is complete when a user with a large iPhone library can:

1. Connect an iPhone to a Mac.
2. Scan the complete accessible image library.
3. Disconnect at any time.
4. Reconnect and resume.
5. Run `sync` repeatedly over several days.
6. Capture any new media added during those days.
7. Back up all media locally.
8. Organize it by date or location.
9. Identify screenshots and WhatsApp images.
10. Detect exact duplicates.
11. Synchronize verified media to Google Drive.
12. Verify local and cloud copies.
13. Create a cleanup campaign.
14. See exactly which assets are safe to remove.
15. Preview the removal operation.
16. Explicitly run removal.
17. Resume removal after interruption.
18. Prove through SQLite state that every removed asset had satisfied the configured backup and verification policy.

That is the first production ready release of **iPhone Image Manager by CloudScale**.
