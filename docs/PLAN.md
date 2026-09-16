# Implementation Plan

Plan of record for delivering `docs/SPEC.md`.

**Status:** P0 complete and it changed the architecture. P1 foundation complete.
P2 onwards is re-cut around PhotoKit.
**Last updated:** 2026-09-16

---

## 1. What P0 decided

The spike ran against a real iPhone 15 Pro Max on iOS 26.6.2. Full evidence in
[`spikes/P0-transport.md`](../spikes/P0-transport.md). The short version:

**The USB premise failed on two independent blockers.**

| | |
|---|---|
| Photos reports | 94,180 items |
| ImageCaptureCore over USB exposes | 1,796 assets, 7.5 GB, **1.9%** |
| `canDeleteOneFile` | **false** |

Everything else lives in iCloud and a cable cannot reach it, and the device
refuses deletion outright. The removal feature as designed was unbuildable.

**The replacement is PhotoKit against the Mac's Photos library**, which reaches
the whole library and carries the metadata USB never had. Most importantly the
source application:

```
net.whatsapp.WhatsApp    25,546   (57% of a 44,973-asset sample)
com.google.chrome.ios       347
com.toyopagroup.picaboo      86
(no bundle id)           18,870   camera originals
```

This is not a heuristic. It is `ZIMPORTEDBYBUNDLEIDENTIFIER`, recorded by iOS.

### Decisions taken

| # | Decision | Choice |
|---|---|---|
| 1 | Transport | PhotoKit on the Mac, not USB. P0 killed the USB design. |
| 2 | Library residency | Photos stays in Optimize Mac Storage. Originals are fetched **per asset on demand**, so the full 424 GB never has to be on disk. |
| 3 | Work unit | Budgeted chunks, `--budget 50GB`, resumable between chunks. |
| 4 | Ordering | Photos before video (video is 73% of bytes from 5% of items), oldest first within each. |
| 5 | Selection | One selector across `list`, `sync` and `remove`. Channel, type, age, size, order, budget. |
| 6 | Removal sequence | fetch to Mac, review, delete from device, move to recycle bin. **No exceptions**, `--discard` was proposed and rejected. |
| 7 | Destinations | `sync` writes the archive and mirrors to cloud. `remove` writes the recycle bin and does not. |
| 8 | iCloud proxies | Back up, permanently block from removal, no override flag. |
| 9 | iCloud sync propagation | Detect, state it in the plan, typed confirmation for `--apply`. |
| 10 | WhatsApp | HIGH confidence via source bundle id. Policy-driven cleanup works as spec section 2.2 wrote it. |
| 11 | Media presentation | Always `.originalAssets`, read back and verified, mismatch fatal. |
| 12 | Mac-side deletion | Never `unlink` user media. macOS Trash via `NSFileManager.trashItem`. |
| 13 | Exact duplicates | One archive file per unique SHA256, N asset rows. Requires downloading, no device fingerprint exists. |
| 14 | Cloud transport | rclone. The tool never holds a token. |
| 14b | Local disk is reclaimed each cycle | The archive is a **staging buffer, not a destination**. Once an asset is cloud-verified and gone from the phone, the Mac copy is released and the recycle bin keeps only its manifest row. Junk that was never uploaded keeps its bytes. Invariant: two independent copies at all times. Settled 2026-09-16; the plan had never said what frees the disk, and the recycle bin's hard link would have stalled the second cycle. |
| 15 | Reverse geocoding | Offline bundled dataset. No network. |
| 16 | Python baseline | 3.12 floor, CI on 3.12, 3.13 and 3.14. |
| 17 | Distribution | Helper built from source. No Apple Developer account, no signing. |

---

## 2. The selector

One vocabulary, three verbs. What you previewed is literally what acts.

```bash
iphone-image list   <selector>            # harmless, always available
iphone-image sync   <selector> --budget   # fetch down, archive, mirror to cloud
iphone-image remove <selector> --apply    # the four-step sequence in section 4
```

