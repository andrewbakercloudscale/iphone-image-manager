# Handover

Written 2026-09-14. Everything below is measured or recorded, not assumed.

---

## 1. What this is

`iphone-image` is a macOS CLI that inventories, backs up, verifies and
eventually offloads iPhone media. Repo: **https://github.com/andrewbakercloudscale/iphone-image-manager**
(public, MIT). Working copy: `~/Desktop/github/iphone-image-manager`.

The core promise, unchanged: **nothing is ever removed from the phone that is
not already on the Mac, reviewed, and verified.** See `docs/SAFETY.md`.

---

## 2. The one thing to understand first

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

---

## 3. Current state

| Phase | Status |
|---|---|
| P0 USB spike | Complete. Killed the architecture. |
| P0b PhotoKit spike | Complete. Deletion **works** (`PHAssetChangeRequest.deleteAssets`). |
| P1 foundation | Complete. Config, ledger, migrations, journal, CLI, paths, retention, logging, doctor. |
| P5 scan / list / sync | **Complete and run against the real library.** |
| P6 dedupe, P7 cloud, P8 verify, P9 campaigns, P10 removal | Not started. |

237 tests, ruff and mypy clean, CI green on macOS across Python 3.12 to 3.14.
27 commits. Nothing uncommitted.

### What has actually been fetched

```
archive   ~/Desktop/iphone           31 GB, 11,395 files, {source}/{year}/{month}
ledger    ~/Desktop/iphone/iphone-image.sqlite
config    ~/.iphone-image/config.yaml
logs      ~/.iphone-image/logs/iphone-image.log

PHOTO  LOCAL_VERIFIED   11,395    30.9 GB     done, all camera
PHOTO  DISCOVERED       65,108    43.3 GB     to do
VIDEO  DISCOVERED        2,303    83.5 GB     untouched, needs --type video
```

All 11,395 archived files were re-hashed from disk after the move and matched
their recorded SHA256 exactly. Zero partials, zero failures.

The archive moved out of `~/Pictures/iPhoneArchive` and the ledger out of
`~/.iphone-image/` on 2026-09-14, and the layout gained a channel level, so
each source lands in its own folder: `camera/2019/03/`, `whatsapp/2024/11/`.
`iphone-image relocate` is what performs that move; it renames rather than
copies, so it is instant on one volume and refused across two. Logs and the
run-chunk state file stayed in `~/.iphone-image/`.

---

## 4. Measured numbers

Do not replace these with estimates; they were expensive to get.

| | |
|---|---|
| Library | 78,806 assets, **158 GB** (not the 424 GB first estimated from the device cache) |
| Photos | 76,503 assets, 74 GB |
| Videos | 2,303 assets, **83.5 GB — 53% of bytes from 3% of items** |
| iCloud download rate | **1.24 to 1.50 MB/s** sustained |
| Local disk read rate | > 4,000 MB/s, which is why the two must never be averaged |
| Scan time | ~2 minutes for the whole library, zero bandwidth |
| WhatsApp | 43,967 assets but only **15.4 GB** |
| Camera | 20,713 assets, **124 GB** (20,613 excluding suspected proxies, which is what the pre-2026-09-14 selector counted) |
| Screenshots | 7,681 assets, 6.7 GB |
| Suspected iCloud proxies | 4,224, excluded from selection by default |

**Storage insight worth keeping:** cleaning WhatsApp is high-count and
low-value for space. 32,405 WhatsApp images over a year old recover only 5 GB.
The space is in camera video.

---

## 5. How to run it

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
iphone-image status | journal | config show|validate|init
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

### The next chunk

A chunk takes hours, and a process started from a terminal dies with SIGHUP when
the window closes. Use the detached runner:

```bash
./run-chunk.sh --source camera      # detached, survives closing the terminal
./run-chunk.sh --status             # is it running, how far along, any errors
./run-chunk.sh --stop               # clean stop; resume with another run
tail -f ~/.iphone-image/chunk.log
```

It runs under `nohup caffeinate -dimsu`, so the Mac will not idle, sleep its
disk or dim out mid-transfer, and the process reparents to launchd (verified:
PPID 1). Or in the foreground if you prefer to watch:

