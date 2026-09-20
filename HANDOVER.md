# Handover

Started 2026-09-17 (the day the camera photo roll finished uploading), updated
2026-09-17 late evening (section 8), 2026-09-19 (section 3b) and **2026-09-20
12:50, which is the section immediately below and the only one to trust for
current state.** Everything is measured or recorded, not assumed -- and section
8c is about the difference between those two words.

---

## START HERE -- state at 2026-09-20 12:50

**The video job is RUNNING** (restarted 12:48, `cycle.sh video 40`), with
`after-video.sh` waiting to start screenshots and `watch-video.sh` writing an ALIVE
line every 10 minutes to `~/.iphone-image/watch.log`. 47.7 GB free at restart, a
chunk needs 24.7 GB. Chunk 1 (131 videos) landed and verified this morning:
829 of 1,823 videos are now in Drive, 994 to go. **The Mac was on battery (84%)**;
plug it in.

**The disk was not full of Bin files. It was full of our own leak.** The 12:18 stop
("13 GB free, needs 24.7") happened with the Bin already empty, and the earlier
notes blaming "released bytes sit in the Bin" were wrong. `doctor` copies the Photos
database and its WAL to a temp dir on every call (the WAL alone is 7.7 GB) and
**never removed the copy**: 1,748 `iim-doctor-*` dirs, 23 GB, plus a stray 10 GB WAL
copy from 09-17. Deleted, and `doctor.py` now removes its copy at exit, on a failed
copy, and sweeps any over an hour old (tests in `tests/test_doctor_temp_copies.py`).
Disk went 14 -> 47.7 GB. **Unexplained:** who ran `doctor` ~580 times. Nothing in
`~/.iphone-image` or launchd does. Check `ls /private/var/folders/*/*/T | grep -c
iim-doctor` if free space falls again; the sweep now caps it, but a caller looping
on `doctor` would still cost ~10 GB transiently each time.

Also fixed this morning (see `scripts/`): `cycle.sh` measures the requirement in
bytes the way `sync` does and **empties the Bin itself** when every item is a
released asset (a foreign file blocks it, exit 2); `chunk_bytes` is 13GB.

**Still open:** the Photos library's `originals` grew 10/32/48/14 GB on 09-17..20 as
PhotoKit fetched. Optimize Mac Storage should evict them under pressure, but this
has not been observed. Watch free space across chunk 2: if it falls ~13 GB per
chunk with the Bin empty, that is the next leak.

### Numbers (ledger, 2026-09-20)

```
verified in Drive          23,586     22,888 photos 68.7 GiB   698 videos 73.8 GiB
removed from the phone     18,037     5,024 (09-17) + 13,013 (09-19)
                                      Recently Deleted expires ~2026-10-17 and ~2026-10-19
camera video               1,823      168.6 GiB
   in Drive                  829      ~87 GiB     45%   (698 old ones still on the phone: 693 removable)
   still to fetch            994      ~82 GB      about 7 chunks of 13 GiB
   FAILED                      0
screenshots, older than 1y 11,313     12.8 GiB    not started (2,899 are suspected proxies)
Mac disk free                47.7 GB  after removing the leaked doctor copies; a chunk needs 24.7 GB
```

### First actions, in order

1. **Plug the Mac in.** Then `tail -3 ~/.iphone-image/watch.log`: the last line
   must say `alive` with a recent timestamp. `JOB GONE` means read the last
   camera/video lines in `cycle.log` and re-run the restart command below.
2. **If the job is gone**, restart it (it resumes from the ledger):
   ```bash
   cd ~ && nohup caffeinate -dimsu ~/.iphone-image/cycle.sh video 40 \
     > ~/.iphone-image/cycle-video.out 2>&1 & disown
   nohup ~/.iphone-image/after-video.sh > ~/.iphone-image/after-video.out 2>&1 & disown
   nohup ~/.iphone-image/watch-video.sh > /dev/null 2>&1 & disown
   ```
   Exit 2 now means the Bin holds something that is not ours: look, empty it by
   hand, re-run. Exit 4 means two cycles without progress: read the sync errors.
