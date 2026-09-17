# Handover

Written 2026-09-16, revised the same day once `release` landed. Everything below
is measured or recorded, not assumed.

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

**There is no external drive and there will not be one.** Working in chunks on a
Mac with ~20 GB free *is the product*, not a limitation to engineer around. A
previous session proposed buying a drive; that was solving the wrong problem.

**The Mac is a temporary working copy.** The archive is a staging buffer. Once
an asset is in Google Drive the Mac copy is released. Invariant: **two
independent copies at all times**, and the Mac is only ever the third.

```
cloud-verified   Drive verified + Recently Deleted 30d  ->  Mac copy released
never uploaded   Recently Deleted 30d only              ->  Mac keeps the bytes
```

### The cycle

```
fetch  ->  upload  ->  verify  ->  release the Mac copy  ->  next chunk
```

**Every step now exists.** `release` landed in `ff4c9bd`; the cycle can turn a
second time. It has not yet freed meaningful disk, because the first upload is
still in flight and only what the remote confirms is eligible -- 20 of 19,926 on
its verified trial run, the other 19,906 correctly refused as not yet in the
cloud. **The disk is still at 18 GB. Finishing the upload is what unlocks it**,
not more code.

---

## 3. Current state

| Phase | Status |
|---|---|
| P0 / P0b spikes | Complete. USB is dead; PhotoKit works and deletion is permitted. |
| P1 foundation | Complete. |
| P5 scan / list / sync | Complete. **Camera is fully archived.** |
| P6 dedupe | Not needed yet: zero exact duplicates among the 19,962 archived. |
| **P7 cloud** | **Built and running.** Upload in flight, see below. |
| P8 verify | Per-asset hash verification exists inside `cloud`; a full reconcile does not. |
| **The release step** | **Built** (`ff4c9bd`). Waiting on the upload, not on code. |
| P10 removal | Not started, gated on `tests/destructive/` existing first. |

292 tests and ruff clean. 40 commits, 1 unpushed. Nothing uncommitted.
(`mypy src` reports one pre-existing error: PyYAML stubs are not installed in
this venv. It is the environment, not the code -- `pip install types-PyYAML`.)

### Where the bytes are

```
archive   ~/Desktop/iphone           {source}/{year}/{event}
ledger    ~/Desktop/iphone/iphone-image.sqlite
config    ~/.iphone-image/config.yaml
logs      ~/.iphone-image/logs/, chunk.log, cloud.log

PHOTO  LOCAL_VERIFIED   19,926    59.4 GB   camera, complete
PHOTO  DISCOVERED       56,577    14.8 GB   whatsapp, screenshots, and 36 to re-fetch
VIDEO  DISCOVERED        2,303    83.5 GB   untouched, needs --type video

free disk on the Mac      18 GB   <- below the 20 GB floor; doctor says NOT READY
```

**The first upload failed and nothing has been re-run.** It started 2026-09-16
15:26, was killed by a hardcoded 6h timeout at 21:26, and is gone. What it left:

```
on Drive, measured      11,748 files   36.96 GB   62% of the 19,942 planned
in the ledger                  20 assets          the release trial, nothing else
disk freed                      none             still 17 GB
```

**Six hours of genuine uploading recorded as zero**, because verification was a
single step at the very end and the run never reached it. Both causes are fixed
(section 9 entry 13): work is now banked folder by folder, and a transfer is
killed for going silent rather than for taking too long. The bytes on Drive are
not wasted -- `--checksum` means a re-run skips them and the first pass banks
those 11,748 almost immediately.

---

## 4. Google Drive

Connected and working. `rclone` holds the token; the tool never does.

```
remote        gdrive              scope = drive (full)
destination   Diskstation2/Family Photos/Andrew iPhone Archive
quota         2 TiB total, 325 GiB free   (124 GB would cover all of camera)
```

