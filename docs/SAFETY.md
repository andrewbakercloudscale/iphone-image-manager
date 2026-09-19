# Safety Model

This document exists because the tool can delete photographs. Everything here is
about the ways that could go wrong, and what the tool does about each one.

The core promise, from the specification:

> iPhone Image Manager will never remove an image or video merely because it has
> been seen, copied, or uploaded. Removal is a separate, explicit operation and is
> only offered when the configured verification requirements have been satisfied.

---

## 1. The default is to keep everything

`remove_from_iphone.policy` defaults to `never`. Backup, verification and cloud
upload never imply deletion. The user has to change the policy in config AND run
`remove-from-iphone --apply` before a single byte is removed from the device.

---

## 1b. The four-step removal sequence

The strongest rule in the project. It has no exceptions.

```
1. fetch      the selected assets come down to the Mac
2. review     the user inspects them and approves
3. remove     they are deleted from the device
4. recycle    the Mac copy moves to the recycle bin, with retention
```

**Nothing is ever deleted from the phone that is not already on the Mac.** A
`--discard` flag that would have skipped step 1 for junk categories was proposed
and rejected.

### The one exception, decided by the owner on 2026-09-19

This section said "no exceptions" and it was true for as long as the Mac copy
outlived the removal. It stopped being true by design: the cycle *releases* the
Mac copy the moment Drive verifies it, because the Mac has no room to hold a
library and there is no external drive. By the time removal was first run over
the older photos, 14,729 of them (45.5 GiB) were on the phone, verified in
Drive, and had no Mac copy -- and the tool cannot fetch a RELEASED asset again,
so "already on the Mac" could only be satisfied by re-downloading all of it
purely to have something to delete, and then releasing it again.

The owner chose to trust a fresh Drive hash instead. It is narrow, and each
edge below is a test in `tests/destructive/test_remove_from_iphone.py`:

- **Only under `cloud_verified`.** `local_verified` promises a Mac copy, and a
  released asset has none.
- **Only for RELEASED assets.** A LOCAL_VERIFIED row whose file has vanished is a
  surprise, not a decision, and is still refused ("the local copy is missing").
  A never-fetched asset is still refused ("no verified local copy").
- **The remote becomes the only evidence, so it cannot be skipped or guessed
  at.** The hash is re-read from Drive in the same invocation as the deletion,
  it must match the ledger, and an empty hash on either side is not a match --
  with nothing else to compare, `"" == ""` would otherwise read as agreement.
- **Nothing else is waived.** The fresh scan (section 7), proxy and favourite
  protection (sections 2 and 6), Live Photo and burst completeness, and the
  typed confirmation phrase all still apply, and the macOS confirmation dialog
  is still Apple's, not ours.
- **It says so afterwards.** Every deletion's `deletion_events.evidence` carries
  `"basis": "remote_hash_only"` (or `"local_and_remote"`) and
  `"local_hash_rechecked": false`, so a photograph deleted without a Mac copy
  states that, and why it was allowed, for as long as the ledger exists.

What this gives up, stated plainly: for those assets the *only* copies are
Google Drive and, for 30 days, the phone's Recently Deleted. The Mac copy was
already gone before removal ran; the exception does not remove it, it stops
pretending it is there. `iphone-image audit` is the check that proves Drive
still holds what the ledger says, and should be run before any removal pass.

Step 4 is why bulk categories do not pollute the archive. WhatsApp media removed
from the phone lands in the recycle bin, not the archive, so it is never mirrored
to cloud storage and it ages out after the retention window rather than being
kept forever. An asset that should be both archived and removed is `sync`ed
first; whether its recycle bin entry keeps the bytes then depends on the cloud
copy, per section 5.

---

## 2. iCloud "Optimize iPhone Storage" and proxy files

This is the single largest risk in the whole product.

**What happens.** iPhone Settings > Photos offers "Download and Keep Originals" or
"Optimize iPhone Storage". Under Optimize, when the device is short on space, iOS
replaces the on-device full-resolution original with a smaller stand-in. Roughly
screen-resolution JPEG for photos, a lower bitrate encode for video. The real
original then exists only in iCloud.