| Dimension | Values |
|---|---|
| `--source` | `camera`, `whatsapp`, `snapchat`, `safari`, `chrome`, `messages`, or a raw bundle id |
| `--type` | `photo`, `video`, `live`, `screenshot`, `raw` |
| age | `--older-than 1y`, `--newer-than 30d`, `--year 2019` |
| size | `--min-size 10MB`, `--max-size 500KB` |
| order | `--order oldest\|newest\|largest` |
| budget | `--budget 50GB` for sync, `--limit 50` for remove |
| protection | `--no-favourites`, `--no-proxy-suspect` |
| layout | `--pattern "{year}"` or `"{year}/{month}"` |

Worked examples, from the requirements as stated:

```bash
# 50 GB of camera photos, oldest first, deduplicated, in date buckets
iphone-image sync --source camera --type photo \
    --order oldest --budget 50GB --dedupe --pattern "{year}/{month}"

# WhatsApp images over a year old
iphone-image remove --source whatsapp --type photo --older-than 1y

# WhatsApp videos over a year old and larger than 10 MB
iphone-image remove --source whatsapp --type video --older-than 1y --min-size 10MB
```

**Filters narrow an already-eligible set.** No selector can make a blocked asset
removable. Proxy suspects, unverified assets and incomplete groups stay blocked
whatever the filter says.

---

## 3. Chunking

The library is roughly 424 GB against 95 GB of free disk, so "download it all,
then process" was never viable. Instead the Mac's Photos library stays in
**Optimize Mac Storage** and originals are pulled one asset at a time via
`PHAssetResourceManager` with `isNetworkAccessAllowed`.

```yaml
chunking:
  enabled: true
  chunk_bytes: 50GB
  order: [photo, video]
  within_type: oldest_first
  free_space_floor: 20GB
```

A chunk is: select by the selector until the byte budget is reached, fetch each
original, hash it, write it to its destination, verify, record it, release. The
next chunk resumes from the ledger. `free_space_floor` refuses to start a chunk
that would breach it, which replaces spec section 42's whole-library estimate.

**Photos before video is not arbitrary.** In the device sample, videos were 73%
of the bytes from 5% of the items. Photos-first completes ~95% of the item count
for ~27% of the bytes, so the slow expensive part is isolated at the end where it
can be decided on separately.

**Oldest first within a type**, because if the job is ever abandoned half done,
the oldest material is the least replaceable and the most likely to be an
offloaded proxy.

---

## 4. Removal: the four-step sequence

This is the strongest rule in the project and it has no exceptions.

```
1. fetch      the selected assets come down to the Mac
2. review     the user inspects them and approves
3. remove     they are deleted from the device
4. recycle    the Mac copy moves to the recycle bin, with retention
```

Nothing is ever deleted from the phone that is not already on the Mac. A
`--discard` flag that would have skipped step 1 for junk categories was proposed
and **rejected**.

This also solves where WhatsApp bulk goes. It lands in the recycle bin, not the
archive, so ~70 GB of forwarded images never reaches Google Drive and ages out
after the retention window instead of being kept forever.

An asset that should be both archived and removed is `sync`ed first. What the
remove step then does with the Mac copy is decision 14b: released once the asset
is cloud-verified, because Drive plus Recently Deleted is already two independent
copies and the third is the disk the next chunk needs. Junk that was never
uploaded keeps its bytes and hard links the archive file where one exists.

**This is what makes the product work on a Mac with no room for the library.**
Each cycle fetches a chunk, uploads what is worth keeping, removes it from the
phone and hands the disk back. The archive is a staging buffer, not a
destination.

---

## 5. Milestones

| Milestone | Contains | The promise it makes |
|---|---|---|
| **v0.1 Inventory** | P1, P2, P3, P4 | Tells you what is in your library, and lets you filter it. Touches nothing. |
| **v0.2 Archive** | P5, P6 | Fetches it down in budgeted chunks, verified, organized, deduplicated. |
| **v0.3 Cloud** | P7, P8, P9 | Mirrors to Google Drive, reconciles every view, campaigns. |
| **v1.0 Offload** | P10, P11 | The four-step removal sequence, resumable, provable. |

Removal is written last and is gated on its own test harness existing first.

---

## 6. Phases

### P1. Foundation **[done]**
Config, SQLite schema and migrations, operations journal, CLI skeleton, path
builder, retention parsing, CI.

111 tests, ruff and mypy clean, CI on macOS across Python 3.12 to 3.14. The
example config that `config init` writes is itself validated in CI, so the
documentation cannot drift from what the loader accepts.

