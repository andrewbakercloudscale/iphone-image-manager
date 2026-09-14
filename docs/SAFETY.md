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

Assets over the threshold are flagged `SUSPECTED_PROXY` and:

- are still backed up locally and to the cloud, normally;
- are **permanently blocked from removal** in v1, with no override flag;
- are listed explicitly by `iphone-image status --proxies` and appear as a named
  blocked category in every removal plan.

The block is deliberately not overridable in v1. Detection is heuristic and a false
negative destroys an irreplaceable original.

---

## 3. iCloud Photos sync propagation

If iCloud Photos is enabled, the on-device library is not a local copy. It is one
replica of a synced library. Deleting an asset on the phone deletes it from iCloud
and from every other device signed into that account. This is a different and much
larger action than "freeing space on one phone".

**What the tool does.**

- Detects whether iCloud Photos sync is active.
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
  most useful piece of recovery information at that moment.

---

## 5. Evidence based eligibility

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

## 6. Mandatory final reconciliation

A removal plan is always computed against a scan taken in that same invocation.
Stale database state is never used to authorize deletion. If the device disconnects
during the final scan, no plan is produced.

---

## 7. Removal happens through Apple's API, not by unlinking files

Deleting files directly from the device filesystem over AFC removes the bytes but
leaves the on-device Photos database referencing assets that no longer exist. That
produces broken entries and unreclaimed space.

Removal is therefore performed through ImageCaptureCore, the same framework Apple's
own Image Capture application uses, so iOS maintains its own consistency.

---

## 8. Never tested first on a real library

Removal code is developed against a fake device backend and a dedicated test iPhone.
The destructive test harness is a prerequisite for the removal feature, not a
follow up to it. See `docs/PLAN.md`, phase P10.