3. **Remove the 693 old videos** (73.4 GiB, verified in Drive, still on the
   phone). Needs a human at the Mac: the macOS dialog is Apple's. Runs alongside
   the fetch (did on 09-19):
   ```bash
   cd ~/Desktop/github/iphone-image-manager && export PYTHONPATH=src
   ./.venv/bin/python -m iphone_image audit                     # ~5 min, must exit 0
   ./.venv/bin/python -m iphone_image remove-from-iphone --source camera --type video --older-than 1y
   # read the plan, then re-run it with the --apply --confirm "<phrase>" it prints
   ```
4. **Screenshots start themselves** this time: `after-video.sh` is waiting and
   runs `cycle.sh photo 4 screenshot 1y` when the video job ends with "nothing
   left to fetch". Then `audit`, then `remove-from-iphone --source screenshot
   --older-than 1y`; expect ~8,400 of the 11,313 to be removable.

### Do not repeat these

- **Watch for the job DYING, not only for events.** A watcher that greps the log for
  `WARNING:` is silent when the process is gone, which is how 12 hours were lost.
  Poll `pgrep -f "cycle.sh video"` in the same loop. And **never
  `tail -n +"$(wc -l < f)"`** -- macOS `wc -l` pads with spaces, `tail` rejects the
  offset (`illegal offset`), and the grep never runs. That silently disabled a
  monitor for 15 hours on 09-18.
- **Never edit `cycle.sh` in place while a job runs from it.** Bash reads scripts
  incrementally. Write `cycle.sh.new` and `mv` it over (the running process keeps
  the old inode). The operational scripts live in `~/.iphone-image/` and are **not
  in git**; consider copying them under `scripts/` in the repo.
- **On battery the Mac sleeps despite `caffeinate`** (`PreventSystemSleep 0`), and
  a sleeping laptop wedges rclone with a frozen byte count. It was on BATTERY at the 09:05 restart.
  `no_progress_timeout_seconds` (30 min) now kills such a transfer, but the job
  still loses the time.
- **One PhotoKit exporter at a time** for fetches. Two syncs on one library is a
  device-stability risk this project has always avoided. (A removal alongside a
  fetch has been fine.)
- **Do not trust the ledger to verify the ledger.** `audit` last ran clean on
  09-19 (23,301 rows, 0 missing, 0 mismatched). It has not run since 285 more
  videos were verified. Run it before every removal pass.

### Decisions the user has made (durable)

- **Deletion is gated on Google Drive, never on the Mac.** Verbatim: "the deletion
  should require gdrive verification, not mac verification." Section 3b and
  `docs/SAFETY.md` 1b. Under `cloud_verified` the Drive hash is re-read in the
  same run and must match; the Mac copy is not consulted.
- **Videos go straight into the existing `Diskstation2/Family Videos` year
  folders**, not a subfolder (verified collision-free against the 7,168 files
  already there).
- **Screenshots go to `.../Andrew iPhone Archive/screenshots`**, filed by
  year/month. Goal for everything: **12 months on the phone, all older uploaded
  and removed.**
- No external drive, ever. The Mac is a buffer; Drive is the archive.

### Open questions for the user

- **Google Photos for search.** Asked 2026-09-19, answered but not started. Facts
  checked against current docs: Drive does not sync to Photos; Photos' importer
  (Add -> Google Drive) is manual and its docs list photo types only (video
  unconfirmed); rclone's Google Photos backend can upload at original quality and
  create albums from paths, but since 2025 can only see what it uploaded itself.
  It is a *second copy* counting against the same 5 TiB. Needs the user to run
  `rclone config` (browser login). Suggested: trial one folder with the importer
  first. Nothing built.
- **Drive object IDs.** `assets.cloud_path` stores the full Drive path, verify time
  and hash for all 23,586 verified assets; `cloud_objects` (which has an
  `object_id` column) is empty and `rclone lsjson` already returns IDs. Offered,
  not done.
- **436 Live Photos + 14 bursts** are blocked from removal because their motion
  halves / frames were never archived. Archive them, or leave them on the phone.
- **WhatsApp and screen recordings** still have no pipeline (section 9).

### Where things live

| | |
|---|---|
| repo | `~/Desktop/github/iphone-image-manager` (public, MIT, `main`) |
| run it | `export PYTHONPATH=src; ./.venv/bin/python -m iphone_image <verb>` |
| ledger | `~/Desktop/iphone/iphone-image.sqlite` (backup `...backup-20260917-215225`, pre-repair) |
| config | `~/.iphone-image/config.yaml` (backups alongside) |
| jobs | `~/.iphone-image/cycle.sh [photo\|video] [cycles] [source] [older-than]`, `after-video.sh` |
| logs | `~/.iphone-image/cycle.log` (rolled 09-19: `cycle.log.through-2026-09-19-0800`) |
| verbs | `doctor scan list sync cloud release remove-from-iphone relocate audit status` |

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

## 3. Where things stood on 2026-09-17 (HISTORICAL -- superseded by START HERE and 3b)

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

348 tests pass, ruff clean. 48 commits, 0 unpushed, nothing uncommitted.

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

## 3b. 2026-09-19: what changed since the section above was written

Read this before section 4. Four things happened, and one of them reversed a
rule this document called absolute.

**1. Deletion is gated on Google Drive, not on the Mac.** `docs/SAFETY.md` 1b
said nothing leaves the phone that is not already on the Mac, "no exceptions".
That stopped being achievable by design: the cycle releases the Mac copy the
moment Drive verifies it, so all 14,729 camera photos older than a year (45.5
GiB) were on the phone, verified in Drive, and unremovable -- and the tool cannot
fetch a RELEASED asset again. The owner's rule, verbatim: **"the deletion should
require gdrive verification, not mac verification."** Under `cloud_verified` the
Drive hash is re-read in the same invocation and must match the ledger; the Mac
copy is not consulted at all, and a perfect Mac copy cannot vouch for a wrong
Drive one (both directions are tests, mutation-checked). `local_verified` keeps
its meaning. Every deletion's journal evidence says `"basis": "remote_hash_only"`.
Commits `5a4366c`, `34e18a7`. SAFETY.md 1b is rewritten and quotes the owner.

**2. The first real removal of the older photos.** After a clean `audit`
(23,301 verified rows, 0 missing, 0 mismatched) the plan was:

```
scan #9                        assets examined   14,729
                               safe to remove    13,013   42.7 GB
                               blocked            1,716
                                  1,266  flagged as a suspected iCloud proxy
                                    436  Live Photo whose motion half is not archived
                                     14  burst with unarchived frames
