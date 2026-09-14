# Implementation Plan

Plan of record for delivering `docs/SPEC.md`.

**Status:** P0 not started. Nothing below P0 is committed, because P0 decides it.
**Last updated:** 2026-09-14

### Decisions taken

| # | Decision | Choice |
|---|---|---|
| 1 | iCloud optimized proxies | Back up, permanently block from removal, no override flag |
| 2 | iCloud Photos sync propagation | Detect, state it in the plan, require a typed confirmation for `--apply` |
| 3 | WhatsApp cleanup | Review-only with per-batch approval, not a confidence-gated policy |
| 4 | Release cut | Four releases, v0.1 through v1.0, removal last |
| 5 | Unobtainable metadata | Best-effort inference, never a removal precondition |
| 6 | Reverse geocoding | Offline bundled dataset, no network |
| 7 | Cloud transport | Shell out to rclone, the tool never holds a token |
| 8 | Exact duplicate storage | One archive file per unique SHA256, N asset rows |
| 9 | Python baseline | 3.12 floor, CI on 3.12 and 3.14 |
| 10 | Distribution | Homebrew tap, signed and notarised helper |
| 11 | Deletion recoverability | Nothing is unlinked. See section 6. |
| 12 | `local_objects` table | Dropped, `asset_resources` covers it |

---

## 1. The decision that shapes everything else

