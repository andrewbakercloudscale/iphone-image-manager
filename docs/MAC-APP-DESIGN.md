# Photo Sentinel: Mac app design (SwiftUI)

Status: **design, not built.** Written 2026-10-25. Visual mockups: the "iPhone Image
Manager — Mac app" design canvas, https://claude.ai/artifact/2X4jLE78DSoMF8QJ52hThE (private).

Styling comes from System Sentinel (`../system-sentinel/Sources/SystemSentinel/Theme.swift`):
coloured icon-over-label tabs, a gradient hero band, tinted-title `Card`s, `Pill`s,
`SymbolTile`s, "Ignore / Fix it / More" finding rows, and a footer button row. Copy that
file, do not re-derive it.

---

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| 1 | **Native Swift engine** (`ImageCore`), not a shell over the Python CLI, for anything shipped to other people | The CLI needs Python, rclone, exiftool and ffprobe, plus an rclone remote set up by hand. Every one of those is an onboarding step most people fail. Native: PhotoKit, ImageIO, AVFoundation and Google's REST API need nothing installed |
| 2 | `Engine` protocol with two implementations: `CLIEngine` (wraps `iphone-image --json`) and `NativeEngine` | The UI can be built and dogfooded on the owner's real library against today's engine, while the port happens behind it. Same screens, swap the engine |
| 3 | The Python code is the **reference implementation** | 388 tests encode hard-won rules (empty-hash guard, proxy blocking, Bin rename matching, release ordering). Port them as Swift tests before porting the code they test |
| 4 | Google Drive via **Sign in with Google** (`ASWebAuthenticationSession`), scope `drive.file` | One button instead of `rclone config`. `drive.file` sees only files the app made: non-restricted scope, so no CASA security audit, and it cannot touch the rest of anyone's Drive |
| 5 | Verification uses Drive's `sha256Checksum` | Same hash the ledger already stores, read fresh from the remote in the same run as removal (SAFETY.md, "Drive is the evidence") |
| 6 | Jobs run in a **login-item agent** (`SMAppService`), the app is a window over it, plus a menu-bar extra | Libraries take days. The window must be closable without stopping the job. `cycle.sh`, `caffeinate` and the `watch-*.sh` scripts become this agent |
| 7 | Distribution: Developer ID + notarised DMG, Sparkle updates. Mac App Store later | Background agent and long jobs are simpler outside the sandbox; nothing here needs the store |
| 8 | Same safety model, no new overrides | Everything in SAFETY.md applies unchanged. The UI only makes it visible |

### Owner decisions, 2026-10-25

| Question | Decision | Consequence for the design |
|---|---|---|
| Engine | Swift engine, phased (A–D in section 10) | As above |
| Screenshots | **Always backed up before removal**, for everyone | No "delete without backup" option. SAFETY.md 1b stands |
| Live Photos, bursts | **Back up every part** (paired video, every burst frame) so they become removable | `PHAssetResource` export of all resources per asset; removal requires every resource verified, not just the still |
| Storage | **Google Drive + S3-compatible** in v1 | `StorageProvider` protocol. Onboarding step 3 offers Google Drive (default) or "S3-compatible (advanced)": endpoint, bucket, keys in Keychain. S3 verification: upload with SHA-256 checksums and store the whole-file SHA-256 as object metadata; remove only when a fresh `HeadObject` matches the ledger. Multipart uploads give a composite checksum, so compare per-part, never the bare ETag |
| Name | A friendlier public name, not "iPhone …" (Apple trademark) | **Photo Sentinel**, a sibling to System Sentinel (confirmed 2026-10-25) |
| Distribution | Free and open source, notarised DMG, Sparkle | No licensing code; donation link in About |
| Presence | Window + menu-bar icon | Menu bar shows job progress and the pause reason |
| Battery | Pause on battery by default; setting "allow on battery above N%" (default off, N = 50) | Agent reads `IOPSCopyPowerSourcesInfo` |

## 2. Who it is for, and the one promise

People whose iPhone and iCloud are full of photos they want to keep, but somewhere cheaper.
They do not know what a proxy is and should not need to.

The promise, shown on the first screen and on every removal sheet:

> **Nothing leaves your iPhone until a copy is safely in your Google Drive, checked, and
> you say so.**

## 3. Onboarding (5 steps, about 3 minutes)