```

Applied with `--confirm "remove 13013 photos from my iphone"`; the outcome is in
the run's own output, not repeated here. **The 1,716 blocked are a to-do, not a
verdict**: the 436 Live Photos need their `.MOV` halves archived (the same 528-
photo problem section 3 recorded), and the 1,266 proxies can never be removed and
are backed up like anything else.

**3. Screenshots have a destination.** `cloud.screenshot_destination` sends the
screenshot channel to `.../Andrew iPhone Archive/screenshots`, nested inside the
photo archive as the owner asked, and files them `<year>/<month>/`. 11,312
screenshots are older than a year (12.8 GiB, one chunk, ~1.3 h to fetch); 2,899
of them are suspected proxies. Routing is by *channel*, not media type, because a
screenshot is a PHOTO by type. Nesting is where this can fail silently, so three
places are pinned by tests: `split_cloud_path` matches longest-first;
`backfill_archive_claims` used to iterate a *set* of destinations, whose order is
arbitrary, and would strip the parent prefix first; and `audit.remote` excludes a
parent's nested files so each screenshot is not counted twice. Commit `8b76cd2`.
It runs automatically after the video job (`~/.iphone-image/after-video.sh`).

**4. The video job was killed by a sleeping laptop, and the tool could not tell.**
On battery `caffeinate` does not prevent system sleep (`PreventSystemSleep 0`,
19 "Maintenance Sleep" events overnight). rclone stayed alive printing stats every
30 s with its byte count frozen at 1.732 GiB for **14h45m**. `stall_timeout`
watches for silence and it was never silent; `batch_timeout` (2 h) did not fire
because `time.monotonic()` does not advance while macOS sleeps. **`no_progress_
timeout_seconds` (default 1800) now kills a process that is talking but has moved
no bytes** -- zero progress only, never slow progress, armed only once a byte
count has parsed, compared against a high-water mark so rclone's dipping
numerator cannot re-arm it. Commit `0f7789c`. **Plug the Mac in for any long job.**

Also fixed on the way: the cloud summary printed the *photo* destination for
every video upload (`6f0d1aa`, display only, ledger was right); and `cycle.sh`'s
`to_fetch` counted only `DISCOVERED`, so a run whose last stragglers were FAILED
would report "nothing left to fetch" and never retry them (fixed in the new
`cycle.sh`, which is what a restarted job uses).

### The video job, as it stands

**See START HERE.** History: started 2026-09-18 10:56; chunk 1 took 2h09m; killed
overnight by a sleeping laptop (fixed: `no_progress_timeout`); restarted
2026-09-19 08:44; stopped 2026-09-19 20:46 by a disk-floor bug (fixed: the script
now stops at the real 26 GB requirement and detects no-progress). Logs:
`cycle.log`, previous `cycle.log.through-2026-09-19-0800`.

---

## 4. Next steps as of 2026-09-19 (SUPERSEDED by START HERE; kept for the reasoning)

Both section-8 defects are fixed and the ledger is repaired. What is left:

**Done since this list was written:** 8a's release ran (5,032 files, 13.2 GB
freed, 0 blocked, 0 failed) and the Trash has since been emptied. The audit is
built -- see section 8d. Both were items 1 and 2.

**The consequence of those two together, stated plainly: the 5,024 photos
deleted from the phone now have exactly one copy, in Google Drive.** The
iPhone's Recently Deleted holds them until roughly 2026-10-17 and then that is
it. This is the design -- no external drive, the Mac is a buffer -- but it is
the first time it has actually been true of 13 GB of photographs, and it is why
`audit` existing matters more today than it did yesterday.

1. **Run `iphone-image audit` before any removal pass.** It is the check that
   proves Drive still holds what the ledger claims. Not a one-off: the point of
   8b is that a false CLOUD_VERIFIED can appear at any time and nothing else
   notices.
2. **Decide the video job**, section 6 -- and read section 7 first, because
   the reason it was on hold does not survive measurement. Note that 6 video
   filename collisions are already waiting in the un-uploaded set: harmless now
   that the fix is in, and a demonstration that 8b was a class of defect rather
   than an incident.
3. **Decide what to do about screenshots, WhatsApp, and screen recordings**,
   section 9 -- 74,127 items / 278 GB have no plan at all and are outside the
   12-month camera-only pipeline that exists today.
4. **The second removal pass ran** (section 3b): 13,013 old camera photos, on
   Drive evidence alone. What remains from it: **436 Live Photos** whose motion
   half was never archived (a photo archived as the still only is not
   removable, and this is the 528-photo problem from 09-17 again), and **14
   bursts**. Neither is a verdict -- archive the missing halves and they become
   eligible. The 1,266 suspected proxies can never be removed.
5. **When the video job finishes:** run `sync --source camera --type video
   --apply` once (its old script never retries FAILED stragglers), then
   `iphone-image audit`, then `remove-from-iphone --source camera --type video
   --older-than 1y` for the ~1,525 old videos (146.6 GiB). Same rule, same typed
   phrase, same macOS dialog.
6. **When the screenshot job finishes** (it starts itself behind the video job):
   `audit`, then `remove-from-iphone --source screenshot --older-than 1y`. Expect
   ~8,400 of the 11,312 to be removable; 2,899 are suspected proxies.
7. **Drive object IDs are not stored.** `assets.cloud_path` records the full
   Drive path, verification time and hash for all 23,301 verified assets, but
   `cloud_objects` -- which has an `object_id` column -- is empty, and `rclone
   lsjson` already returns each file's ID. Populating it would make a record
   survive a rename or move in Drive and let a row link straight to its file.
   Not done; the path is what every verb reads today.

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
                    also refuses to move anything already in the cloud -- 8d
audit               the ledger against what the remote actually holds.
                    --offline for the instant, no-network half. Exits non-zero
                    on any disagreement. Run before every removal pass -- 8d
device | status | journal | config show|validate|init
```

