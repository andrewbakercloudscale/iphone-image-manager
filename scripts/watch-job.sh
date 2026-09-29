#!/bin/bash
# watch-video.sh for any source: records whether `cycle.sh <type> N <source>`
# is ALIVE every 10 minutes and raises a macOS notification with a sound when
# no cycle for that source is left running. Usage: watch-job.sh <source> [older-than]
#
# Tolerates the gap between the photo and video jobs of a chain: it only
# declares the job gone after two checks two minutes apart both find nothing.
set -u
SOURCE=${1:?usage: watch-job.sh <source> [older-than]}
OLDER=${2:-}
LOG=~/.iphone-image/watch.log
CYCLE=~/.iphone-image/cycle.log
REPO=/Users/cp363412/Desktop/github/iphone-image-manager
say() { echo "[$(date '+%m-%d %H:%M:%S')] $*" >> "$LOG"; }
running() { pgrep -f "cycle.sh (photo|video) [0-9]+ $SOURCE" >/dev/null; }
left() {
  (cd "$REPO" && PYTHONPATH=src ./.venv/bin/python -c "
import sqlite3, os
from iphone_image.selector import Selector
c=sqlite3.connect('file:'+os.path.expanduser('~/Desktop/iphone/iphone-image.sqlite')+'?mode=ro',uri=True)
w,pr=Selector(source='$SOURCE', older_than='$OLDER' or None).where()
print(c.execute(f\"SELECT COUNT(*) FROM assets a WHERE {w} AND a.local_status NOT IN ('LOCAL_VERIFIED','RELEASED')\", pr).fetchone()[0])" 2>/dev/null || echo "?")
}
sleep 60   # let the first cycle start
while :; do
  if ! running; then sleep 120; running || break; fi
  say "alive: $(df -g / | awk 'NR==2{print $4}') GiB free, $(left) $SOURCE items left, on $(pmset -g batt | grep -o "'[^']*'" | head -1), last: $(grep "\[$SOURCE/" "$CYCLE" | tail -1 | cut -c1-110)"
  sleep 600
done
why=$(grep "\[$SOURCE/" "$CYCLE" | tail -1 | cut -c22-150)
say "$SOURCE JOB GONE. Last lines:"
grep "\[$SOURCE/\|\[chain\]" "$CYCLE" | tail -4 >> "$LOG"
if grep "\[$SOURCE/video\]" "$CYCLE" | tail -3 | grep -q "nothing left to fetch"; then
  osascript -e "display notification \"All $SOURCE media is backed up. Next: audit, then remove from the phone.\" with title \"iphone-image: $SOURCE finished\" sound name \"Glass\"" >/dev/null 2>&1
else
  osascript -e "display notification \"$(echo "$why" | tr -d '"')\" with title \"iphone-image: $SOURCE job STOPPED\" sound name \"Sosumi\"" >/dev/null 2>&1
fi