**Why it matters here.** A Mac connected over USB can only copy files that
physically exist on the device. It cannot reach into iCloud. So for an optimized
asset, the tool copies the proxy, hashes the proxy, verifies the proxy, and uploads
the proxy. Every internal check passes. The tool would then declare the asset
"verified" and offer it for removal, and deleting it from a synced iCloud library
destroys the full-resolution original everywhere.

**What the tool does.** Every asset is scored for proxy likelihood at scan time and
carries a `proxy_suspicion` value:

| Signal | Weight |
|---|---|
| Pixel dimensions below the camera model's native sensor resolution | strong |
| Byte size far below the expected range for those dimensions and that format | strong |
| JPEG where the device and iOS version would have produced HEIC | moderate |
| EXIF `Make`/`Model` present but no lens or capture settings | moderate |
| Video bitrate far below what the source format implies | moderate |
| Missing RAW or `.AAE` companion where the asset is otherwise a full capture | weak |

Measured on a real device: **487 of 1,796 assets, 27%**, fell below 0.12 bytes
per pixel, including a 4000x3000 JPEG at 409 KB that cannot be a full-resolution
original. This is not theoretical.

Assets over the threshold are flagged `SUSPECTED_PROXY` and:

- are still backed up locally and to the cloud, normally;
- are **permanently blocked from removal** in v1, with no override flag;
- are listed explicitly by `iphone-image status --proxies` and appear as a named
  blocked category in every removal plan.

The block is deliberately not overridable in v1. Detection is heuristic and a false
negative destroys an irreplaceable original.

---

## 2b. The API hands out transcodes by default

A second way to back up something that is not your photograph, entirely separate
from iCloud and present even when Optimize Storage is off.

`ICCameraDevice.mediaPresentation` defaults to `ICMediaPresentationConvertedAssets`.
In that mode the device hands out **JPEG transcoded from your HEIC originals, and
H.264 transcoded from your HEVC video**. The transcode is what gets copied, hashed,
verified and uploaded. Every check passes, because the transcode is the only thing
the tool ever saw.

Confirmed on the test device: `supportsHEIF` is true, `.originalAssets` was
accepted, and the value read back as `original`. Left alone it would have read
`converted`.

**What the tool does.** The original variant is requested explicitly, the value
actually in effect is read back, and a mismatch is a hard error that stops the
run. It is recorded on every scan, so the ledger can prove which variant each
asset was captured as, and a row captured as a derivative can never satisfy a
verification requirement.

The same hazard exists in PhotoKit, where the equivalent is requesting the
original `PHAssetResource` rather than a derivative rendition.

---

## 3. iCloud Photos sync propagation

If iCloud Photos is enabled, the on-device library is not a local copy. It is one
replica of a synced library. Deleting an asset on the phone deletes it from iCloud
and from every other device signed into that account. This is a different and much
larger action than "freeing space on one phone".

**What the tool does.**

- Detects whether iCloud Photos sync is active. Confirmed true on the test
  device: `ICCameraDevice.iCloudPhotosEnabled` reported `true`.
- States the consequence in plain language at the top of every removal plan.
- Requires an explicit typed confirmation phrase for `--apply` while sync is on.
  A `--yes` style flag is not sufficient.

---

## 4. Recently Deleted

iOS moves deleted assets to "Recently Deleted" and holds them for up to 30 days.
Two consequences:

- Storage is **not** reclaimed at the moment of removal. Removal plans report
  "storage reclaimed after Recently Deleted is emptied, up to 30 days" rather than
  an immediate figure.
- There is a 30 day window in which a mistaken removal can be undone from the
  phone. The tool tells the user this after a removal run, because it is the single
  most useful piece of recovery information at that moment. It is not the only one:
  see section 5 for the Mac side.

---

## 5. Nothing is unlinked, on either side

There are two separate undo paths and they are easy to confuse.

**On the phone**, iOS gives you Recently Deleted, covered in section 4 above.

**On the Mac**, two rules apply:

