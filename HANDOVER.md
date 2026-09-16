# Handover

Written 2026-09-16. Everything below is measured or recorded, not assumed.

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

Two things follow from that, and both were got wrong once already.

**There is no external drive and there will not be one.** Working in chunks on a
Mac with ~30 GB free *is the product*, not a limitation to engineer around. A
previous session proposed buying a drive; that was solving the wrong problem.

**The Mac is a temporary working copy.** The archive is a staging buffer, not a
destination. Once an asset is in Google Drive the Mac copy is released. The plan
had never said what frees the disk, and `docs/SAFETY.md` said the opposite --
that the recycle bin hard links the archive file and holds it 90 days -- which
would have stalled the second cycle. Fixed 2026-09-16, decision 14b.

The invariant that makes it safe: **two independent copies at all times**, and
the Mac is only ever the third.

```
cloud-verified   Drive verified + Recently Deleted 30d  ->  Mac copy released
never uploaded   Recently Deleted 30d only              ->  Mac keeps the bytes
```

### What that means numerically

One year as the cutoff, measured against the real library:

```
STAYS on the phone     15,208 assets    44 GB
LEAVES the phone       63,598 assets   114 GB
  camera               17,443 assets    92 GB   -> Google Drive
  whatsapp             35,712 assets    10 GB   -> discard
  screenrecording         223 assets     7 GB   -> discard
  screenshot            4,377 assets     4 GB   -> discard
  unattributed          5,115 assets     1 GB   -> look before deciding
```

Phone goes from **158 GB to 44 GB**, in roughly 9 cycles of: fetch a chunk,
upload what is worth keeping, remove it from the phone, hand the disk back.

Note the shape: **92 of the 114 GB leaving is camera**, and 35,712 WhatsApp
assets are only 10 GB. Cleaning WhatsApp is high-count and low-value for space.

---

## 3. The one piece of architecture to understand

**The original USB architecture is dead.** The P0 spike ran against a real
iPhone 15 Pro Max on iOS 26.6.2 and killed it on two independent counts:

| | |
|---|---|
| Photos library | 94,180 items |
| ImageCaptureCore over USB could see | 1,796 assets, **1.9%** |
| `canDeleteOneFile` | **false** |

Everything else lives in iCloud and a cable cannot reach it, and the device
refuses deletion outright. Full evidence: `spikes/P0-transport.md`.

**The replacement is PhotoKit against the Mac's Photos library**, plus a
read-only query of `Photos.sqlite` for the source application, which PhotoKit
does not expose. Evidence: `spikes/P0b-photokit.md`.

The Swift helper is `spikes/iimphotos`, speaking JSON Lines on stdout. Build it
with `cd spikes/iimphotos && swift build -c release`.

---

## 4. Current state

| Phase | Status |
|---|---|
| P0 USB spike | Complete. Killed the architecture. |
| P0b PhotoKit spike | Complete. Deletion **works** (`PHAssetChangeRequest.deleteAssets`). |
| P1 foundation | Complete. Config, ledger, migrations, journal, CLI, paths, retention, logging, doctor. |
| P5 scan / list / sync | Complete, run against the real library. **Camera is fully archived.** |
| **P7 cloud** | **Next, and it blocks everything else.** |
| P8 verify, the release step, P10 removal | Not started. |
| P6 dedupe, P9 campaigns | Not started, and not blocking. |

237 tests, ruff and mypy clean, CI green on macOS across Python 3.12 to 3.14.
30 commits. Nothing uncommitted.

### Where the bytes are

```
archive   ~/Desktop/iphone            {source}/{year}/{month}
ledger    ~/Desktop/iphone/iphone-image.sqlite
config    ~/.iphone-image/config.yaml
logs      ~/.iphone-image/logs/, chunk.log, chunk.pid
```

```
PHOTO  LOCAL_VERIFIED   19,962    59.5 GB   camera, complete
PHOTO  DISCOVERED       56,541    14.7 GB   whatsapp, screenshots, the small stuff
VIDEO  DISCOVERED        2,303    83.5 GB   untouched, needs --type video
TOTAL                   78,806   157.8 GB

cloud verified                0             nothing has been uploaded yet
free disk on the Mac       31 GB
```

**This is the pinch point.** 59.5 GB is staged on a Mac with 31 GB free and
nowhere to go. Nothing can be released until Drive has it, which is why P7 is
next and not P6.

