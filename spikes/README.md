# P0 transport spike

Answers the nine questions in [`../docs/PLAN.md`](../docs/PLAN.md) section 2 against
a real iPhone, so the architecture is decided by evidence rather than assumption.

**This spike is read-only.** It enumerates and measures. It downloads nothing,
modifies nothing, and deletes nothing. The Swift binary contains no call to
`requestDeleteFiles` or `requestUploadFile`. The deletion question is answered by
reading the device's capability list, not by deleting one of your photographs.

---

## Run it

```bash
cd /path/to/iphone-image-manager
python3 spikes/run_p0.py
```

That is the whole thing. It builds the helper if needed, runs preflight checks, and
tells you exactly what is wrong if it cannot proceed.

### Before you plug in

1. **Quit Photos and Image Capture.** Both take an exclusive session on the device
   and the spike will not see it. Preflight checks this and stops if either is running.
2. Connect the iPhone by **cable**, not Wi-Fi.
3. **Unlock it** and tap **Trust** if prompted.
4. Keep it unlocked for the whole run. A large library takes many minutes to build
   its content catalog, and a lock part way through aborts it.

### Options

```bash
python3 spikes/run_p0.py --compare-presentations   # also enumerate transcodes, slower
python3 spikes/run_p0.py --no-ifuse                # skip the AFC cross-check
python3 spikes/run_p0.py --timeout 3600            # very large library
```

`--compare-presentations` runs the enumeration twice and measures the size
difference between original and transcoded assets. It is the one that puts a number
on the hazard in `docs/SAFETY.md` section 2b. Worth doing once.

### Two follow-up runs for question 7

Interruption behaviour cannot be observed in a clean run. Afterwards:

```bash
python3 spikes/run_p0.py          # and lock the iPhone while the catalog builds
python3 spikes/run_p0.py          # and unplug the cable part way through
```

Both error paths land in the JSONL and can be compared.

---

## What you get

Written to `spikes/output/`, which is gitignored because it describes your library:

| File | Contents |
|---|---|
| `findings-<stamp>.md` | The readable report, same as the terminal output |
| `findings-<stamp>.json` | The same findings, machine readable |
| `probe-original-<stamp>.jsonl` | Every asset record the device exposed |
| `probe-converted-<stamp>.jsonl` | Only with `--compare-presentations` |

The most important sections of the report:

- **Q4 / Q6**: whether deletion is possible at all, and whether iCloud Photos sync
  is on. If `canDeleteOneFile` is false, the removal feature cannot be built the
  planned way and the milestones get re-cut.
- **Q2 field availability**: the percentage of your real library carrying each
  metadata field. This is the empirical answer to what the scanner can rely on, and
  it replaces guesswork in `docs/PLAN.md` section 5 conflict 4.
- **Fingerprint duplicates**: whether exact deduplication can be done without
  downloading anything.
- **Q5 proxy signal**: the low bytes-per-pixel assets, so the iCloud proxy threshold
  can be calibrated against your library rather than invented.

---

## Building the helper by hand

```bash
cd spikes/iimhelper
swift build -c release
.build/release/iimhelper --help
.build/release/iimhelper probe --timeout 30
```

Exit codes: `0` complete, `2` no device or timeout, `3` disconnected mid-run,
`4` session could not be opened, `64` bad arguments.

No code signing is involved. You compile it locally, so Gatekeeper quarantine never
applies and no Apple Developer account is needed.