### P0b. PhotoKit spike **[next, blocking]**
Against the existing library, before any hardware is bought.

| # | Question | Why it blocks |
|---|---|---|
| 1 | Does `PHAssetResourceManager` with `isNetworkAccessAllowed` actually fetch a non-local original, and at what sustained rate? | The entire chunking design depends on it. If Apple throttles it to a trickle, the schedule changes. |
| 2 | Is `PHAssetChangeRequest.deleteAssets` permitted, read from authorisation rather than by deleting? | Removal, again. USB already said no once. |
| 3 | Does `PHAssetMediaSubtype.photoScreenshot` classify screenshots reliably? | Screenshot cleanup. |
| 4 | Does PhotoKit expose the source app, or must it come from `Photos.sqlite`? | The WhatsApp feature. The database has it; the public API may not. |
| 5 | Album membership, favourites, burst, GPS coverage across the real library. | Selector dimensions and the never-remove-favourites rule. |
| 6 | Does a CLI binary get Photos authorisation with an embedded `Info.plist`, or is an app bundle required? | Distribution. |

**Exit:** a findings document, and a go/no-go before the user spends money on an
external drive and a week of downloading.

### P2. Library layer
`PhotoKitBackend` and `FakeLibraryBackend` behind one protocol. Authorisation,
library change observation, and a read-only reader for the `Photos.sqlite` fields
PhotoKit does not expose.

### P3. Inventory
`scan` populating the ledger from the whole library. Proxy suspicion scoring.
Asset group construction. Two scans of an unchanged library must produce zero row
changes other than `last_seen_at`.

### P4. Classification and the selector
Channel from the source bundle id, type, screenshot subtype, camera originals
from "no bundle id plus `IMG_*`". `list` with the full selector, non-destructive,
shipping in v0.1 so a filter is checked long before removal is typed.

### P5. Chunked fetch
Budgeted chunks, on-demand original fetch, `.partial` staging, SHA256 verify,
atomic rename, archive path building, collision handling, free-space floor.
**Exit:** kill at 50% of a chunk, rerun, result byte-identical to an
uninterrupted run.

### P6. Exact deduplication
Hash after download, since no device-side fingerprint exists. One archive file
per unique SHA256, N asset rows. Collapsed duplicates go to the macOS Trash.

### The release step **[done]**
`iphone-image release`, decision 14b. Trashes the local copy of any asset whose
cloud copy the remote confirms **in that same invocation** -- a ledger row is a
claim about when it was written, and this deletes the only other copy. Files go
to the macOS Trash through the Swift helper's `trash` command, never `unlink`.
`RELEASED` is terminal: `sync` excludes it as firmly as `LOCAL_VERIFIED`, or the
cycle would fetch, release and fetch the same asset forever.

### P7. Cloud
`CloudProvider` protocol, `RcloneProvider`, Google Drive first. Archive only;
the recycle bin is never mirrored.

**This is the phase that unblocks everything else**, because nothing can be
released from the Mac or removed from the phone until a second copy is proven to
exist somewhere. Prerequisite outside the code: an rclone remote the user
configures themselves, since decision 14 is that the tool never holds a token.

### P8. Verify and report
Reconcile library, archive, cloud and ledger. Every blocked asset individually
listable with its reason.

### P9. Campaigns
Membership frozen at start. Assets added later are excluded from the default plan.

### P10. Removal
Gated: `tests/destructive/` must exist and pass first. The four-step sequence,
fresh reconciliation, typed confirmation while iCloud sync is on, per-asset
journal writes, resumable, group-aware.

### P11. Release
Install docs, Homebrew tap, README, CHANGELOG.

---

## 7. Spec conflicts and resolutions

### 1. WhatsApp cleanup **[resolved by P0, in the spec's favour]**
Originally recorded as unresolvable: the source bundle id lives in
`Photos.sqlite`, unreadable over USB, so WhatsApp classification would top out at
MEDIUM and `clean whatsapp` would ship inert.

PhotoKit removes the constraint. `ZIMPORTEDBYBUNDLEIDENTIFIER` is populated on
58% of assets and names `net.whatsapp.WhatsApp` on 25,546 of them. Classification
is HIGH confidence from the source application itself. **Spec section 2.2 works
exactly as written** and the review-only demotion is withdrawn.