---

## 5. Measured numbers

Do not replace these with estimates; they were expensive to get.

| | |
|---|---|
| Library | 78,806 assets, **158 GB** (not the 424 GB first estimated from the device cache) |
| Videos | 2,303 assets, **83.5 GB -- 53% of bytes from 3% of items** |
| iCloud download rate | **1.15 to 1.50 MB/s** sustained, measured repeatedly |
| Local disk read rate | > 4,000 MB/s, which is why the two must never be averaged |
| Scan time | ~2 minutes for the whole library, zero bandwidth |
| Archive move | 11,395 files, 30.9 GB, **4.5 seconds** -- renames, not copies |

Channels, every asset filed in exactly one:

```
whatsapp        43,967      camera          20,713      screenshot   7,681
unattributed     5,115      chrome             535      screenrecording 309
chatgpt            217      snapchat            91      + 24 more
```

`camera` is 20,713 for backup and 20,613 with suspected proxies excluded, which
is where an older figure in this document came from.

---

## 6. How to run it

```bash
cd ~/Desktop/github/iphone-image-manager
export PYTHONPATH=src
./.venv/bin/python -m iphone_image <command>       # or: pip install -e .
```

```bash
iphone-image doctor            # 11 checks on the machine, says what to change
iphone-image scan              # inventory the library, ~2 min, no bandwidth
iphone-image list <selector>   # preview, harmless
iphone-image sync <selector>   # plan only
iphone-image sync <selector> --apply
iphone-image relocate          # re-file the archive after a config change
iphone-image device | status | journal | config show|validate|init
```

The selector is one vocabulary shared by `list`, `sync` and later `remove`:

```
--source   camera whatsapp screenshot screenrecording snapchat safari chrome
           messages twitter chatgpt grok unattributed, or a raw bundle id
--type     photo video live screenshot raw burst favourite
--older-than 1y   --newer-than 30d   --year 2019
--min-size 10MB   --max-size 500KB
--order    oldest|newest|largest|smallest
--budget   15GB   --limit N   --no-favourites   --no-proxy-suspects
```

### Running a chunk

A chunk takes hours and dies with SIGHUP if the terminal closes. Use the
detached runner, which runs under `nohup caffeinate -dimsu` and reparents to
launchd (verified: PPID 1):

```bash
./run-chunk.sh --source camera      # detached, survives closing the terminal
./run-chunk.sh --status             # this run's progress, errors, started time
./run-chunk.sh --stop               # SIGTERM; resume with another run
tail -f ~/.iphone-image/chunk.log
```

Interrupt freely. Every asset is journalled before it is fetched, no `.partial`
is ever mistaken for a verified file, and the next run resumes from the ledger.
A `FAILED` row is not terminal -- it is simply not `LOCAL_VERIFIED`, so the next
run re-queues it. That path is tested and has been exercised for real: an outage
left 1,435 failures and the retry recovered all of them.

---

## 7. Decisions already taken

| Decision | Choice |
|---|---|
| Transport | PhotoKit + read-only `Photos.sqlite`, not USB |
| Library residency | Photos stays in **Optimise Mac Storage**; originals fetched per asset on demand |
| **The Mac is a temporary working copy** | Archive is a staging buffer. Released once the asset is in Drive; the recycle bin then keeps only its manifest row. Junk never uploaded keeps its bytes. Two independent copies at all times. |
| Archive layout | `{source}/{year}/{month}` under `~/Desktop/iphone`. `{source}` is the channel as `--source` names it. |
| Chunk size | **15 GB**, roughly one overnight run. Sized by wall clock, not disk. |
| Default types | **`[photo]`**. Video must be asked for by name. |
| Removal sequence | fetch to Mac, review, delete from device, move to recycle bin. **No exceptions.** A `--discard` flag was proposed and rejected -- so even junk is fetched before it can be removed. |
| Proxies | backed up like anything else, **permanently blocked from removal**, no override. The block is `Selector.for_removal()`, never a selector default: as a default it silently removed them from backup too. |
| Channels partition | Every asset belongs to exactly one channel. `unattributed` is the exact complement of `camera`, not "no source app". |
| iCloud sync propagation | detected; removal will need a typed confirmation |
| Mac-side deletion | never `unlink` user media; macOS Trash via `NSFileManager.trashItem` |
| Cloud | rclone; **the tool never holds a token** |
| Licence / distribution | MIT; helper built from source, no Apple Developer account |

