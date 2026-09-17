# Handover

Written 2026-09-17, at the end of the day the camera photo roll finished
uploading; **updated 2026-09-17 (late evening)** with two fixed defects and a full
remote-vs-ledger audit (section 8). Everything below is measured or recorded,
not assumed -- and section 8c is about the difference between those two words.

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
  verified in Drive                22,888   73.77 GB   100%   re-checked 2026-09-17 (late evening)
  still on the Mac (LOCAL_VERIFIED)  5,032   13.20 GiB  release is the next step
  released (Trash, then emptied)    17,856
deleted from the iPhone             5,024   13.19 GiB  Recently Deleted, 30-day window

camera video                        1,823  181.1 GB    NOT STARTED -- see section 6

Mac disk free                       42 GB
Google Drive                        5 TiB total, 3.25 TiB free
```

The 22,888 is now a measured count of files in Drive, not a count of ledger
rows claiming to be there -- **one recursive `rclone lsjson --hash` against the
archive, diffed against every CLOUD_VERIFIED row: 22,888 files, 22,888 rows,
every one matching on both size and SHA256, zero in any disagreement class.**
Section 8c is why that distinction is the whole point and why no command does
it yet.

The 5,032 is 5,024 + the 8 photographs section 8b had to re-fetch.

334 tests pass, ruff clean. 46 commits, 0 unpushed, nothing uncommitted.

**The figures above are corrected, and the correction is the point.** As
written on 09-17 this block said 22,888 verified in Drive. Drive held 22,880
files: eight pairs of distinct photographs shared one file each, and eight rows
read CLOUD_VERIFIED over a copy that did not exist. Nothing was lost -- all
sixteen were still on the phone -- but the next removal pass would have turned
it into loss. Section 8b has the mechanism and the repair. **The number was
wrong because it was a count of rows that claimed to be in Drive, and the
ledger cannot verify the ledger.**

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

Both section-8 defects are fixed and the ledger is repaired. What is left:

1. **Run `release --source camera --type photo --apply`** to free the 13.2 GiB
   that 8a was stranding. The plan is verified and reports 5,024 safe,
   0 blocked; the trashing step was deliberately left unrun for the user to
   authorise. Then **empty the Trash** from Finder (`~/.Trash` is not readable
   from a terminal -- macOS privacy -- so the terminal cannot confirm either
   the size or that it worked).
2. **Add the remote-vs-ledger audit to `doctor`**, section 8c. It is the only
   check that has ever found a false CLOUD_VERIFIED, and right now it exists
   only as something a person remembered to do by hand. It must report
   duplicate `cloud_path` values too: that was the signal that unpicked 8b.
3. **Decide the video job**, section 6. It cannot start today's way because
   Photos' place-name coverage is still recovering (section 7). Options are
   there. Note that 6 video filename collisions are already waiting in the
   un-uploaded set -- harmless now, and a demonstration that 8b was a class of
   defect and not an incident.
4. **Decide what to do about screenshots, WhatsApp, and screen recordings**,
   section 9 -- 74,127 items / 278 GB have no plan at all and are outside the
   12-month camera-only pipeline that exists today.
5. **A second, larger removal pass** is now realistic: with the whole camera
   roll in Drive, `remove-from-iphone --source camera --older-than 1y` would
   plan against roughly 15,000 eligible photos rather than 5,024. Not run --
   deliberately left for the user to authorise, per SAFETY section 3.
   **Do not run this before item 2.** 8b is exactly the defect this pass turns
   into permanent loss, and the audit is what proves there is not another one.

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

## 8. Two defects, both fixed on 2026-09-17 (late evening)

### 8a. `release` excluded the assets it exists to free — fixed, `1b7c5f4`

`Selector.where()` required `present_on_phone = 1` unconditionally. Right for
`sync` (cannot fetch what is not there) and `remove-from-iphone` (cannot delete
what is not there); wrong for `release`, whose only job is Mac disk space and
which never touches the phone. The set it excluded was the set *most* eligible:
an asset already off the phone has a Mac copy that is pure surplus.

Measured, not estimated: the 5,024 photos deleted from the phone on 09-17 were
all LOCAL_VERIFIED **and** CLOUD_VERIFIED, and every `release --apply` since had
silently skipped them — 13.2 GiB stranded on a Mac that had run out of disk
that same afternoon.

`Selector.for_release()` drops the clause, applied inside `release.plan()` the
way `for_removal()` is applied inside `remove.plan()`, so no route into the
verb can miss it. None of release's own guards changed: every candidate is
still re-verified against the remote in the same invocation. `where()` can now
compile to no clauses at all, which would have produced `WHERE  AND ...`; it
returns `1 = 1` instead.

No test had caught it because every release fixture hardcoded
`present_on_phone = 1`. `archived()` takes `on_phone` now.

**A plan run against the live ledger reports 5,024 examined, 5,024 safe,
0 blocked** — so all 5,024 were also hash-matched at the remote in that run,
not merely believed from the ledger. **Not yet released:** the trashing step
was left for the user to authorise. `release --source camera --type photo
--apply` frees 13.2 GiB.

### 8b. Two photographs, one Drive file — fixed, `64396f1`

Found by answering "have any photos been lost?" properly: listing all 22,888
files Drive actually holds and comparing them to the ledger, rather than
reading the ledger's account of itself.

22,880 matched on size and SHA256. The other 8 were **pairs** — two distinct
photographs, different capture dates, different hashes, sharing one Drive
file. Both rows of each pair read CLOUD_VERIFIED. Only one of each was there.

```
IMG_0083.HEIC   2020-01-18  2,733,615 B  in Drive    |  2020-08-25  1,384,918 B  NOT in Drive
IMG_0369.HEIC   2020-01-25  1,504,153 B  in Drive    |  2020-09-06  1,331,226 B  NOT in Drive
IMG_0624.HEIC   2020-02-02  1,457,662 B  in Drive    |  2020-09-08  1,354,602 B  NOT in Drive
IMG_1742.HEIC   2020-03-04  1,796,437 B  in Drive    |  2020-09-29  1,370,674 B  NOT in Drive
IMG_3349.HEIC   2020-03-23  5,300,900 B  in Drive    |  2020-11-14    849,998 B  NOT in Drive
IMG_3351.HEIC   2020-03-23  5,940,621 B  in Drive    |  2020-11-14    853,576 B  NOT in Drive
IMG_3683.HEIC   2020-03-25  4,428,749 B  in Drive    |  2020-11-28  1,327,121 B  NOT in Drive
IMG_0999.JPG    2023-10-20  2,682,399 B  in Drive    |  2023-12-28  4,668,663 B  NOT in Drive
```

**The mechanism.** `unique_filename` asks the *filesystem* whether a name is
free, and the filesystem forgets. Release trashes the Mac copy and clears
`local_path`, so the name looks free again, so the next asset with the same
camera filename — iPhone counters wrap, which is why all eight collisions are
same-name-same-year — is handed the same archive path, the same remote path,
and its upload replaces a file the first asset's row still points at.

Every one of the eight losers was verified between 07:27 and 10:19 on 09-17 and
overwritten by its twin between 16:27 and 17:48 the same day. **Verification
cannot catch this**: by the time the second upload runs, the first has long
since verified and is never re-checked. `release` *would* have caught it — it
re-asks the remote in the same invocation — but each loser had already been
released, in the morning, before its twin arrived.

**Nothing was lost.** All 16 were still on the phone; `present_on_phone = 1`,
`removed_from_phone_at` NULL for every one. But CLOUD_VERIFIED is exactly what
`remove_from_iphone.policy: cloud_verified` consults before deleting from the
phone, so **a second removal pass would have deleted eight photographs whose
only other copy did not exist.** That pass was item 5 on the old section 4 list.

**The repair, in order, all done:**

1. The eight rows corrected in the live ledger to `cloud_status = 'NONE'`, which
   closed the deletion hazard immediately. Ledger backed up first to
   `~/Desktop/iphone/iphone-image.sqlite.backup-20260917-215225`.
2. Migration **0004** adds `archive_claim`: the archive-relative name an asset
   has taken, written when the name is chosen and **never cleared**, because the
   whole point is to outlive the file. `sync.backfill_archive_claims` fills it
   for rows predating the column — from `local_path`, or for released rows from
   `cloud_path` plus the channel rule, which is Python and so cannot live in the
   migration. Applied live: **22,880 claims recorded, 0 names claimed twice.**
3. `sync.claimed_by_another` replaces `unique_filename`'s default `taken` test.
   A name is taken when the file is there *or* when another row claims it.
4. `relocate` will not move a file onto a claim held by a row it cannot see
   (released rows have no `local_path`, so they are neither movers nor stayers),
   and `cloud.plan` refuses outright to upload onto another asset's
   `cloud_path`. The last is redundant with 2 and 3 **deliberately**: it is the
   layer that still holds the day someone adds a second route to a filename.
5. The eight re-fetched and re-uploaded. Each was given a distinct
   hash-suffixed name, which is the fix working in production rather than in a
   test: `camera/2020/01-12 Home/IMG_0083__E021EDF7.HEIC`. All eight verified
   by hash. Drive now holds 22,888 distinct files.

**Two things worth keeping from this.**

The regression test was checked by disabling the fix and watching it fail with
both assets on `2021/07/IMG_0083.HEIC`. A test for a defect this quiet is worth
nothing until it has been seen to fail.

One further test exists only to catch a silent failure this design invites: a
fetched claim is derived from a real archive path, a backfilled one is
reconstructed from a cloud path, and **if those two ever disagree about the
channel level, every comparison between them is false and the whole check reads
as "no claim, name free" for precisely the rows it was built for.** Note that
the channel level comes from `organization.pattern` (live value
`{source}/{year}/{event}`) and is not structural — the `Config` default
`{year}/{month}` has no channel level at all, so a test written against the
default pattern would have proved nothing. That is the same shape as every
gate in the WordPress CLAUDE.md that reported OK while measuring the wrong
thing.

### 8c. What this says about the audit itself

The ledger cannot verify the ledger. Both defects were invisible to every
existing check and both were found the same way: **asking the remote what it
actually holds and diffing it against what we claim.** That is one recursive
`rclone lsjson --hash` and a dictionary comparison — a few minutes for the whole
22,888-file archive, and it is the only check that has ever found a
false CLOUD_VERIFIED.

Nothing in the tool does this. `doctor` is where it belongs, and the numbers to
report are: files in Drive, rows claiming CLOUD_VERIFIED, and the three
disagreement classes separately (missing, size mismatch, hash mismatch). It
must **also** report duplicate `cloud_path` values, because that was the signal
that unpicked this one — the listing had 8 fewer files than the ledger had rows
while reporting zero missing, which is only possible if two rows point at one
file.

`cloud_objects` is worth knowing about: the table exists, has a full schema,
and is **empty** — all 22,888 verified assets carry their state in
`assets.cloud_path` / `cloud_verified_at` instead. Not a data-loss bug, and the
per-asset fields are the ones every verb reads, but a reader who trusts the
schema will conclude there are no cloud copies at all. Either populate it or
drop it.

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
| `src/iphone_image/release.py` | Why an asset is re-verified against the remote in the same invocation it is deleted from the Mac. Its selector gap is fixed; see section 8a. |
| `src/iphone_image/db/migrations/0004_archive_claim.sql` | The whole of section 8b, written where the next person will hit it. Why the archive name an asset claims has to outlive the file. |
| `src/iphone_image/sync.py` | `claimed_by_another` and `backfill_archive_claims`: why `unique_filename` cannot ask the filesystem whether a name is free. |
| `src/iphone_image/cloud.py` | Per-folder batching, the stall-vs-slow distinction, `destination_for` / `split_cloud_path` for the two-archive split. |
| `src/iphone_image/relocate.py` | The un-naming guard (`unnaming()`), added today after nearly stripping 8,590 folder names. |
| `src/iphone_image/photos/places.py` | Where place names come from, and why their coverage is not stable. |
| `~/.iphone-image/cycle.sh` | The unattended fetch/upload/release loop used today. Gitignored, local-only, photo-only as written. |
| `spikes/iimphotos/Sources/iimphotos/Delete.swift` | The PhotoKit deletion primitive: confirms by re-fetching, never guesses at a missing identifier. |
| `docs/SPEC.md` | The original specification, kept verbatim; `PLAN.md` section 7 lists what is superseded. |