Selector: `--source --type --older-than --newer-than --year --min-size
--max-size --order --limit --no-favourites --no-proxy-suspects`.

**The unattended cycle:**

```bash
~/.iphone-image/cycle.sh [photo|video] [cycles] [source] [older-than]   # defaults: photo 12 camera
RELEASE=0 ~/.iphone-image/cycle.sh ...              # hold the Mac copy; nothing needs it now (see 3b)
~/.iphone-image/cycle.sh video 25                   # the video job, started 2026-09-18
```

Started under `nohup caffeinate -dimsu`, so it survives closing the terminal
and keeps the Mac awake. Logs to `~/.iphone-image/cycle.log`, every line tagged
with the type. Loops release -> check-nothing-to-fetch -> check-floor -> fetch
-> upload and stops cleanly when there is nothing left to fetch.

The type is an argument now and is passed to **every** verb, not only `sync`:
an unscoped `cloud` run during a video job uploads any stray photo sitting
LOCAL_VERIFIED, which is not wrong but makes the log unreadable against the
plan. `release` stays deliberately unscoped -- a leftover verified photo is
disk the video job needs.

**The disk warning is the part that matters on a long job.** Released bytes sit
in the Bin until it is emptied, so free space falls across the run even though
every cycle releases. The script now warns at 30 GB as well as refusing at the
15 GB floor, because emptying the Bin is a manual step and the job stalls until
someone does it. At ~15 GB per chunk, expect to empty it roughly every three
cycles.

