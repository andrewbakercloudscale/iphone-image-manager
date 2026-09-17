# Handover

Written 2026-09-17, at the end of the day the camera photo roll finished
uploading. Everything below is measured or recorded, not assumed.

---

## 1. What this is

`iphone-image` is a macOS CLI that inventories, backs up, verifies and offloads
iPhone media. Repo: **https://github.com/andrewbakercloudscale/iphone-image-manager**
(public, MIT). Working copy: `~/Desktop/github/iphone-image-manager`.

The core promise, unchanged: **nothing is ever removed from the phone that is
not already on the Mac, reviewed, and verified.** See `docs/SAFETY.md`.

---

## 2. The goal, in the user's words

> "We process this chunk, we delete files that aren't needed and upload needed
> files to my gdrive. At the end of this, I will only have one year's images on
> my phone."

Restated today, more precisely: **12 months of photos on the phone, everything
older uploaded and removed.** Cutoff as of today is **2025-09-17**.

**There is no external drive and there will not be one.** The Mac is a
temporary working copy; the archive is a staging buffer. The full cycle now
exists and ran for a whole day without manual intervention once it was
running:

```
fetch  ->  upload  ->  verify  ->  release the Mac copy  ->  next chunk
```

Every step is built. Today proved it end to end, repeatedly, unattended.

---

## 3. Where things stand right now

**The entire camera photo roll (22,888 photos, 73.77 GB) is uploaded to
Google Drive and hash-verified.** `still to fetch: 0`. This is the single
biggest milestone since the project started.

```
camera photos, total archived      22,888
  verified in Drive                22,888   73.77 GB   100%
  still on the Mac (LOCAL_VERIFIED) 5,024   14.16 GB   see section 8 -- gap found
  released (Trash, then emptied)   17,864
deleted from the iPhone             5,024   14.16 GB   Recently Deleted, 30-day window

camera video                        1,823  181.1 GB    NOT STARTED -- see section 6

Mac disk free                       26 GB
Google Drive                        5 TiB total, 3.25 TiB free (upgraded today)
```

320 tests pass, ruff clean. 44 commits, 0 unpushed, nothing uncommitted.

### What happened today, in order

1. **Fixed a log that said nothing for six hours** while the previous night's
   upload ran silently, then died on a 6-hour timeout having banked nothing.
   `fe48297`.
2. **Rebuilt the upload to bank per folder** rather than all-at-once, so an
   interruption costs a folder, not a night. `57e4415`.
3. **Fixed a rate computed from bytes that never crossed the network** --
   planned-size-over-elapsed-time for a skipped, already-uploaded folder.
   `3acf7dc`.
4. **Built P10, `remove-from-iphone`**, the last unbuilt phase. Found before
   it deleted anything: 528 Live Photos archived as the still image only, zero
   `.MOV` anywhere in the archive. Now permanently blocked. `37b980a`.
5. **Ran the first real removal: 5,024 photos deleted from the iPhone**, zero
   failures, zero collateral (verified by a before/after scan: library shrank
   by exactly 5,024, nothing else). Recently Deleted, 30-day window.
6. **Added photo/video as two separate cloud archives** (`Family Photos` /
   `Family Videos`), because every verb -- upload, release, remove -- has to
   agree on where a file lives or one of them deletes on a wrong match.
   `c1366fb`.
7. **Found and fixed a double-planning bug**: `release` and
   `remove-from-iphone` each planned twice per invocation (once for the
   preview, once inside `run`), so the two numbers printed side by side could
   disagree, and `remove-from-iphone` was scanning the whole 95,000-asset
   library twice per run. `03f39e6`.
8. **Made the PhotoKit confirmation batch size configurable** and raised it to
   10,000 live, so a removal is 1-2 macOS dialogs instead of eleven. `4e07fdf`.
9. **Found `relocate` would have silently un-named the archive.** Photos'
   place-name coverage had fallen from 69% to 24.8% as an iCloud backfill
   added 15,000 unanalysed assets, and `relocate --show` proposed moving 8,590
   files out of named folders into bare months on the strength of it.
   `relocate` now refuses any plan that removes a name; adding one is always
   allowed. `bac26e3`.
10. **Fixed a display bug live**: the per-file progress line printed the
    *chunk's running total* beside each filename, so late in a run an ordinary
    2.4 MB JPEG printed as "3.0 GB". Display only; nothing about what was
    fetched or verified was wrong. `a760f02`.
11. **Ran an unattended fetch -> upload -> release cycle** (`~/.iphone-image/cycle.sh`)
    to completion: the last 2,962 undownloaded camera photos, fetched,
    uploaded, verified, released, with a 10 GB disk floor re-checked before
    every fetch. It finished on its own and reported "nothing left to fetch."
12. **The Mac ran out of disk mid-afternoon** (117 MB free) -- not from the
    archive, from `~/Library/Caches` (21 GB, 8 GB of it Homebrew's download
    cache) on a Data volume already at 97%. Cleared 10 GB of safe,
    regenerable caches. Not a code problem; recorded here so it isn't
    mistaken for one later.

