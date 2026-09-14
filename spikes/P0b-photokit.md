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