The previous photo-only version is kept at `cycle.sh.photo-only-backup`.

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

## 6. The video job -- RUNNING since 2026-09-18 (see 3b); measurements below are the pre-flight

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

**The decision on 09-17 was to wait** for place-name coverage to recover, so
trip folders came out named (`2024/07 Plett/`) rather than bare months
(`2024/07/`). **Section 7 no longer supports that**: coverage fell from 24.8% to
17.2% within a day and 2025-2026 are at zero. There is nothing to wait for.

The instinct behind it was right, though, and section 8d turned it into a real
guard: **relocate now refuses to move any file that already has a cloud copy**,
adding a name or removing one. So whatever folder a video lands in on upload is
the folder it keeps in Drive, permanently. That is the actual one-way door --
not the coverage percentage.

### The pre-flight, measured rather than assumed

The video destination is `Diskstation2/Family Videos`, and unlike photos it has
**no `Andrew iPhone Archive` subfolder**: videos upload straight into a
pre-existing family video archive of **7,168 files**, 6,188 of them named
`IMG_*.MOV` in year folders covering 2019-2021 -- the same years and the same
naming as the iPhone's. That asymmetry was found by `audit`'s
`at the remote, unclaimed: 7,167` line on its first run.

It is safe, and this was checked properly rather than reasoned about. Every one
of the 1,823 target paths was computed with the real `archive_path_for` and
intersected with a recursive listing of what is actually there:

```
existing family video files          7,168   (5,649 at year/file, 1,519 at year/sub/file)
camera videos to upload              1,823
collisions with existing files           0
collisions among the videos            2   both 2025/01-12 Cape Town
```

Zero. The existing archive files as `<year>/<file>`, the tool writes
`<year>/<event>/<file>`, and even the 1,519 existing files at the same depth
share no path.

**The 2 self-collisions are the point.** `2025/01-12 Cape Town/IMG_3308.MOV` and
`IMG_4876.MOV` each want one name for two different videos -- section 8b
arriving again in a new job. The 8b fix handles them: the second gets a
hash-suffixed name. Before that fix they would have been two more silently
overwritten files.

**Still worth deciding before the run:** whether videos should go to
`Family Videos/Andrew iPhone Archive` for the same isolation photos have. It
costs nothing now and cannot be changed afterwards without the desync the
relocate guard exists to refuse.

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

**It is not recovering. Measured again 24 hours later, it had fallen further:**

```
                 2026-09-17 (first)   2026-09-17 (late evening)
assets not trashed        89,849                89,863
with a place name         22,241                15,442
coverage                   24.8%                 17.2%
```

Roughly 6,800 assets *lost* a named moment overnight while the library size
barely moved. Photos re-clustered its moments and the new ones are untitled, so
this number is not a monotonic recovery curve -- it goes both ways, and
"presumably still catching up" was an assumption, not an observation.

By year it is clearly not a backlog:

