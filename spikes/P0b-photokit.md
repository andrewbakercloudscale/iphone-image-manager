# P0b PhotoKit spike: findings

**Run:** 2026-09-14, macOS 26.5.2, against the local Photos library (44,939 assets).
**Verdict:** PhotoKit is a viable transport and clears the blocker that killed the
USB design. Two questions remain unanswerable against this library.

---

## Answers

| # | Question | Answer |
|---|---|---|
| 1 | On-demand iCloud fetch rate | **UNMEASURED.** Every asset here is already local, so `--only-remote` found nothing to exercise. Needs a synced library. |
| 2 | Is `PHAssetChangeRequest.deleteAssets` permitted? | **Yes.** Round-trip proved it: created a 1x1 PNG, fetched it back, deleted it, confirmed gone. Moves to Recently Deleted. |
| 3 | Does `photoScreenshot` classify screenshots? | **Inconclusive.** 1 of 44,939. This library holds only 12 PNGs total, so it may genuinely contain no screenshots. Needs the real library. |
| 4 | Does PhotoKit expose the source app? | **No.** It must come from `Photos.sqlite`. See below. |
| 5 | Album, favourite, burst, GPS coverage | Albums yes, favourites 0.1%, bursts 0.0%, GPS 30.4%. |
| 6 | Can a CLI get Photos authorisation? | **Yes**, with `NSPhotoLibraryUsageDescription` embedded via `-sectcreate __TEXT __info_plist`. Without it the process terminates rather than prompting. |

---

## What PhotoKit gives that USB could not

| | ImageCaptureCore over USB | PhotoKit |
|---|---|---|
| Library coverage | 1.9% | 100% |
| Deletion | refused | **permitted** |
| File size without downloading | none | **100% of assets** |
| File type | `public.image` / `public.movie` | `public.jpeg`, `public.heic`, `com.apple.quicktime-movie` |
| Album membership | none | yes |
| GPS | 0.0% | 30.4% |
| Original filename | 100% | 100% |

Survey of 44,939 assets completed in 55 seconds, 46 GB, 2008-09-08 to 2024-06-20.

Sizes are exact: `requestData` returned byte counts matching the declared
`fileSize` on every sample. That is what makes chunk budgets exact rather than
estimated, and what makes "verified" mean something.

```
public.jpeg                40,102
public.heic                 4,826
com.apple.quicktime-movie     528     (Live Photo video halves)
public.png                     12
```

Smart albums are populated and usable as selector dimensions: Live Photos 528,
Portrait 326, Selfies 889, Favorites 62, Bursts 6.

---

## The source application still needs the database

PhotoKit has no API for it. The album is not a substitute:

| Source | WhatsApp assets |
|---|---|
| PhotoKit "WhatsApp" album | 13,728 |
| `ZIMPORTEDBYBUNDLEIDENTIFIER` in `Photos.sqlite` | **25,546** |

The album undercounts by 46%. So the architecture is a **hybrid**: PhotoKit for
assets, resources, fetching and deletion; a direct read-only query of
`Photos.sqlite` for the source app bundle id.

That database read is the same technique `osxphotos` uses and it is read-only,
but it is an undocumented schema and will change between macOS releases. Treat a
missing or renamed column as a degraded classification, never as a crash, and
never as "no WhatsApp assets found".

---

## Still blocking, and why the numbers cannot be invented

The chunking design rests on pulling originals from iCloud one asset at a time.
The rate at which Apple serves that is the difference between a workable tool and
one that takes a month. It cannot be measured against a library whose assets are
all local, and it cannot be estimated from anything else.

The Mac library is also detached from the phone's:

| | Mac library | iPhone |
|---|---|---|
| Assets | 44,939 | 94,180 |
| Videos | zero | yes |
| Newest | 2024-06-20 | today |

Every asset carries a `ZCLOUDASSETGUID`, so this was an iCloud library that got
disconnected rather than a separate one. **Verify the Apple ID matches the
phone's before re-enabling sync**, because enabling it against a different
account would attempt to upload 44,939 photographs to the wrong place.

---

## Measured on 2026-09-14: the fetch rate, at last

The question this spike could not answer against a fully-local library. Five
assets created after the old library's cutoff, so genuinely iCloud-only,
confirmed by a `--no-network` probe failing on all five first.

```
IMG_6698.PNG   1.31 MB   17.20s   0.07 MB/s   <- first, includes setup
IMG_6706.PNG   1.23 MB    1.28s   0.92 MB/s
IMG_6714.PNG   3.22 MB    4.52s   0.68 MB/s
IMG_6722.JPG   3.58 MB    3.88s   0.88 MB/s
IMG_6723.JPG   4.89 MB    3.12s   1.50 MB/s
             14.2 MB in 30s, sustained 0.45 MB/s
```

Excluding the first, roughly 1.0 MB/s. Apple's metadata sync was running
concurrently and competing for bandwidth, so this is a floor rather than a
ceiling.

| | at 0.45 MB/s | at 1.0 MB/s |
|---|---|---|
| one 50 GB chunk | ~32 h | ~14 h |
| all photos, 74 GB | ~47 h | ~21 h |
| whole library, 158 GB | ~100 h | ~44 h |

**Consequences for the design.** Chunking is vindicated rather than undermined:
at this rate a single unattended run of the whole library would be days long and
any interruption without resume would be catastrophic. But chunk sizing should be
chosen by wall-clock, not by disk: 50 GB is over a day. Smaller chunks give more
frequent safe stopping points for the same total time.

Worth testing before P5 ships: whether parallel fetches raise the sustained rate,
and whether it improves once the metadata sync finishes.

## Photos does not keep a second copy

The disk-doubling risk, measured rather than assumed. Across the same fetch:

```
we wrote to the archive     13 MB
Photos library grew by     -26 MB     (it shrank: Optimise evicting as it went)
total free space lost       22 MB
```

So a 50 GB chunk costs 50 GB, not 100 GB. `writeData(for:toFile:)` streams to
our destination without leaving a resident copy behind.

## Library size, corrected

An earlier estimate of ~424 GB was extrapolated from the 1,796-asset on-device
cache, which was heavily weighted toward video and badly unrepresentative. A full
scan of the real library gives:

```
78,806 assets, 158 GB
  photos  76,503    74 GB
  videos   2,303    84 GB   (53% of bytes from 3% of items)
```

Wrong by a factor of nearly three, in the expensive direction. It changes the
hardware answer: a 500 GB drive is ample where a 2 TB one was suggested.