---

## 4. What to do next, in order

1. **Fix the release gap in section 8** -- five minutes of work, described
   there precisely. It is why 14.16 GB is stuck on the Mac that should already
   be free.
2. **Empty the Trash** if it hasn't been since the last release. Check
   `du -sh ~/.Trash` isn't readable from a terminal (macOS privacy) --
   check from Finder.
3. **Decide the video job**, section 6. It cannot start today's way because
   Photos' place-name coverage is still recovering (section 7). Options are
   there.
4. **Decide what to do about screenshots, WhatsApp, and screen recordings**,
   section 9 -- 74,127 items / 278 GB have no plan at all and are outside the
   12-month camera-only pipeline that exists today.
5. **A second, larger removal pass** is now realistic: with the whole camera
   roll in Drive, `remove-from-iphone --source camera --older-than 1y` would
   plan against roughly 15,000 eligible photos rather than 5,024. Not run
   today -- deliberately left for the user to authorise, per SAFETY section 3.

---

## 5. How to run it

```bash
cd ~/Desktop/github/iphone-image-manager
export PYTHONPATH=src
./.venv/bin/python -m iphone_image <command>
```

```
doctor              11 checks on the machine, says what to change
scan                inventory the library, ~2 min, no bandwidth
list <selector>     preview, harmless
sync <selector>     plan only; --apply to fetch
cloud <selector>    plan only; --apply to upload and verify
release <selector>  plan only; --apply to trash local copies the remote confirms
remove-from-iphone <selector>   plan only; --apply --confirm "<phrase>" to delete from the phone
relocate            re-file the archive after a layout change; --apply to run
                    refuses if it would remove a place name -- see section 3.9
device | status | journal | config show|validate|init
```

Selector: `--source --type --older-than --newer-than --year --min-size
--max-size --order --limit --no-favourites --no-proxy-suspects`.

**The unattended cycle used today:**

```bash
~/.iphone-image/cycle.sh
```

Started under `nohup caffeinate -dimsu`, so it survives closing the terminal
and keeps the Mac awake. Logs to `~/.iphone-image/cycle.log`. Loops
release -> check-nothing-to-fetch -> check-floor -> fetch -> upload, up to 12
times, and stops cleanly when there is nothing left to fetch. **Written for
today's photo job specifically** (hardcoded `--type photo`); would need
`--type video` and a size-aware batching decision to be reused for videos,
see section 6.

`run-chunk.sh` (older, `sync`-only) still exists and still works; `cycle.sh`
is the one that also uploads and releases.

**The live config now has:**

```yaml
cloud:
  video_destination: "Diskstation2/Family Videos"
remove_from_iphone:
  policy: cloud_verified          # ARMED -- see docs/SAFETY.md before touching
  batch_size: 10000               # PhotoKit confirmation batch, was 500
chunking:
  free_space_floor: 10GB          # was 20GB, per the user's explicit ask today
```

---

## 6. The video job -- measured, not started

```
camera video            1,823 files    181.1 GB
  excluded (correct)    WhatsApp video   3,102 / 22.5 GB
                        screen recordings 597 / 20.8 GB
```

This is **not the 83.5 GB the previous handover recorded** -- that figure was
wrong, possibly from before the camera bundle-id backfill was understood
correctly (see the old mistake list, item 6). 181.1 GB is measured against the
real ledger with the real selector.

**Why it hasn't started: place-name coverage, not disk or Drive space.**
Google Drive was upgraded to 5 TiB today specifically to make room for this
(3.25 TiB free, plenty). Disk is fine at 26 GB and the cycle script proved it
can maintain a floor unattended. The blocker is organisational: see section 7.

**The user's decision, given today:** wait for place-name coverage to recover
before running any video chunk, so trip folders come out named
(`2024/07 Plett/`) rather than bare months (`2024/07/`). A video chunk run
today would mostly produce bare-month folders and the `relocate` guard from
item 9 would then block ever renaming them into place, because renaming a
month into a name is fine but the archive-vs-cloud desync risk from section
3.9 applies just as much to a first filing as to a re-file.

**When ready:** `cycle.sh` as written will not do this -- it hardcodes
`--type photo`. Either write a twin script with `--type video`, or generalise
`cycle.sh` to take the type as an argument (recommended, five-minute change).
The 15-minute-chunk size in config (`chunk_bytes: 15GB`) will need
reconsidering too: at ~100 MB average per video, a 15 GB chunk is only ~150
files, so 1,823 files is roughly 12 chunks regardless -- same order of
magnitude as the photo job's, but each file is much larger so a single stalled
transfer costs more wall-clock before the stall detector (15 min silence)
fires.

---

## 7. Place-name coverage -- what it is and why it collapsed

