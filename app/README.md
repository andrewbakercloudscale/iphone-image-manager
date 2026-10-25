# Photo Sentinel (Mac app)

SwiftUI front end for iphone-image. Design: [`../docs/MAC-APP-DESIGN.md`](../docs/MAC-APP-DESIGN.md).

**Every photo operation runs from the app** (owner, 2026-10-25). The app never writes the
ledger itself; it reads it (`~/Desktop/iphone/iphone-image.sqlite`, read-only, safe while a
job writes) and `~/.iphone-image/cycle.log`, and acts only through the engine:

| Action | What runs | Where it is |
|---|---|---|
| Back up / Resume | `cycle.sh <type> 40 <source> [age]` under `caffeinate`. One at a time; the rest queue | Library, Activity |
| Check Google Drive | `iphone-image --json audit`: every verified file's hash re-read from Drive | Backup, removal step 2 |
| Plan a removal | `remove-from-iphone <selector>`: fresh scan, then each item's Drive hash re-read | removal step 3 |
| Remove | the same with `--apply --confirm "<phrase>"`. Never resumes by itself | removal step 4 |
| Proof | `deletion_events.evidence.remote_hash_confirmed`, per removed item | removal step 5, Bin |
| Back up picked items | `sync --id … --budget --apply` → `cloud --apply` → `release --apply`, queued behind any job | Library |
| Places | `places`: country/city from Photos' reverse geocoding, by rounded GPS | Library (auto daily), filters |
| Finish a removal | `settle --apply`: a later scan records what Photos actually deleted | Activity, Checkup |
| Recover | `recover --id …`: Drive → new folder, each file hash-checked; "Add to Photos" only on click | Bin |

CLI actions run detached under `~/.iphone-image/ui/<id>/` (`task.json`, `out.json`,
`err.log`, `exit`), so they outlive the app and show in Activity when it reopens.
Removal stays locked until a Google Drive check from the last 6 hours is clean.
Clean-up numbers are estimates that mirror `remove.py`'s per-asset rules; the plan is the truth.

```bash
bash scripts/build-app.sh               # test, build, install to ~/Applications, launch
swift test                              # PhotoCore tests
.build/debug/PhotoSentinel --snapshot /tmp/snap   # render every tab to PNG and quit
```

| | |
|---|---|
| `Sources/PhotoCore` | Ledger queries, channel rule (mirrors `selector.py`), recipes, jobs from cycle.log, findings. No UI |
| `Sources/PhotoSentinel` | Window, menu bar, tabs. `Theme.swift` is copied from System Sentinel: change both |

Keep `Channel.sql` and `selector.py`'s `channel_of` in step; `LedgerTests` pins the cases.
The job states come from cycle.sh's log wording; `JobsTests` pins each message.
