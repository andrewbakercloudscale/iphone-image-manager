# Photo Sentinel (Mac app)

SwiftUI front end for iphone-image. Design: [`../docs/MAC-APP-DESIGN.md`](../docs/MAC-APP-DESIGN.md).

**Phase A (now): read-only over the Python engine.** It reads the ledger
(`~/Desktop/iphone/iphone-image.sqlite`, read-only, safe while a job writes) and
`~/.iphone-image/cycle.log`, and runs `iphone-image --json doctor`. The one thing it
does is **Resume** a stopped backup job, which runs `cycle.sh` under `caffeinate`
exactly as HANDOVER.md does. Backup only: nothing is removed from the phone.

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