Read live from Photos' own reverse geocoding at organise time (`places.py`),
never cached in the ledger. Two numbers, straight from the real Photos
database today:

```
assets not trashed             89,849
with a ZMOMENT title (place)   22,241   =  24.8%
```

The previous handover recorded **69%** when the archive was first filed. The
cause: the library grew from 78,806 to 94,678 assets between then and now, as
an iCloud backfill (already in progress, unrelated to this tool) caught up.
Photos analyses new arrivals lazily and had not yet run its reverse-geocoding
pass on ~15,000 of them, so overall coverage fell even though the *old*
assets' coverage did not change.

**It is recovering.** Spot-checked mid-cycle today: newly-uploaded 2022/2023
folders were correctly named (`2022/12 Babylonstoren Road`, `2023/09 Taunton`)
in the upload log, meaning that period's coverage has already come back.
2024-2026 is presumably still catching up. Speeds up with the Mac plugged in,
on wifi, screen locked -- normal Photos background-analysis conditions, no
tool involvement needed.

**No command in this tool measures coverage.** Today's number came from a
one-off query against a copy of `Photos.sqlite`. Worth adding to `doctor` if
this recurs -- it is exactly the kind of silent, self-correcting drift that
`doctor`'s trend-reporting pattern was built for.

---

## 8. A gap found while writing this handover, not yet fixed

**`release`'s selector unconditionally requires `present_on_phone = 1`.**
`Selector.where()` (selector.py:202) hardcodes that clause with no override,
because it is the right default for `sync` (don't fetch what isn't there) and
`remove-from-iphone` (can't remove what isn't there). It is the *wrong*
default for `release`, whose entire job is Mac disk space: whether an asset is
still on the phone has no bearing on whether its Mac copy can be freed once
Drive holds a verified copy. If anything, an asset **already removed from the
phone** is a *stronger* candidate for release, not a weaker one.

**Measured consequence:** the 5,024 photos deleted from the iPhone earlier
today are still sitting on the Mac, fully cloud-verified, because every
`release --apply` run since then silently excluded them.

```sql
SELECT COUNT(*) FROM assets
WHERE removed_from_phone_at IS NOT NULL
  AND local_status = 'LOCAL_VERIFIED'
  AND cloud_status = 'CLOUD_VERIFIED';
-- 5,024
```

That is **14.16 GB stranded on the Mac for no reason**, exactly matching the
count deleted from the phone -- confirmed by overlap query, not coincidence.

**The fix**, not yet made: `release`'s `plan()` should not depend on
`selector.where()`'s phone-presence clause at all, or should offer an explicit
override the way `for_removal()` does for the opposite direction. The cleanest
shape is probably a `for_release()` method on `Selector` parallel to
`for_removal()`, dropping `present_on_phone` entirely, since release never
touches the phone and has no reason to care about it.

No test caught this because no test exercises `release` against an asset that
has been removed from the phone -- every existing fixture has
`present_on_phone = 1`. Add that fixture alongside the fix.

---

## 9. What still has no plan

```
still on the phone, no upload/delete pipeline exists for any of these:
  screenshots          14,607 items    13.8 GB
  WhatsApp (photo+video) ~6,200 items  46.8 GB
  screen recordings      597 items    20.8 GB
```

None of this is touched by the camera-only pipeline. Under `cloud_verified`
removal policy, none of it can ever be deleted by this tool without a
decision to also upload it somewhere -- and unlike the photo archive, none of
these have an agreed destination folder on Drive. This was flagged to the
user today and deliberately left as an open decision, not started.

---

## 10. Files worth reading, in order

| File | Why |
|---|---|
| `docs/SAFETY.md` | Shortest and most important. The hazards, the four-step removal sequence, and what happens to the Mac copy. |
| `docs/PLAN.md` | Architecture, decisions, phases, risk register. |
| `src/iphone_image/remove.py` | P10. The eight-way refusal, and the Live Photo group-completeness check that caught 528 photos before this was ever run for real. |
| `src/iphone_image/release.py` | Why an asset is re-verified against the remote in the same invocation it is deleted from the Mac -- and see section 8 for the gap in its selector. |
| `src/iphone_image/cloud.py` | Per-folder batching, the stall-vs-slow distinction, `destination_for` / `split_cloud_path` for the two-archive split. |
| `src/iphone_image/relocate.py` | The un-naming guard (`unnaming()`), added today after nearly stripping 8,590 folder names. |
| `src/iphone_image/photos/places.py` | Where place names come from, and why their coverage is not stable. |
| `~/.iphone-image/cycle.sh` | The unattended fetch/upload/release loop used today. Gitignored, local-only, photo-only as written. |
| `spikes/iimphotos/Sources/iimphotos/Delete.swift` | The PhotoKit deletion primitive: confirms by re-fetching, never guesses at a missing identifier. |
| `docs/SPEC.md` | The original specification, kept verbatim; `PLAN.md` section 7 lists what is superseded. |