**The scope is deliberately full and was originally not.** `drive.file` was
chosen first because it lets rclone see only files it created, but the target is
a folder created by hand in the web UI, which `drive.file` cannot reach at all.
The user chose full access knowingly. Consequence worth remembering: rclone can
now also modify everything else in that Drive, including `cloudscale-backups`.

**`Family Photos` is a hand-curated archive going back to 2003**, organised as
`2019/03 Plett/`, `2020/05 and 06 Lockdown Covid/`. Nothing the tool writes goes
into those folders. It writes to one new top-level folder, matching the existing
convention for device dumps (`Shannon iPhone 7 Plus 2019`).

---

## 5. The archive layout

`{source}/{year}/{event}`, where `{event}` is a place and the span of one visit:

```
camera/2019/11-12 Cape Town/       194
camera/2019/11 Constantia and Hout Bay/  42
camera/2019/12 Mossel Bay/         166
camera/2019/11/                     21   <- month bucket, not "Unsorted"
```

Place names come from **Photos' own on-device reverse geocoding**, read from
`Photos.sqlite` the same way the source bundle id is. No network, no dataset, no
dependency, and the names match what the user sees in Photos. 69% of archived
photos get one; the rest fall back to their month, which sorts beside the named
folders so the year stays chronological.

Three clustering rules, each from a real failure: a cluster never crosses a year,
a gap over 45 days starts a new visit, and a folder needs at least
`organization.event_min_photos` (10) files to earn a name. **That count is of
files the archive will hold**, not of assets that inspired them -- counting
library-wide let a place clear the minimum on WhatsApp images that are never
archived.

---

## 6. Measured numbers

Do not replace these with estimates; they were expensive to get.

| | |
|---|---|
| Library, on the Mac | 79,024 assets, 158 GB |
| Library, on the phone | **~94,180 assets. The Mac holds 84%.** See section 8. |
| iCloud download rate | **1.15 to 1.50 MB/s** sustained |
| Drive upload rate | **2.55 MB/s** at 16 transfers; 0.33 at rclone's default of 4 |
| Drive upload rate, sustained | **falls to 1.12 MB/s after roughly an hour.** 4.46 rising to 6.79 over the first 63 min, then 1.12 for the next 5 hours, and `rateLimitExceeded` on a listing the next morning. Short benchmarks do not see this. |
| Archive shape | 236 folders, median 25 files, largest 965 files / 2.70 GB |
| Local disk read rate | > 4,000 MB/s, which is why the two must never be averaged |
| Scan time | ~2 minutes for the whole library, zero bandwidth |
| Archive re-file | 13,172 files, 37 GB, 20 seconds -- renames, not copies |
| Exact duplicates among archived | **zero**, all 19,962 unique by SHA256 |
| GPS in the archived files | 99% in the ledger, 98% in the EXIF itself |

---