Each step runs the matching `doctor` check live and only asks for something when it fails.
A step that passes shows a green tick and the Continue button is already focused.

| Step | Shows | Auto-checks (`doctor`) | If it fails |
|---|---|---|---|
| 1 Welcome | The promise, three bullets: back up, clear out, undo for 30 days | macOS 14+ | Explain and stop |
| 2 Photos | "Let it see your library" → PhotoKit full-access prompt | Photos authorisation; iCloud Photos on; **Optimise Mac Storage** selected; disk headroom | "Download Originals is on: your Mac's disk will fill. [Open Photos Settings]". This is the expensive mistake; block until fixed |
| 3 Google Drive | [Sign in with Google] → choose folder (default `iPhone Archive`) | Drive free space vs library size | "Drive has 15 GB free, your videos alone are 181 GB. [Get more storage] or start with screenshots" |
| 4 First look | Scan runs (metadata only, nothing copied). Counters and the space bar fill in live | Scan completes; expected count vs found | Resumes; partial results are still useful |
| 5 Your goals | Recipe checklist with recommended ages pre-set (screenshots 60 days, WhatsApp 18 months, videos 1 year, back up everything) and the estimated space each frees | Power: on mains? Notifications allowed? | "Keep the Mac plugged in, lid open" as a tip, not a blocker |

Finish lands on **Checkup** with the chosen tasks already queued as findings.

## 4. Window layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ [icon] iPhone Image Manager      (✓ 94% backed up) (iCloud 91%) (⏸ Paused) │
│ v1.0 · 94,180 items                                                  │
│ [Checkup][Library][Clean up][Backup][Activity][Bin][Settings]        │
│ ┌ hero: "4 ways to free 238 GB" ............ [Check again] ┐         │
│ cards...                                                             │
│                      [About][Help][Share][Open Photos][Quit]         │
└──────────────────────────────────────────────────────────────────────┘
```

Header pills (always visible): backup coverage, iCloud storage used, current job state
(Running · Paused on hotspot · Paused on battery · Waiting for disk · Waiting for you).

| Tab | Colour (`Tab.color`) | Job |
|---|---|---|
| **Checkup** | pink `#db2777` | "What should I do?" Findings with one primary action each |
| **Library** | indigo `#4f46e5` | Analytics: where the space goes, what is backed up, what could go |
| **Clean up** | orange `#ea580c` | Task recipes and the task flow |
| **Backup** | blue `#0284c7` | Google Drive: folders, coverage per category, audit |
| **Activity** | green `#16a34a` | Running job, queue, pause reasons, history (`journal`) |
| **Bin** | amber `#b45309` | Recently removed, 30-day countdown, restore |
| **Settings** | slate `#64748b` | Ages, folders, safety, schedule, power and network rules |

## 5. Checkup findings (the `Detector`)

Same row shape as Sentinel: tile, title, one-line reason, `Ignore` / `Snooze` / primary
action / `More`. Ordered by space freed, safety problems first.

| Finding | Trigger | Primary action |
|---|---|---|
| Download Originals is on | `doctor` iCloud storage mode | Fix it (opens Photos settings) |
| N videos are only on your phone | camera videos with no verified Drive copy | Back up |
| N screenshots older than X | `retention.screenshots` | Clear them |
| WhatsApp saved N items older than X | source = WhatsApp bundle id | Clean up |
| iCloud full in about N days | growth over last 30 days vs quota | What grew |
| N exact duplicates | sha256 groups | Review |
| N videos over 500 MB | size | Move to Drive |
| N items removable now | verified in Drive, eligible, not yet removed | Remove |
| Drive check overdue | last `audit` > 7 days, or removal pending | Check now |
| Worth knowing: proxies, Live Photo halves, favourites kept | blocked categories | Explain |

## 6. Tasks (Clean up tab)

Every task is a **recipe**: selector (source × type × age × size) + destination + removal
rule. Recipes map 1:1 onto existing CLI selectors, so `CLIEngine` can run all of them today.