The spec leaves the transport open (section 39: "The project should evaluate
libimobiledevice, ifuse, AFC, ImageCaptureCore"). That evaluation is not a detail.
It determines which metadata exists, which classifications are possible, and whether
deletion can be done safely at all. Nothing past P0 can be sized until it is settled.

### The proposed architecture, to be confirmed or killed by P0

```
  iphone-image (Python CLI)
        │
        │  JSON Lines over stdout, one record per asset
        ▼
  iimhelper (Swift binary, ImageCaptureCore)      <- enumeration + download + DELETE
        │
        ├── libimobiledevice / AFC                <- device identity, iOS version, cross-check
        └── exiftool / ffprobe                    <- metadata from downloaded bytes
```

**Why a Swift helper is not optional.** Deletion must go through
`ICCameraDevice.requestDeleteFiles:`, the API Image Capture.app uses, so that iOS
updates its own Photos database. Unlinking files over AFC removes bytes and leaves
dangling database entries and unreclaimed space. There is no Python binding for
ImageCaptureCore, so a small Swift binary is the price of doing removal correctly.

**Why libimobiledevice still earns its place.** Stable UDID, device name, model and
iOS version, plus an independent enumeration of `/DCIM` that can be diffed against
what ImageCaptureCore reports. Two independent views of the device is cheap
insurance for a tool that deletes things.

---

## 2. P0: transport spike (blocking, 1 to 2 days)

Runs against a real iPhone. Output is a written findings document in
`spikes/P0-transport.md` plus throwaway code in `spikes/`. No production code is
written in this phase.

### Questions that must be answered

| # | Question | Why it is blocking |
|---|---|---|
| 1 | What asset count and total bytes does ImageCaptureCore report vs `ifuse` + `find /DCIM`? | A discrepancy means one view is incomplete, and we need to know which before trusting either. |
| 2 | Which of spec section 7.3's metadata fields are actually available? | Album membership, source bundle id and burst relationships live in `Photos.sqlite`, which is not readable over either transport. Confirm what survives. |
| 3 | Are Live Photo `.HEIC` + `.MOV` pairs reliably pairable? By name, by `ICCameraItem` relationship, or not at all? | Asset group integrity is a removal safety precondition. |
| 4 | Does `requestDeleteFiles:` work, and does it leave a consistent Photos database? Does it route through Recently Deleted? | The entire removal feature depends on this. |
| 5 | Can iCloud "Optimize iPhone Storage" state be read, and do proxy assets look measurably different? | Drives the `SUSPECTED_PROXY` detector in `docs/SAFETY.md`. |
| 6 | Can iCloud Photos sync state be detected? | Drives the typed-confirmation gate. |
| 7 | What happens on lock, sleep and unplug mid-enumeration and mid-download? Which errors surface? | Determines the entire resume and error taxonomy. |
| 8 | Sustained throughput and stability at 1, 2 and 4 concurrent downloads. | Sets the default for `performance.local_transfer_workers`. |
| 9 | Does the helper need TCC / Full Disk Access, and does it need signing and notarisation to run on another Mac? | Distribution story. A tool users cannot install is not shipped. |

### Exit criteria

- `spikes/P0-transport.md` answers all nine with observed evidence, not inference.
- A one page go / no-go on the Swift helper.
- If ImageCaptureCore deletion turns out to be unusable, P0 ends with a written
  recommendation on whether removal ships at all in v1, and the milestones below
  are re-cut before any further work.

---

## 3. Milestones

The spec's section 53 lists twenty items as "version 1". Shipping them as one
release means the deletion code lands at the same time as everything it depends on,
with no soak period. Cut into four releases instead, so removal is last and arrives
on top of machinery that has already been proven on real libraries.

| Milestone | Contains | The promise it makes |
|---|---|---|
| **v0.1 Inventory** | P1, P2, P3, P4 | Tells you what is on your phone. Touches nothing. |
| **v0.2 Archive** | P5, P6 | Gets it all onto your Mac, resumably, verified, organized. |
| **v0.3 Cloud** | P7, P8, P9 | Gets it to Google Drive, reconciles all four views, campaigns. |
| **v1.0 Offload** | P10, P11 | Removes from the phone, explicitly, resumably, provably. |

Removal code is written last and is gated on its own test harness existing first.

---

## 4. Phases

### P1. Foundation
Repository scaffolding, config, database, journal.

- `config.py`: pydantic model of spec section 26, loader, `config show`, `config validate`.
  Unknown keys are an error, not a warning.
- `db/schema.sql` and a forward-only migration runner. Tables per spec section 27.
  Indexes per section 47 from day one, not retrofitted.
- `journal.py`: append-only operations log, spec section 33. Every phase writes to it.
- `iphone-image` entrypoint with `--json` output mode alongside the rich human output.
- CI: ruff, mypy, pytest on macOS.

**Exit:** `config validate` and an empty `status` run green on a clean machine.

### P2. Device layer
- `iimhelper` Swift package: `enumerate`, `download`, `delete`, `trash`, `info`
  subcommands (`trash` wraps `NSFileManager.trashItem` for rule A in section 6),
  JSON Lines on stdout, non-zero exit on every failure path.
- Python `DeviceBackend` protocol with three implementations: `ImageCaptureBackend`,
  `AfcBackend` (read only), `FakeDeviceBackend` (tests).
- Discovery, pairing and trust detection with actionable messages.
- Lock, disconnect and sleep handling: pause, checkpoint, resume. Spec section 41.
- `device`, `device info`.

**Exit:** unplugging the phone mid-enumeration loses no recorded work.

### P3. Inventory and scan
- `scan`: enumerate, upsert assets, maintain `first_seen_at` / `last_seen_at` /
  `present_on_phone`. Read only with respect to the device, asserted in tests.
- Metadata extraction via exiftool and ffprobe, with a no-shell argv boundary.
- Asset group construction: Live Photo pairs, RAW+JPEG, `.AAE` sidecars, bursts.
- Proxy suspicion scoring, per `docs/SAFETY.md` section 2.

**Exit:** two scans of an unchanged device produce zero row changes other than
`last_seen_at`.

### P4. Classification
- `classifiers/`, each returning `(category, confidence, evidence[])`. The evidence
  list is persisted, so any classification can be explained to the user.
- Screenshot: PNG, no `Make`/`Model`, dimensions matching a known device screen size.
  Realistically HIGH confidence.
- Camera: `Make` = Apple plus lens and capture settings present. HIGH.
- WhatsApp: see conflict 1 below. MEDIUM at best.
- `SAVED_IMAGE`, `VIDEO`, `LIVE_PHOTO`, `BURST`, `RAW`, `EDITED`, `UNKNOWN`.

**Exit:** a labelled fixture set with measured precision and recall per category,
committed as a test, so a classifier regression is visible.

### P5. Local sync
- Storage preflight, spec section 42. Refuses to start rather than filling the disk.
- Worker pool, `.partial` suffix, atomic rename only after hash verification.
- SHA256 on the downloaded bytes, compared against a re-read of the final file.
- Path builder for date, location and custom modes, with `Unknown Date` /
  `Unknown Location` fallbacks and path traversal rejection on every metadata
  derived path segment.
- Collision handling: `IMG_1234__A1B2C3D4.HEIC`, suffix from the content hash.
- Metadata preservation, spec section 37, including file mtime from capture date.
- Replacing a hash-mismatched archive file sends the old one to the Trash, never
  unlinks it. Scratch `.partial` files are unlinked.
- Reverse geocoding with an on-disk cache and an off switch.

**Exit:** kill -9 at 50 percent, rerun, and the result is byte identical to an
uninterrupted run. This is a test, not a manual check.

### P6. Exact deduplication
- `duplicate_groups`, canonical asset selection, `clean duplicates`.
- One archive file per unique SHA256. Collapsed duplicates go to the Trash.
- Distinct logical asset rows are preserved even when content is identical, per
  spec section 35.

**Exit:** N device assets with identical content produce one archive file and N
asset rows.

### P7. Cloud
- `CloudProvider` protocol, spec section 16. `RcloneProvider` as the first concrete
  implementation, Google Drive as its first remote.
- Credentials via the user's own rclone config. The tool never holds a token and
  never logs one.
- Incremental, resumable, verified against remote size and hash where the remote
  exposes one.

**Exit:** interrupted upload resumes without re-uploading verified objects.

### P8. Verify and report
- `verify` reconciles device, local archive, cloud and database, spec section 17.
- `status`, spec section 25, plus `status --proxies` and `status --blocked`.
- Every blocked asset is individually listable with its reason.

**Exit:** the "6 blocked" in the spec's example output can be enumerated with causes.

### P9. Campaigns and cleanup planning
- `campaign start|status|close`, membership frozen at start, spec section 6.
- `clean screenshots|whatsapp|duplicates`, planning only, no device writes.

**Exit:** assets created after campaign start are excluded from the default plan.

### P10. Removal
Gated: the harness in `tests/destructive/` must exist and pass before
`remove-from-iphone --apply` is implemented.

- Mandatory fresh scan, then eligibility recomputation, then plan.
- Typed confirmation when iCloud Photos sync is active.
- Per-asset journal write before and after each deletion, so a mid-run crash
  leaves an accurate ledger.
- Resume, idempotence, and "already gone" treated as success not error.
- Group-aware deletion: all resources of a logical asset, or none.
- Recycle bin entry written and fsynced **before** the device deletion call, so a
  crash between the two leaves a record rather than a hole.
- `recycle-bin list|restore|empty`.

**Exit:** all eight destructive scenarios from spec section 52 pass against the fake
backend, every removed asset has a recycle bin entry that `restore` can resolve to
readable bytes, and a full cycle runs clean on a dedicated test iPhone.

### P11. Release
Install docs, helper signing and notarisation, Homebrew tap, README with real output,
CHANGELOG, issue templates.

---

## 5. Spec conflicts and proposed resolutions

Found while reviewing the spec. Each needs a decision. Resolutions marked
**[settled]** have been agreed already.

### 1. WhatsApp cleanup cannot reach HIGH confidence **[settled]**
Spec section 7.6 ranks source bundle identifier as the best evidence, and section
7.4 says destructive policies default to HIGH confidence only. The source bundle id
lives in `Photos.sqlite`, inside the device's protected app data, and is not
readable over AFC or ImageCaptureCore. What remains is metadata signatures and
filename heuristics, which the spec itself ranks fourth and fifth. On iOS, WhatsApp
saves to the camera roll under ordinary `IMG_` names.

Net effect: WhatsApp classification tops out at MEDIUM, so under the spec's own rule
`clean whatsapp` would never produce a removable asset. The feature would ship
inert.

**Settled:** WhatsApp cleanup is review-only in v1. It presents candidates with
per-asset evidence and requires explicit per-asset or per-batch approval, rather
than running as a confidence-gated policy. Screenshot cleanup, which genuinely does
reach HIGH, remains policy driven. Document the limitation in the README rather
than implying parity between the two.

### 2. iCloud optimized proxies **[settled]**
Detect by size and dimension heuristics, back up normally, block from removal
permanently, and surface the list explicitly. No override flag in v1. See
`docs/SAFETY.md` section 2.

### 3. iCloud sync deletion propagation **[settled]**
Detect, state the consequence in the removal plan, require a typed confirmation
phrase for `--apply`. See `docs/SAFETY.md` section 3.

### 4. Spec section 7.3 lists metadata that is not obtainable **[settled]**
Album metadata, source application metadata and burst relationships are
`Photos.sqlite` residents. Edited-version relationships are partially inferable from
`.AAE` sidecars. Burst membership is partially inferable from filename and
sub-second capture time.

**Settled:** mark these as best-effort in the schema, populate from inference where
possible with recorded evidence, and never let a removal precondition depend on
them. Confirm the exact list in P0 question 2.

### 5. "Estimated storage recovered" is misleading **[settled]**
Spec section 21 shows `183.7 GB` as if removal frees it immediately. iOS holds
deleted assets in Recently Deleted for up to 30 days.

**Settled:** reword to "storage reclaimed once Recently Deleted is emptied, up to
30 days", and tell the user after a removal run that the window is their undo.

### 6. Section 27 lists `local_objects` but sections 29 and 30 fold local state into
`assets` and `asset_resources` **[settled]**
Two representations of the same thing.

**Settled:** drop `local_objects`. `asset_resources` already carries `local_path`,
`local_status` and `sha256` at the right grain, and resource-level state is what
group-aware removal actually needs.

### 7. Section 53's twenty items as one release **[settled]**
Covered in section 3 above. Cut into v0.1 through v1.0 so removal lands last.

### 8. Python baseline **[settled]**
`requires-python = ">=3.12"`. The local interpreter is 3.14 and `pillow-heif` has
3.14 wheels, but 3.12 is the conservative floor for contributors and CI runs both.

---

## 6. Deletion is always recoverable

Added after the specification was written. It splits into two separate rules that
are easy to conflate.

### Rule A: the tool never unlinks user media on the Mac

Any file containing user media that the tool removes from the Mac goes to the macOS
Trash via `NSFileManager.trashItem`, never `unlink`. It is then restorable by the
user from Finder in the ordinary way, and on an external archive volume it lands in
that volume's `.Trashes`.

This applies to:

- a duplicate archive file collapsed by `clean duplicates`;
- an archive file replaced because its hash no longer matches;
- any archive pruning the user initiates.

It deliberately does **not** apply to scratch files: `.partial` fragments, a failed
download, or a corrupt temp file are not user media and are unlinked directly.
Trashing them would bury the Trash in junk and make the feature useless when it
actually matters.

### Rule B: removal from the iPhone leaves a recycle bin record on the Mac

When an asset is removed from the device, the tool writes a recycle bin entry at
`<recycle_bin.path>/<campaign>/<date>/` holding:

- a hard link to the verified archive file, so the entry costs no extra disk on the
  same volume, and a copy when the archive is on a different volume;
- a JSON manifest row with the asset id, device asset id, original device path,
  SHA256, capture date, classification, the evidence that made it eligible, and the
  removal timestamp.

This gives a real answer to "what did I take off the phone in September, and where
are those files now", which the archive alone cannot answer once the asset is gone
from the device.

`recycle-bin list` shows entries, `recycle-bin restore` copies files back out to a
chosen folder, and `recycle-bin empty` clears entries past the retention window.
Restore puts files on the Mac. It does **not** push media back onto the iPhone;
that is out of scope and the command says so.

### Configuration

```yaml
recycle_bin:
  enabled: true
  path: "~/.iphone-image/recycle-bin"
  retention: 90d              # or: never
  use_macos_trash: true       # Rule A. Turning this off is not recommended.
```

Both rules are on by default. Neither can be disabled by a command line flag, only
by config, and `config validate` warns when either is off.

---

## 7. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| Proxy detection false negative leads to deleting a full-res original | Irrecoverable data loss | Permanent removal block, no override, conservative threshold, plus the iCloud sync confirmation as a second gate |
| ImageCaptureCore cannot delete reliably | Removal, the headline feature, is unshippable | P0 question 4 answers this before any removal code exists |
| ImageCaptureCore enumeration is incomplete vs the real library | Silent partial backup presented as complete | Cross-check against AFC enumeration on every scan, report any delta as a blocking finding |
| Multi-hour USB transfers destabilise or the device drops | Sync never completes on large libraries | Conservative default worker count, checkpoint after every asset, throughput measured in P0 question 8 |
| Helper binary blocked by Gatekeeper on other Macs | Nobody outside this machine can run it | Signing and notarisation scoped into P11, distribution tested on a second Mac |
| WhatsApp classification ships inert | Advertised feature does nothing | Conflict 1 resolution, and honest README wording |
| Deletion bug found only on a real library | Catastrophic and public | Fake backend plus dedicated test iPhone, harness before feature, P10 gate |
| Reverse geocoding leaks location data to a third party | Privacy violation in a privacy-first tool | Offline bundled dataset, no network call at all |
| A bug unlinks archive files instead of trashing them | User media gone with no Finder undo | Single choke point for media deletion, asserted by test, direct `unlink` of archive paths banned by a lint rule |

---

## 8. Testing

- **Unit**, per spec section 51: hashing, path generation, retention arithmetic,
  eligibility rules, campaign membership, collision suffixes.
- **Fake device backend** implementing the same protocol as the real one, so every
  resume, interruption and removal path is testable in CI with no hardware.
- **Media fixtures**: JPEG, HEIC, MOV, MP4, RAW, Live Photo pair, screenshot,
  edited image with `.AAE`, burst, WhatsApp import, exact duplicate, plus a
  synthetic file over 1 GB generated at test time rather than committed.
- **Destructive harness** in `tests/destructive/`, covering all eight scenarios in
  spec section 52. Refuses to run against any device not on an explicit allowlist.
- **Property test:** interrupt local sync at a random point, resume, assert the
  result is identical to an uninterrupted run.

---

## 9. Conventions

- One logical change per commit. No pushes without explicit instruction.
- Every gate fails loudly. A checker that cannot run exits non-zero and says why.
  It never passes by default and never reports success on absent output.
- Every command supports `--json` so the tool is scriptable and testable.
- Nothing touching the device is written without a corresponding journal entry.