---

## 8. Mistakes made, and what they cost

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
9. **Treating an outage as N broken assets.** A chunk lost its network partway
   and marked **1,435 assets FAILED**, one per remaining asset in the plan,
   attempting every one of them after the cause was unmistakable. Nothing was
   lost, because a FAILED row is re-queued by the next run, but the state read
   as 1,435 individually broken photographs. `sync` now ends a chunk after
   `CONSECUTIVE_FAILURE_LIMIT` failures in a row and says it stopped early. The
   rule counts rather than diagnoses: matching Apple's error strings to spot
   "offline" would be reading a message where a signal already exists.

---

## 9. Open questions

- **Screenshot classification is unproven at scale.** On the current library
  `com.apple.springboard` and the `photoScreenshot` subtype agree on 7,679 of
  7,681, which is strong, but it has not been checked against a library with
  many screenshots from mixed sources.
- **Whether parallel fetches raise the 1.5 MB/s rate.** Untested. At 1.5 MB/s
  the remaining 98 GB is ~18 hours of transfer, so this is worth an hour to find
  out before committing to 9 sequential cycles.
- **The 5,115 unattributed assets.** uuid-named, no source app, not matched by
  the `IMG_*` camera heuristic. Only 0.6 GB, so they cost nothing to keep -- but
  nobody has looked at what they actually are.
- **Proxy threshold (0.12 bytes per pixel) is calibrated from a sample**, not
  proven. 4,224 assets are flagged. They are backed up; they can never be
  removed. Worth eyeballing some before that block matters.
- **`photos.expected_assets` is unset** and should stay unset unless a real
  number is known. The 94,180 figure came from a phone screenshot and caused a
  false stall report.
- **Junk retention is unsettled in practice.** Decision 14b says junk never
  uploaded keeps its bytes for the retention window (90 days by default). At
  ~21 GB of junk against 31 GB free, that window may itself become the pinch
  point. Shortening it, or dropping junk immediately, is a live option the user
  has not been asked about directly.

---

## 10. What to do next

**Blocked on the user, and nothing proceeds without it:** an rclone remote for
Google Drive. rclone 1.74.1 is installed and has **no remotes configured**. The
tool never holds a token, so this is configured by hand. A ready script was put
on the clipboard; it creates the remote with `scope=drive.file` (least
privilege -- rclone can only see files it created), makes the `iPhone Archive`
folder, and round-trips a test file verified by hash before trusting it.

Then, in order:

1. **P7 cloud.** `CloudProvider` protocol, `RcloneProvider`, upload + verify by
   hash. Archive only; the recycle bin is never mirrored. Build it against a
   fake provider the way `FakeHelper` works, so the logic is testable without a
   live remote.
2. **P8 verify.** Reconcile library, archive, cloud and ledger.
3. **The release step**, decision 14b. Does not exist in any form yet. This is
   what makes cycle 2 possible.
4. **P10 removal.** Gated: `tests/destructive/` must exist and pass first. The
   four-step sequence, fresh reconciliation, typed confirmation while iCloud
   sync is on, per-asset journal writes, resumable, group-aware.
5. **P6 exact dedupe** whenever convenient. It saves upload bandwidth rather
   than unblocking anything, and every archived file already carries its SHA256.

`docs/PLAN.md` is the plan of record and is current.

---

## 11. Files worth reading, in order

| File | Why |
|---|---|
| `docs/SAFETY.md` | Shortest and most important. The hazards, the four-step removal sequence, and what happens to the Mac copy. |
| `docs/PLAN.md` | Architecture, decisions, phases, risk register. |
| `src/iphone_image/selector.py` | The selector, the channel classifier, and `for_removal`. |
| `src/iphone_image/sync.py` | The fetch engine, its verification rules, and the rate split. |
| `src/iphone_image/relocate.py` | How the archive is re-filed when the layout changes. |
| `spikes/P0-transport.md` | Why USB failed. |
| `spikes/P0b-photokit.md` | What replaced it, and the measured fetch rate. |
| `docs/SPEC.md` | The original specification, kept verbatim. Some of it is superseded; `PLAN.md` section 7 lists which. |