| Recipe | Selector | Default | Removes from phone? |
|---|---|---|---|
| Back up everything | all | — | No |
| Move old videos to Drive | `--type video --older-than` | 1 year | Yes, after verify |
| Clear old screenshots | `--source screenshot --older-than` | 60 days | Yes, after verify |
| WhatsApp clean-up | `--source whatsapp --older-than` | 18 months; keeps favourites and your own albums | Yes |
| Screen recordings | `--source screen_recording` | 30 days | Yes |
| Large videos | `--type video --min-size` | 500 MB | Yes |
| Exact duplicates | dedupe groups | keep oldest | Yes, extras only |
| Organise my archive | organisation mode | Year/Month; or Events, Places | No (Drive layout only) |
| Custom rule | any selector | — | Optional |

### The task flow (one sheet, five steps, a stepper across the top)

1. **Choose** — age slider (30 d … 5 y), size filter, live count and GB.
2. **Preview** — thumbnail grid sample, totals, and **blocked** with reasons
   ("626 low-res stand-ins: kept", "6 favourites: kept", "214 in your albums: kept").
3. **Back up & check** — becomes a job on Activity. Sheet can close.
4. **Remove** — appears only when every selected item is verified in Drive. Typed phrase when
   iCloud sync is on. Then: *"Click Delete when the dialog appears."* Apple's dialog times
   out after ~25 min with nothing removed, so the agent posts a notification with sound.
5. **Done** — space freed, "Recently Deleted keeps them for 30 days", [Open Bin].

Removal is never scheduled or automatic. Schedules only back up.

## 7. Library analytics (what is actually useful)

Ranked by how often it changes what someone does:

1. **Could free** — GB per recipe, as a stacked bar. The number that drives action.
2. **Where the space goes** — by source (Camera, WhatsApp, Screenshots, Screen recordings,
   other apps by bundle id) × photo/video.
3. **Backed up** — % verified in Drive per category, with "only on your phone" called out.
4. **By year** — items and GB per capture year; old years are the cheapest to clear.
5. **Growth** — GB added per month, and the iCloud-full projection.
6. **Biggest items** — top 20 videos, with Move to Drive inline.
7. **Apps that fill your library** — WhatsApp, Telegram, Instagram saves, by count and GB.
8. **Kept for safety** — proxies, favourites, album members, Live Photo halves, with Explain.

Charts follow Sentinel's `Theme.series` order and its `ColorBook` so a source keeps its
colour across refreshes.

## 8. Background agent: the ops lessons as product

Each row is something `cycle.sh` learned the hard way (HANDOVER.md). In the app it becomes
a visible state, not a log line.

| Lesson | Native mechanism | Shown as |
|---|---|---|
| Hotspot transfers | `NWPathMonitor` `isExpensive` / `isConstrained` | Pill "Paused: on iPhone hotspot" |
| Sleep on battery killed jobs | `IOPMAssertion` while running; pause on battery unless allowed | "Paused: on battery. Plug in to continue" |
| PhotoKit fills the Photos library | chunk to free space, 10 GiB floor | "Waiting for disk space (12 GB free)" |
| Drive quota 403/429 | exponential backoff 2→30 min, give up after 3 h | "Google Drive is rate-limiting. Retrying at 14:20" |
| Job died unnoticed | agent heartbeat; app and menu bar watch it | Notification with sound; Activity shows the reason |
| Bin rename matching | ported `Bin` check | Never surfaced unless it refuses |

## 9. Code layout

```
Package.swift                 (macOS 14)
Sources/ImageCore/            engine, no UI. Ledger (SQLite, same schema + migrations),
                              Selector, Planner, PhotoKit, Drive, Verify, Remove, Bin, Doctor
Sources/ImageAgent/           login-item agent: job runner, guards, notifications
Sources/ImageManager/         SwiftUI app: Theme.swift (from Sentinel), tabs, onboarding
Tests/ImageCoreTests/         ported from tests/*.py first
```

`spikes/iimphotos` (Scan, Export, Delete, Trash) is the seed of `ImageCore/PhotoKit`.

## 10. Phases

| Phase | Ships | Engine |
|---|---|---|
| A | App shell, all tabs read-only, onboarding, Checkup, Library | `CLIEngine` on the owner's Mac |
| B | Recipes and task flow, Activity, Bin; agent replaces `cycle.sh` | `CLIEngine` |
| C | Native scan, ledger, selectors, Drive OAuth + upload + verify | `NativeEngine` behind a flag; both run, results diffed |
| D | Native removal; notarised beta to outside users | `NativeEngine` only |

Removal is the last thing ported and the first thing tested.

## 11. Open questions for the owner

None open. All settled 2026-10-25.
