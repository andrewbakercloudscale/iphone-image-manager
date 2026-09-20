#!/bin/bash
# fetch -> upload -> release, repeating, never leaving the Mac under the floor.
#
# Each step gates the next, and the floor is checked before every fetch rather
# than once at the start: a chunk that was safe to begin is not safe to begin
# again after the previous one filled the disk.
#
# Usage: cycle.sh [photo|video] [cycles] [source] [older-than]
#   cycle.sh video 40                       camera video, the 2026-09-18 job
#   RELEASE=0 cycle.sh photo 6 screenshot 1y   screenshots older than a year,
#                                              holding the Mac copy for removal
#
# RELEASE=0 skips the release step. Nothing needs it today: removal is gated on
# Google Drive rather than a Mac copy (docs/SAFETY.md 1b), so the Mac copy can go
# the moment Drive verifies it, which frees disk for the next chunk.
#
# EMPTY_BIN=0 stops the script emptying the Bin itself. By default, when a chunk
# will not fit, it lists the Bin through Finder and empties it ONLY if every item
# is a file this tool released (name matches a RELEASED ledger row). Anything
# else in the Bin -- the user's own files -- stops the job with exit 2 instead.
set -u

TYPE="${1:-photo}"
CYCLES="${2:-12}"
SOURCE="${3:-camera}"
OLDER="${4:-}"
RELEASE="${RELEASE:-1}"
EMPTY_BIN="${EMPTY_BIN:-1}"
case "$TYPE" in
  photo|video) ;;
  *) echo "usage: cycle.sh [photo|video] [cycles] [source] [older-than]" >&2; exit 64 ;;
esac
case "$CYCLES" in
  ''|*[!0-9]*) echo "cycles must be a number" >&2; exit 64 ;;
esac

cd /Users/cp363412/Desktop/github/iphone-image-manager || exit 1
export PYTHONPATH=src
PY=./.venv/bin/python
LOG=~/.iphone-image/cycle.log
DB=~/Desktop/iphone/iphone-image.sqlite
TAG="$SOURCE/$TYPE"

# What `sync` itself demands before it will start a chunk, in BYTES, read from
# the same config it reads: chunk_bytes + free_space_floor. The earlier version
# compared round GB numbers against `df -g`, which reports GiB, and the two
# drifted: at 25 "GB" (27.0 GB real) it refused a chunk sync would have taken,
# and the version before that waved through one sync refused. One unit, one
# source of truth.
read -r CHUNK_B FLOOR_B <<<"$($PY -c '
from iphone_image.config import load_config
c=load_config()
print(c.chunking.chunk_bytes, c.chunking.free_space_floor)')"
NEED_B=$((CHUNK_B + FLOOR_B))
HEADROOM_B=$((NEED_B + 10 * 1024**3))
gb() { LC_NUMERIC=C awk -v b="$1" 'BEGIN{printf "%.1f", b/1e9}'; }

# Selector flags shared by every verb, so the log can be read against the plan.
SEL=(--source "$SOURCE" --type "$TYPE")
[ -n "$OLDER" ] && SEL+=(--older-than "$OLDER")

say() { echo "[$(date '+%m-%d %H:%M:%S')] [$TAG] $*" | tee -a "$LOG"; }
# Same measurement sync makes: the volume the archive lives on.
free_b() { $PY -c "import shutil,os; print(shutil.disk_usage(os.path.expanduser('~/Desktop/iphone')).free)"; }