## 7. How to run it

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
relocate            re-file the archive after a layout change; --apply to run
device | status | journal | config show|validate|init
```

Selector: `--source --type --older-than --newer-than --year --min-size
--max-size --order --limit --no-favourites --no-proxy-suspects`.

Long runs go detached, survive the terminal, and keep the Mac awake:

```bash
./run-chunk.sh --source camera      # fetch
./run-chunk.sh --status | --stop
```

The upload was launched by hand with the same `nohup caffeinate -dimsu` pattern.
**A generalised runner does not exist** and `run-chunk.sh` only knows `sync`.

---

## 8. The thing that is actually wrong

**The Mac's Photos library is missing ~15,156 assets that are on the phone.**

```
screenshots     phone 14,605   Mac  7,684   missing  6,921   (53% synced)
whole library   phone 94,180   Mac 79,024   missing 15,156   (84% synced)
```

Characterised, not guessed: the Mac holds thousands of assets from 2019-2023 but
**essentially zero PNGs** from those years (2, 2, 1, 1, 2). Screenshots are PNG,
so they were never downloaded rather than misclassified. This fits the record
that the Mac library was a detached iCloud library that got reconnected: it
carried the old camera roll forward and has only backfilled 2024-onward.

**The tool can only ever act on what the Mac library holds.** Removal goes
through PhotoKit against that library, so "delete all screenshots" today reaches
53% of them and reports success. Completing the sync is a prerequisite for the
goal, not a tidiness matter.

**The cause is not established.** All four sync daemons run and sit at 0.0% CPU;
nothing new has arrived in two days; the disk is at 95%; there are 106,003 queued
background work items. Photos did a burst of work mid-investigation (resources
203,945 -> 210,019, ~5.6 GB) and stopped. Do not assert a cause from one number:
that mistake is already in the list below. The cheapest real test is the work
already planned -- free ~60 GB by releasing uploaded copies, then re-measure.

---

## 9. Mistakes made, and what they cost

Kept because the pattern matters more than the individual bugs.

1. **Trusting SDK headers over a device.** `originatingAssetID`, `fingerprint`,
   `gpsString`, `exifCreationDate`, `pairedRawImage` are all declared by
   ImageCaptureCore and populated **0.0%** of the time. A header is not evidence.
2. **Reporting an empty result as success.** A locked iPhone reports a complete
   but empty catalog with a stripped capability list. The first spike run called
   that a successful measurement.
3. **Trusting a delegate callback.** `deviceDidBecomeReadyWithCompleteContentCatalog`
   never fires on that device. The probe sat at 100% for 153 seconds. Progress
   is now polled, because a callback cannot report its own absence.
4. **A hardcoded rate constant.** 0.45 MB/s was measured while Apple's metadata
   sync competed for bandwidth. The real rate is 1.24 to 1.50. Estimates now
   come from this installation's own journal.
5. **Averaging disk reads into a network rate.** Produced 3.77 MB/s where the
   truth was 1.24, then later displayed "4696.44 MB/s" as a transfer rate. The
   same threshold is now defined once and used in both places. **It came back
   on 2026-09-15**, because that threshold was applied to the *run*: it catches
   a chunk that was entirely local and cannot see a chunk that was half of
   each. One was live at the time, reporting 10.12 MB/s while the instantaneous
   network rate was 2.6, and it would have estimated the next all-iCloud chunk
   at 0.4 hours instead of 1.7. Now each asset is classified as it lands and
   only downloads feed the estimate. Rows written before the split are ignored
   rather than approximated, because their bytes cover both kinds and the
   proportion cannot be recovered afterwards. **A threshold is only as good as
   the thing it is applied to.**
6. **A channel mapping invented rather than measured.** `camera` was mapped
   first to "no source app", then to `com.apple.camera` alone, which matched
   6,181 assets and 20.7 GB. It should have been 20,613 and 124 GB, because iOS
   only began recording that bundle id around 2024-08. A backup filter silently
   omitting 39 GB of the user's photographs is the worst way to be wrong.
7. **Asserting a cause from a single number.** Claimed the iCloud sync had
   "quietly stalled" on no evidence. Photos had zero upload jobs queued and
   every asset marked cloud-synced. `doctor` now reports the count's trend and
   names both possibilities rather than picking the pessimistic one.
8. **`&& echo pushed` after a commit that was rejected**, reporting success for
   a no-op push. Pushes now compare the SHA before and after.
10. **A completeness check that could not check completeness, reporting `[ok]`.**
   The phone holds **14,605 screenshots**; the Mac library holds **7,684**, and
   every missing one predates 2024. The whole library is 79,024 against the
   phone's ~94,180 -- **84%** -- and `doctor` called the sync healthy
   throughout, because the comparison it would have used is guarded by
   `if expected and ...` and `photos.expected_assets` was unset. Absence of a
   reference read as a pass. This is the same shape as every gate failure in
   this project and it mattered more than most: the user's first cleanup was
   "delete all screenshots", which would have reached 53% of them and looked
   finished. The fix does not depend on anyone typing a number -- the library's
   own composition gives it away, since a year holding 8,411 assets and no
   screenshots is not a change of habit. **The total looked plausible; only the
   composition showed it.**
9. **Treating an outage as N broken assets.** A chunk lost its network partway
   and marked **1,435 assets FAILED**, one per remaining asset in the plan,
   attempting every one of them after the cause was unmistakable. Nothing was
   lost, because a FAILED row is re-queued by the next run, but the state read
   as 1,435 individually broken photographs. `sync` now ends a chunk after
   `CONSECUTIVE_FAILURE_LIMIT` failures in a row and says it stopped early. The
   rule counts rather than diagnoses: matching Apple's error strings to spot
   "offline" would be reading a message where a signal already exists.
11. **Destroying 36 files while reporting zero failures.** `relocate` treated
   every asset holding a path as about to vacate it, including the ones already
   where they belonged. A mover was told a stayer's path was free, `os.rename`
   replaced the file, and because rename is atomic and succeeds, the run
   reported "13,172 moved, 0 failed". The ledger was left with two assets
   pointing at one file. Found by noticing 19,962 assets against 19,926 distinct
   paths -- **the count disagreed before anything else did**. Every duplicated
   path had exactly one claimant whose recorded hash matched the survivor, so
   the 36 losers were identifiable rather than guessed; they are reset to
   DISCOVERED and the next sync re-fetches 91 MB. Two things did work: the
   originals were all still in the Photos library, which is the entire reason
   the archive is a staging buffer, and the upload in flight would have caught
   them anyway as hash mismatches. **Who stays must be decided before where
   anyone goes.**

12. **A log that could not report progress, on the run that needed it most.**
   The first 59.5 GB upload wrote 228 bytes to `cloud.log` and then nothing for
   hours. Two independent causes, either of which was enough: rclone prints no
   progress unless asked, and `_run` used `capture_output`, which hands stderr
   over only once the process exits. So the log read exactly the same whether
   the transfer was moving or had died an hour before -- and the only way to
   tell was to ask Drive what it held. Fixed by streaming stderr as it arrives
   and passing `--stats 30s --stats-one-line --stats-log-level NOTICE`. **The
   third flag is the one to keep**: measured against rclone 1.74.1, the first
   two alone still print nothing, because stats log at INFO and the default
   level is NOTICE. A test asserts all three. The first cut of the fix then
   truncated the diagnostic from the front, so a failing run reported
   `NOTICE: stats 351 ... 375` and cut off the `ERROR:` that came last -- a
   message made entirely of noise with the signal trimmed off the end. Its own
   test caught that. **Absence of output still is not absence of trouble.**

13. **Six hours of uploading recorded as zero.** The first real upload put
   11,748 files and 36.96 GB on Drive, then hit a hardcoded 6h timeout at 62%
   and was killed. Not one of those files was marked verified, because
   verification was a single pass after every upload finished, so an
   interruption anywhere recorded nothing at all. Three faults, and the middle
   one is the one that mattered:
   - **the timeout asked the wrong question.** A wall clock cannot tell a slow
     transfer from a dead one, and it killed a working upload for the crime of
     being throttled. It is now a *stall* timeout -- no output at all for 15
     minutes -- which is only answerable because progress is streamed at all
     (entry 12). Being throttled still prints stats; being wedged does not.
   - **nothing was banked until everything was done.** Now each folder is
     uploaded, verified against a listing of *that folder*, and committed
     before the next begins. The connection is in autocommit, so an
     interruption costs at most the batch in flight. A folder was chosen as
     the unit because it is also the unit of checking: re-listing all 20,000
     files per batch is what made frequent verification too dear to consider.
   - **the rate was measured over minutes and assumed to hold for hours.** It
     did not: 6.79 MB/s at the one-hour mark, 1.12 MB/s over the five that
     followed. Every estimate I gave from the fast window was wrong, and the
     ETA I quoted was under half the real figure. **A rate measured over
     minutes is not a rate**, which is entry 4 again in a new costume.

   A fourth showed up within a minute of the re-run: `banked 189 verified at
   76.99 MB/s` for a folder where rclone sent **nothing**, the files being
   already on Drive. Planned size over elapsed time is not a transfer rate
   when the transfer did not happen -- entry 4 a third time, in a third
   costume. The rate now comes from rclone's own stats line, counts only
   batches that sent something, and reports "rate not reported" rather than a
   number when the line cannot be parsed. **Unknown is not zero.**

   `cloud_upload_workers` is still 16. Drive throttling is one observation and
   changing a measured constant on one observation is entry 4 as well; the
   `cloud.tps_limit` knob exists, defaults to off, and per-folder rates now go
   to the journal so the next session argues from data.

---

## 10. Open questions

- **Whether 16 transfers is what provokes Drive's rate limiter.** Unresolved
  and now instrumented rather than guessed: per-folder rates go to the journal,
  so a second long run answers it. `cloud.tps_limit` is the knob, default off.
- **36 assets are waiting to be re-fetched** and `sync` will refuse to start
  while free space is under the 20 GB floor. They come back as soon as the
  upload finishes and `release --apply` reclaims the disk.
- **6,259 archived photos have GPS but no place name**, because Photos never
  reverse-geocoded them. They fall back to month buckets. Fixing it means an
  offline dataset, which is `docs/PLAN.md` decision 15 and a real build.
- **Screenshot classification is unproven at scale.** On the current library
  `com.apple.springboard` and the subtype agree on 7,679 of 7,681.
- **Whether parallel fetches raise the 1.5 MB/s download rate.** Untested. The
  upload gained 8x from concurrency, so this is worth an hour.
- **Proxy threshold (0.12 bytes per pixel) misfires on screenshots**, which are
  flat colour and compress 7x better than photographs: 35% of them fall below it
  against 5% of camera photos. 595 screenshots are permanently unremovable for a
  reason that does not apply to them.
- **`photos.expected_assets` is still unset.** The phone's own count is ~94,180.
  Setting it turns `doctor`'s completeness check from a warning into a real
  comparison.
- **Junk retention may become the next pinch point.** ~21 GB of never-uploaded
  junk held for 90 days, against ~20 GB free.

---

## 11. What to do next

1. **Re-run `cloud --source camera --apply`, then `release --apply`.** The
   first pass banks the 11,748 files already on Drive almost immediately, since
   rclone skips them by checksum and verification is now per folder. Expect the
   remaining ~22 GB to be slow; being throttled is no longer fatal. Progress is
   visible in `cloud.log` this time, a line every 30s.
2. **Re-measure the sync gap** with ~80 GB free, which settles section 8.
3. **Re-fetch the 36** and finish the screenshots/WhatsApp chunks.
4. **P10 removal**, gated on `tests/destructive/` existing and passing first.

`docs/PLAN.md` is the plan of record and is current.

---

## 12. Files worth reading, in order

| File | Why |
|---|---|
| `docs/SAFETY.md` | Shortest and most important. The hazards, the four-step removal sequence, and what happens to the Mac copy. |
| `docs/PLAN.md` | Architecture, decisions, phases, risk register. |
| `src/iphone_image/cloud.py` | Why an upload is not believed until the remote is asked. |
| `src/iphone_image/release.py` | The only other code that destroys data. Four rules, and why RELEASED had to be terminal. |
| `src/iphone_image/relocate.py` | Two passes, and the 36 files that paid for the second one. |
| `src/iphone_image/organize/events.py` | The three clustering rules and the data that produced them. |
| `src/iphone_image/selector.py` | The selector, the channel classifier, and `for_removal`. |
| `src/iphone_image/sync.py` | The fetch engine, its verification rules, and the rate split. |
| `spikes/P0-transport.md` | Why USB failed. |
| `docs/SPEC.md` | The original specification, kept verbatim; `PLAN.md` section 7 lists what is superseded. |