```
2019  19.0%     2023  36.7%
2020  38.8%     2024  10.5%
2021  21.7%     2025   0.0%   <- 17,003 assets, zero named
2022  32.0%     2026   0.0%   <- 11,113 assets, zero named
```

**Zero across 28,116 assets for two whole years is not lazy analysis in
progress.** Something is not running for recent assets. Not diagnosed --
candidates are Photos' own analysis being blocked, or those years arriving via
the iCloud backfill and never being moment-clustered. Whatever it is, **waiting
for it is not a plan**, and no command measures it, so nobody would have
noticed either way.

Query used, against a copy of `Photos.sqlite` (never the live file):

```sql
SELECT strftime('%Y', a.ZDATECREATED + 978307200, 'unixepoch') yr,
       COUNT(*), SUM(m.ZTITLE IS NOT NULL AND m.ZTITLE != '')
FROM ZASSET a LEFT JOIN ZMOMENT m ON a.ZMOMENT = m.Z_PK
WHERE a.ZTRASHEDSTATE = 0 GROUP BY yr;
```

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

**This is now built — see 8d.** What follows is the reasoning that shaped it,
kept because the reasoning is the transferable part. `doctor` is where it
belongs, and the numbers to
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

### 8d. The guards, `4c244f6`

Both defects were invisible to every check that existed. These are the two that
would have caught them, built after the fact rather than instead of it.

**`iphone-image audit`** — a new verb. Split by cost, because the expensive half
cannot run on every invocation and the cheap half must:

- **offline, instant** (`audit.conflicts`, also wired into `doctor` as
  `check_ledger_conflicts`): two assets at one remote path, two claiming one
  archive name, a row verified with no hash or no path to verify against. Run
  against the pre-repair backup it finds **exactly the eight real collisions and
  names the asset ids**; against the repaired ledger, nothing. That is what
  would have caught 8b on the day it happened, for free.
- **full** (`audit.remote`): one recursive listing per destination, diffed
  against every CLOUD_VERIFIED row by hash. The only thing that can find a file
  that has gone missing at the remote or whose bytes have changed.

Its first real run, live:

```
ledger rows examined        94,681
consistency checks run      4
ledger contradictions       none
destinations listed         Family Photos/Andrew iPhone Archive, Family Videos
files at the remote         30,055
rows claiming verified      22,888
matched by hash             22,888
missing / hash mismatch     0 / 0
no hash reported            0
at the remote, unclaimed     7,167
```

Three design points, each one a bug that was nearly written:

- **A check that cannot run reports skipped, with the reason.** A ledger older
  than migration 0004 has no `archive_claim`; that check says so and `doctor`
  returns WARN. It does not quietly run three checks and call it a pass. This
  was found the honest way — by running it against the backup and watching it
  crash.
- **`audit` exits non-zero when the remote cannot be listed.** An unavailable
  checker fails; it never waves the ledger through.
- **A remote entry listed without a hash is counted as `unhashed`, never as a
  match** — an unanswered question is not a yes. And there is deliberately *no*
  `size_mismatch` field: the provider reports SHA256 and a size difference
  always implies a hash difference, so that field would read 0 forever and look
  like evidence.

Both halves state their coverage (`4 check(s) over 94,681 row(s)`) so a gate
that has stopped covering anything cannot be mistaken for one that found
nothing wrong.

**`relocate` now refuses to desync the cloud.** It moves local files; nothing
moves the remote. Re-filing an uploaded asset leaves its `cloud_path` pointing
at the old layout, and the archive and Drive stop describing the same thing.
Nothing reports an error — `release` re-checks the recorded path, which is
still correct — which is precisely why it is worth refusing rather than warning
about.

The `unnaming` guard already listed cloud desync among its reasons, but it fires
only on plans that *remove* a name and explicitly always allows adding one.
**Adding a name desyncs just as thoroughly**, and 2026-09-17's plan was blocked
for the other reason entirely, so the gap was never visible. There is a test for
exactly that. `allow_cloud_desync` is the override and says what it costs; both
refusals now appear in the plan preview rather than as a surprise at `--apply`.

348 tests pass, ruff clean.


---

## 9. What still has no plan (screenshots now do -- see 3b; WhatsApp and screen recordings do not)

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