releasable() {
  $PY -c "
import sqlite3
c=sqlite3.connect('file:$DB?mode=ro',uri=True)
print(c.execute(\"SELECT COUNT(*) FROM assets WHERE local_status='LOCAL_VERIFIED' AND cloud_status='CLOUD_VERIFIED'\").fetchone()[0])" 2>/dev/null || echo 0
}

# Counts what `sync` would actually fetch: everything not already held, which
# includes FAILED. The earlier version counted only DISCOVERED, so a run whose
# last stragglers had failed once reported "nothing left to fetch" and never
# retried them -- while sync itself would have.
to_fetch() {
  $PY -c "
import sqlite3
from iphone_image.selector import Selector
c=sqlite3.connect('file:$DB?mode=ro',uri=True)
w,pr=Selector(source='$SOURCE', media_type='$TYPE', older_than='$OLDER' or None).where()
print(c.execute(f\"SELECT COUNT(*) FROM assets a WHERE {w} AND a.local_status NOT IN ('LOCAL_VERIFIED','RELEASED')\", pr).fetchone()[0])" 2>/dev/null || echo 0
}

# Empty the Bin if, and only if, every item in it is a file this tool released.
# Prints what it decided; returns 0 if the Bin is now empty, 1 otherwise.
# ~/.Trash is unreadable from a shell (macOS privacy) but Finder can list it, and
# Finder's `empty trash` shows no dialog (tested 2026-09-20).
empty_bin_if_ours() {
  local names foreign
  names=$(timeout 60 osascript -e 'tell application "Finder" to get name of every item in trash' 2>/dev/null | tr ',' '\n' | sed 's/^ //') || { say "Bin: Finder did not answer"; return 1; }
  [ -z "$names" ] && { say "Bin: already empty"; return 0; }
  foreign=$(printf '%s\n' "$names" | $PY -c "
import sqlite3, sys, re, os
c=sqlite3.connect('file:$DB?mode=ro',uri=True)
ours=set()
for f,claim in c.execute(\"SELECT filename, archive_claim FROM assets WHERE local_status='RELEASED'\"):
    ours.add(f)
    if claim: ours.add(os.path.basename(claim))
for line in sys.stdin:
    n=line.rstrip('\n')
    if not n: continue
    # Finder renames a second copy 'IMG_1 2.MOV'; match on the original name.
    base=re.sub(r' \d+(\.[^.]+)$', r'\1', n)
    if n not in ours and base not in ours: print(n)
")
  local total; total=$(printf '%s\n' "$names" | grep -c .)
  if [ -n "$foreign" ]; then
    say "Bin: holds $total item(s) but $(printf '%s\n' "$foreign" | wc -l | tr -d ' ') are NOT files this tool released. Leaving it alone. First few: $(printf '%s\n' "$foreign" | head -3 | tr '\n' ' ')"
    return 1
  fi
  say "Bin: all $total item(s) are released assets (hash-verified in Drive). Emptying."
  timeout 600 osascript -e 'tell application "Finder" to empty trash' >/dev/null 2>&1 || { say "Bin: empty trash failed"; return 1; }
  local left; left=$(timeout 60 osascript -e 'tell application "Finder" to count of items in trash' 2>/dev/null || echo "?")
  say "Bin: emptied, $left item(s) remain, $(gb "$(free_b)") GB free"
  [ "$left" = "0" ]
}

# Wait for anything already running.
while pgrep -f 'iphone_image cloud' >/dev/null || pgrep -f 'iphone_image sync' >/dev/null; do sleep 60; done

say "starting: $CYCLES cycle(s), $(to_fetch) left to fetch, $(gb "$(free_b)") GB free, a chunk needs $(gb "$NEED_B") GB, release=$RELEASE, empty_bin=$EMPTY_BIN"

prev_left=-1
stalled=0
for cycle in $(seq 1 "$CYCLES"); do
  say "=== cycle $cycle/$CYCLES: $(gb "$(free_b)") GB free, $(to_fetch) left to fetch ==="

  # 1. Release anything already safe in the cloud. Always first: it is the
  #    only step that gives disk back, and the fetch below needs room.
  #    Deliberately unscoped: a leftover verified file is disk this job needs.
  if [ "$RELEASE" = "1" ]; then
    n=$(releasable)
    if [ "$n" -gt 0 ]; then
      say "releasing $n file(s) already verified in Drive"
      $PY -m iphone_image release --apply >> "$LOG" 2>&1 || { say "release failed, stopping"; exit 1; }
      say "after release: $(gb "$(free_b)") GB free (the Bin still holds it until emptied)"
    fi
  else
    say "release is OFF: Mac copies are held so remove-from-iphone can run first"
  fi

  # 2. Stop if there is nothing left to do.
  left=$(to_fetch)
  if [ "$left" -eq 0 ]; then say "nothing left to fetch. done."; break; fi

  # A loop that makes no progress must stop, not spin. One cycle of no change is
  # normal (it may only have uploaded a backlog); two in a row means whatever is
  # failing will fail again, and burning the remaining cycles hides it.
  if [ "$left" -eq "$prev_left" ]; then stalled=$((stalled + 1)); else stalled=0; fi
  prev_left=$left
  if [ "$stalled" -ge 2 ]; then
    say "no progress: $left left to fetch, unchanged for $stalled cycles. STOPPING. See the sync errors above."
    exit 4
  fi

  # 3. The floor, checked here rather than once at the top. Released bytes sit
  #    in the Bin until it is emptied, so free space falls across a long job
  #    even though every cycle releases. Empty the Bin when the chunk will not
  #    fit, but only if everything in it is ours.
  now=$(free_b)
  if [ "$now" -lt "$NEED_B" ] && [ "$EMPTY_BIN" = "1" ]; then
    say "only $(gb "$now") GB free and a chunk needs $(gb "$NEED_B") GB. Checking the Bin."
    empty_bin_if_ours || true
    now=$(free_b)
  fi
  if [ "$now" -lt "$NEED_B" ]; then
    say "only $(gb "$now") GB free and a chunk needs $(gb "$NEED_B") GB ($(gb "$CHUNK_B") GB chunk + $(gb "$FLOOR_B") GB floor). EMPTY THE BIN or free disk, then re-run."
    exit 2
  fi
  if [ "$now" -lt "$HEADROOM_B" ]; then
    say "WARNING: $(gb "$now") GB free. The next cycle will need the Bin emptied."
  fi

  # 4. Fetch the next chunk, then mirror it.
  say "fetching the next chunk"
  $PY -m iphone_image sync "${SEL[@]}" --apply >> "$LOG" 2>&1 \
    || say "sync returned non-zero; continuing to upload what landed"
  say "uploading what landed"
  $PY -m iphone_image cloud "${SEL[@]}" --apply >> "$LOG" 2>&1 \
    || say "cloud returned non-zero; next cycle will retry"
done

say "cycle finished: $(gb "$(free_b)") GB free, $(to_fetch) still to fetch"