### 2. iCloud optimized proxies **[settled]**
Detect by bytes-per-pixel and dimension heuristics, back up normally, block from
removal permanently, surface the list explicitly. 27% of the device sample fell
below the threshold, so this is not theoretical.

### 3. iCloud Photos sync propagation **[settled]**
`iCloudPhotosEnabled` is true on this device. Deleting propagates to iCloud and
every device. Typed confirmation required for `--apply`.

### 4. Metadata availability **[resolved by P0]**
Over USB, almost nothing: `originatingAssetID`, `fingerprint`, `gpsString`,
`exifCreationDate`, `pairedRawImage` and `fileSystemPath` all read 0.0%, despite
being declared in the SDK headers. Via PhotoKit and `Photos.sqlite`: source app,
albums, favourites, burst, GPS on 30% of assets, original filename and size.
**A header declaring a property is not evidence a device populates it.**

### 5. "Estimated storage recovered" **[settled]**
Reworded. iOS holds deleted assets in Recently Deleted for up to 30 days, so
space is not reclaimed at the moment of removal.

### 6. `local_objects` **[settled]** Dropped, `asset_resources` covers it.

### 7. Twenty items as one release **[settled]** Four releases, removal last.

### 8. Python baseline **[settled]** 3.12 floor, CI on 3.12 to 3.14.

### 9. ImageCaptureCore transcodes by default **[settled]**
`mediaPresentation` defaults to `ConvertedAssets`, handing out JPEG transcoded
from HEIC and H.264 from HEVC. Confirmed on device: `supportsHEIF` true,
`.originalAssets` accepted and read back. Must be set explicitly; a mismatch is
fatal. Carried into the PhotoKit design, where the equivalent is requesting the
original resource rather than a derivative.

---

## 8. Risk register

| Risk | Impact | Mitigation |
|---|---|---|
| On-demand iCloud fetch is throttled to a trickle | The whole chunking design is slow rather than practical | P0b question 1 measures it before any money is spent |
| PhotoKit deletion is also refused | Removal is unbuildable by any route | P0b question 2, before P10 is scheduled |
| Proxy detection false negative deletes a full-res original | Irrecoverable | Permanent removal block, no override, plus the four-step sequence and Recently Deleted |
| The Mac's Photos library is stale or detached | We plan against the wrong inventory | Measured: 44,973 assets, zero videos, newest 2024-06-20, against 94,180 on the phone. Must be reconciled before P3 |
| Disk exhaustion mid-chunk | Corrupt or partial archive | `free_space_floor`, refuse to start rather than fail midway |
| A bug unlinks archive files instead of trashing them | User media gone with no Finder undo | Single choke point, asserted by test, direct `unlink` of archive paths banned by lint |
| Removal runs against the user's only iPhone | No second chance | Four-step sequence, default limit 50, batches shown in full, recycle bin written first |
| Reverse geocoding leaks location to a third party | Privacy violation in a privacy-first tool | Offline bundled dataset, no network call |

---

## 9. Testing

- **Unit:** hashing, path generation, retention arithmetic, selector compilation,
  eligibility rules, campaign membership, collision suffixes, chunk budgeting.
- **Fake library backend** implementing the same protocol as PhotoKit, so every
  resume, interruption and removal path is testable in CI with no hardware and no
  network.
- **Media fixtures:** JPEG, HEIC, MOV, MP4, RAW, Live Photo pair, screenshot,
  edited with `.AAE`, burst, WhatsApp import, exact duplicate, and a synthetic
  file over 1 GB generated at test time rather than committed.
- **Destructive harness** in `tests/destructive/`, covering all eight scenarios in
  spec section 52 plus the four-step sequence, refusing to run against any
  library not on an explicit allowlist.
- **Property test:** interrupt a chunk at a random point, resume, assert the
  result is identical to an uninterrupted run.

---

## 10. Conventions

- One logical change per commit. No pushes without explicit instruction.
- Every gate fails loudly. A checker that cannot run exits non-zero and says why.
  It never passes by default and never reports success on absent output.
- Every command supports `--json`.
- Nothing touching the library is written without a corresponding journal entry.
- **Long-running work reports progress from a poll, not a callback.** A callback
  cannot report its own absence, and P0 lost four minutes to a delegate that
  never fired.
