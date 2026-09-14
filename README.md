# iPhone Image Manager

**by CloudScale**

> Open source iPhone image backup, cleanup, organization and safe offload for macOS.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
![Status: pre-alpha](https://img.shields.io/badge/status-pre--alpha-orange.svg)
![Platform: macOS](https://img.shields.io/badge/platform-macOS-lightgrey.svg)

iPhone Image Manager is an open source macOS utility that inventories, backs up,
organizes, deduplicates and verifies images and videos from an iPhone. It supports
resumable USB sync, date and location based organization, Google Drive backup,
screenshot and WhatsApp cleanup policies, exact duplicate detection, and explicit
safe removal from the iPhone.

**The default behavior is always to keep media on the iPhone.**

---

## Status

**Pre-alpha. Not usable yet. There is no working code in this repository.**

What exists today is the specification and the plan:

- [`docs/SPEC.md`](docs/SPEC.md) is the full product and technical specification.
- [`docs/PLAN.md`](docs/PLAN.md) is the implementation plan, milestones, and the
  list of places where the specification meets an awkward platform reality.
- [`docs/SAFETY.md`](docs/SAFETY.md) is the safety model. Read this one first if you
  care about not losing photographs.

The next step is a transport spike against a real device, described as P0 in the
plan. Nothing past it is committed until it is done.

---

## The idea

```
iPhone
   │ scan          build a persistent inventory, touch nothing
   ▼
SQLite Inventory
   │ sync          copy to your Mac, resumably, verified
   ▼
Local Archive
   │ cloud-sync    push to Google Drive
   ▼
Cloud Archive
   │ verify        reconcile all four views of reality
   ▼
                   NOTHING HAS BEEN REMOVED

   │ remove-from-iphone --apply    only when you explicitly say so
   ▼
Space back on your phone
```

Inventory first. Sync second. Verify third. Remove only when explicitly requested.

---

## Planned commands

```bash
iphone-image device            # what is connected
iphone-image scan              # inventory only, never writes to the device
iphone-image sync              # incremental, resumable local backup
iphone-image cloud-sync        # upload verified assets to Google Drive
iphone-image verify            # reconcile device, archive, cloud and database
iphone-image status            # where everything stands
iphone-image list --type screenshot --older-than 60d --min-size 2MB

iphone-image campaign start    # freeze a cleanup set
iphone-image clean screenshots --older-than 60d
iphone-image clean duplicates

iphone-image remove-from-iphone           # shows a plan, removes nothing
iphone-image remove-from-iphone --type screenshot --older-than 60d --limit 25
iphone-image remove-from-iphone --apply   # the only command that deletes

iphone-image recycle-bin list             # what came off the phone, and where it is
iphone-image recycle-bin restore
```

---

## Safety promise

> iPhone Image Manager will never remove an image or video merely because it has
> been seen, copied, or uploaded. Removal is a separate, explicit operation and is
> only offered when the configured verification requirements have been satisfied.

Two specific hazards it is built around, both documented in
[`docs/SAFETY.md`](docs/SAFETY.md):

1. **iCloud "Optimize iPhone Storage".** When it is on, the file a Mac can copy over
   USB may be a downscaled proxy rather than your real photo. Assets that look like
   proxies are backed up but permanently blocked from removal.
2. **iCloud Photos sync.** Deleting on the phone deletes from iCloud and every other
   device. Removal requires a typed confirmation while sync is active.

And two undo paths, because deletion should never be a one way door:

- Anything removed from the phone leaves a **recycle bin entry on your Mac**, with
  the file and a record of why it was eligible. `iphone-image recycle-bin restore`
  brings it back.
- The tool never unlinks archive files. They go to the macOS **Trash**, restorable
  from Finder.

---

## Design goals

- Resumable everywhere. A 40,000 asset library takes days, and the phone will be
  unplugged during it.
- Idempotent. Running `sync` three times converges, it does not duplicate work.
- The SQLite database is the ledger, not a cache. Every removal is backed by
  recorded evidence.
- Privacy first. Media stays local unless you configure a cloud target. No
  analytics. Reverse geocoding is optional.

---

## Requirements

- macOS
- Python 3.12 or newer
- `libimobiledevice`, `exiftool`, `ffmpeg`, `rclone` (all via Homebrew)

---

## Contributing

The project is at the specification stage. The most useful contribution right now is
a reality check on [`docs/PLAN.md`](docs/PLAN.md), particularly section 5, where the
specification runs into what iOS actually exposes over USB. Issues welcome.

---

## License

MIT. See [LICENSE](LICENSE).