- The tool never `unlink`s a file containing user media. Anything it removes from
  the Mac goes to the macOS Trash, restorable from Finder. That covers duplicate
  archive files collapsed by `clean duplicates`, an archive file replaced because
  its hash stopped matching, and any archive pruning you ask for. Scratch files,
  meaning `.partial` fragments and failed downloads, are unlinked directly, because
  filling the Trash with junk is how a safety feature stops being used.
- Every asset removed from the iPhone leaves a recycle bin entry on the Mac, under
  `~/.iphone-image/recycle-bin/<campaign>/<date>/`. It always records a manifest row
  with the asset id, original device path, SHA256, capture date, classification, the
  evidence that made it eligible, and when it was removed.

  **Whether it also keeps the bytes depends on whether a cloud copy exists.**

  | | Drive | phone | Mac |
  |---|---|---|---|
  | cloud-verified | verified | Recently Deleted, 30 days | **released** |
  | never uploaded | none | Recently Deleted, 30 days | bytes kept for the retention window |

  The invariant is **two independent copies at all times**, and the Mac is only
  ever the third. For an asset verified in the cloud, holding a third copy buys
  nothing and costs the disk the next chunk needs. For junk that was deliberately
  never uploaded, the recycle bin is the only copy besides Recently Deleted, so it
  keeps the bytes and hard links the archive file where one exists.

  This is what lets the whole library be processed on a Mac with 30 GB free and no
  external drive: the archive is a staging buffer, not a destination. An earlier
  version of this document said the entry always hard links the archive file and
  holds it for 90 days, which would have stalled the second cycle.

The recycle bin entry is written and flushed to disk **before** the deletion call
reaches the device. A crash between the two leaves a spare record, never a hole.

`iphone-image recycle-bin list | restore | empty`. Restore puts files back on the
Mac. It does not push media back onto the iPhone, and the command says so rather
than implying otherwise.

Retention defaults to 90 days and can be set to `never` to keep entries forever.
Both rules are configured in `recycle_bin:` and neither has a command line
override.

---

## 6. Evidence based eligibility

An asset is only eligible for removal when the database holds positive evidence for
every condition the configured policy requires. Never a filename, never a path,
never an inference.

Blocking conditions, any one of which is fatal to eligibility:

- Not present in the current, fresh device scan.
- Local copy missing, incomplete, or hash mismatched.
- Cloud copy missing or unverified, when policy is `cloud_verified`.
- Any resource in the asset group unverified. A Live Photo whose `.MOV` half is not
  verified is not removable, even if the `.HEIC` is.
- Flagged `SUSPECTED_PROXY`.
- Classification confidence below the policy threshold, for classification driven
  cleanups.
- Asset created after the campaign started, unless `--include-new` is given.

Every blocked asset is individually listable with its reason. A count alone is not
acceptable output.

---

## 7. Mandatory final reconciliation

A removal plan is always computed against a scan taken in that same invocation.
Stale database state is never used to authorize deletion. If the device disconnects
during the final scan, no plan is produced.

---

## 8. Removal happens through Apple's API, not by unlinking files

Deleting files directly from the device filesystem over AFC removes the bytes but
leaves the on-device Photos database referencing assets that no longer exist. That
produces broken entries and unreclaimed space. It is never done.

The original plan was to delete through ImageCaptureCore, the framework Apple's
own Image Capture application uses. **The P0 spike proved that impossible**: the
test iPhone reports `canDeleteOneFile: false` and `canDeleteAllFiles: false` from
a valid session on an unlocked device. See `spikes/P0-transport.md`.

Removal therefore goes through **PhotoKit**, `PHAssetChangeRequest.deleteAssets`,
against the Mac's Photos library, which syncs the deletion to iCloud and to every
device. Whether that is permitted is P0b question 2, and it is answered before
any removal code is written.

---

## 9. Never tested first on a real library

Removal code is developed against a fake device backend and a dedicated test iPhone.
The destructive test harness is a prerequisite for the removal feature, not a
follow up to it. See `docs/PLAN.md`, phase P10.
