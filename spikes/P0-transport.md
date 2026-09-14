# P0 transport spike: findings

**Run:** 2026-09-14, iPhone 15 Pro Max (iPhone16,2), iOS 26.6.2, macOS 26.5.2, SDK 26.2.
**Verdict:** the USB / ImageCaptureCore premise **fails** for this library. Two
independent blockers, either of which alone would be fatal.

---

## 1. Blocker: visibility

| | |
|---|---|
| Photos app reports | **94,180 items** |
| ImageCaptureCore over USB exposes | **1,796 assets, 7.5 GB** |
| Coverage | **1.9%** |

Everything else has no on-device original. It lives in iCloud, and a USB
connection cannot reach it. The cached set is not even "recent photos": it spans
2019-06-07 to the day of the run.

Composition of what is visible, by extension (the API's own `uti` is only ever
`public.image` or `public.movie`, so it cannot tell these apart):

| | count |
|---|---|
| JPG | 1,314 |
| PNG | 312 |
| HEIC | 71 |
| MP4 | 68 |
| MOV | 31 |

Only 71 HEIC on a phone that shoots HEIC, and 856 files named `XXXX9999.JPG`,
says most of what is cached is saved or shared media, not camera originals.

## 2. Blocker: deletion is not permitted

```
canDeleteOneFile   false
canDeleteAllFiles  false
capabilities       ["ICCameraDeviceSupportsHEIF", "ICCameraDeviceCanAcceptPTPCommands"]
```

Read from a valid session on an unlocked device with a complete catalog. This is
a real answer, not the locked-state artefact described in section 5 below.
**Removal through ImageCaptureCore is impossible on this hardware**, which was the
entire planned architecture for the removal feature.

---

## 3. Metadata: the headers lied by omission

Properties the SDK declares, which this device never populates:

| Field | Claimed use | Actual coverage |
|---|---|---|
| `originatingAssetID` | stable primary key | **0.0%** |
| `fingerprint` | dedupe with no download | **0.0%** |
| `gpsString` | GPS without downloading | **0.0%** |
| `pairedRawImage` | RAW pairing | **0.0%** |
| `exifCreationDate` | capture time | **0.0%** |
| `fileSystemPath` | device path | **0.0%** |

What does survive:

| Field | Coverage | Note |
|---|---|---|
| `relatedUUID` | 100% | the real Photos identifier, `UUID/L0/001`. 1,679 distinct, 117 shared across assets, so grouping works |
| `originalFilename` | 100% | |
| `creationDate`, `fileCreationDate` | 100% | |
| `width`, `height`, `uti` | 100% | `uti` is coarse, see above |
| `sidecarFiles` | 5.8% | |
| `groupUUID` | 0.4% | |
| `burstUUID` | 0.1% | |

**The lesson:** a header declaring a property is not evidence that a device fills
it. Everything in `docs/PLAN.md` section 2 that was inferred from headers had to
be re-derived from a real device, and most of it was wrong.

---

## 4. Confirmed hazards

- **`ICMediaPresentation` defaults to `ConvertedAssets`.** `supportsHEIF` is true
  on this device, `mediaPresentation` accepted `.originalAssets`, and the value
  read back as `original`. So the transcoding hazard in `docs/SAFETY.md` section
  2b is real and avoidable, but only if explicitly set.
- **`iCloudPhotosEnabled` is true.** The library is a synced replica. Deleting on
  the phone deletes from iCloud and every other device.
- **Proxy suspicion is measurable.** 487 of 1,796 assets (27%) fall below 0.12
  bytes per pixel. The worst include a 4000x3000 JPEG at 409 KB, which cannot be
  a full-resolution original.

---

## 5. Behaviour of a locked device, and three bugs it exposed

A locked iPhone does **not** report that it is locked. It reports a *complete*
content catalog containing zero media files, with a stripped capability list in
which `canDeleteOneFile` and `supportsHEIF` both read false. The first run of this
spike reported that as a successful result.

Three defects in the spike itself, all found by running it:

1. **Reporting an empty catalog as success.** Now a zero-asset result is a
   non-zero exit with a message naming the likely cause.
2. **Aborting on lock.** `cameraDeviceDidEnableAccessRestriction` and
   `...DidRemoveAccessRestriction` fire reliably, so the probe now pauses and
   resumes rather than discarding the run. Losing an hour of cataloguing to a
   screen timeout is not acceptable behaviour, and this is a P2 requirement.
3. **`deviceDidBecomeReadyWithCompleteContentCatalog` never fires.** The catalog
   reached 100% with 1,796 files and sat there for 153 seconds with no callback.
   The probe now polls `contentCatalogPercentCompleted` and waits for the count
   to stop moving. **Do not depend on that delegate.**

Also: no progress reporting at all meant "working" and "hung" looked identical
for four minutes. The helper now emits a run-loop heartbeat every two seconds
carrying elapsed time, catalog percent, file count and lock state, independent of
any delegate callback. A callback cannot report its own absence.

---

## 6. What replaced it

The Photos library database on the Mac carries everything USB could not, most
importantly the source application:

```
net.whatsapp.WhatsApp    25,546     (57% of a 44,973-asset library)
com.google.chrome.ios       347
com.toyopagroup.picaboo      86     Snapchat
com.apple.mobilesafari       47
com.atebits.Tweetie2         13     Twitter
com.apple.MobileSMS           5     Messages
(no bundle id)           18,870     camera originals
```

`ZADDITIONALASSETATTRIBUTES.ZIMPORTEDBYBUNDLEIDENTIFIER`, populated on 58% of
assets. This overturns `docs/PLAN.md` conflict 1: WhatsApp classification is
HIGH confidence from the source app itself, not a filename heuristic, so the
policy-driven cleanup in spec section 2.2 works as originally written.

GPS is present on 30% of assets (13,685 of 44,973) in the same database.

See `docs/PLAN.md` section 3 for the resulting architecture.