```bash
iphone-image sync --source camera --apply     # 15 GB
```

Chunk 1 ran in minutes because 2019-2021 were already resident on disk from the
old local originals. **Chunk 2 will be genuinely slow**, because it reaches
years that exist only in iCloud. Interrupt freely: resume is tested.

---

## 6. Decisions already taken

| Decision | Choice |
|---|---|
| Transport | PhotoKit + read-only `Photos.sqlite`, not USB |
| Library residency | Photos stays in **Optimise Mac Storage**; originals fetched per asset on demand |
| Chunk size | **15 GB**, roughly one overnight run. Sized by wall clock, not disk. |
| Default types | **`[photo]`**. Video must be asked for by name. |
| Ordering | photos before video; oldest first within a type |
| Removal sequence | fetch to Mac, review, delete from device, move to recycle bin. **No exceptions.** A `--discard` flag was proposed and rejected. |
| Destinations | `sync` writes the archive and will mirror to cloud; `remove` writes the recycle bin and never mirrors |
| Proxies | backed up like anything else, **permanently blocked from removal**, no override. The block is applied by `Selector.for_removal()`, never by a selector default: as a default it silently removed them from backup too. |
| iCloud sync propagation | detected; removal will need a typed confirmation |
| Mac-side deletion | never `unlink` user media; macOS Trash via `NSFileManager.trashItem` |
| Cloud | rclone; the tool never holds a token |
| Licence / distribution | MIT; helper built from source, no Apple Developer account |

---

## 7. Mistakes made, and what they cost

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

## 8. Open questions

- **Screenshot classification is unproven at scale.** `photoScreenshot` flagged
  1 of 44,939 on the old library, but that library had 12 PNGs total. On the
  current one `com.apple.springboard` and the subtype agree on 7,679 of 7,681,
  which is strong, but it has not been checked against a library with many
  screenshots from mixed sources.
- **Whether parallel fetches raise the 1.5 MB/s rate.** Untested. Would change
  the schedule materially.
- **`photos.expected_assets` is unset** and should stay unset unless a real
  number is known. The 94,180 figure came from a phone screenshot and caused a
  false stall report.
- **Proxy threshold (0.12 bytes per pixel) is calibrated from a sample**, not
  proven. 4,224 assets are currently excluded by it. Worth eyeballing some.
- ~~Proxy suspects are not being backed up at all.~~ **Settled 2026-09-14: the
  protection belongs on `remove`, where it is absolute.** It had been a selector
  default, and because `sync` uses the default selector, all 4,224 were left out
  of every backup while `docs/SAFETY.md` section 2 promised they were archived
  normally. Backup now reaches all 78,806 assets; `Selector.for_removal()`
  blocks the 4,224 and no flag can waive it. Found while verifying the channel
  classifier against the real library, not by reading the code.
- **The 18,932 unattributed assets** are assumed to be mostly camera. The
  `IMG_*` heuristic covers 13,787 of them; the other 5,111 are uuid-named and
  currently unattributed to any channel.

---

## 9. What to do next

In order:

1. **Chunk 2**: `iphone-image sync --source camera --apply`. Expect hours.
2. **P6 exact dedupe.** No device-side fingerprint exists, so hashing happens
   after download. One archive file per unique SHA256, N asset rows.
3. **P7 cloud** via rclone to Google Drive. Archive only; the recycle bin is
   never mirrored.
4. **P8 verify** reconciling library, archive, cloud and ledger.
5. **P10 removal**, gated on `tests/destructive/` existing and passing first.

`docs/PLAN.md` is the plan of record and is current.

---

## 10. Files worth reading, in order

| File | Why |
|---|---|
| `docs/SAFETY.md` | Shortest and most important. The hazards and the four-step removal sequence. |
| `docs/PLAN.md` | Architecture, decisions, phases, risk register. |
| `spikes/P0-transport.md` | Why USB failed. |
| `spikes/P0b-photokit.md` | What replaced it, and the measured fetch rate. |
| `docs/SPEC.md` | The original specification, kept verbatim. |
| `src/iphone_image/selector.py` | The selector and chunk planner. |
| `src/iphone_image/sync.py` | The fetch engine and its verification rules. |
