#!/bin/bash
# watch-video.sh for the screenshot job: records whether `cycle.sh photo ...
# screenshot` is ALIVE every 10 minutes, and raises a macOS notification with a
# sound when it is gone. Added 2026-09-28: after-video.sh started the screenshot
# job with nothing watching it.
set -u
LOG=~/.iphone-image/watch.log
CYCLE=~/.iphone-image/cycle.log
REPO=/Users/cp363412/Desktop/github/iphone-image-manager
say() { echo "[$(date '+%m-%d %H:%M:%S')] $*" >> "$LOG"; }
left() {
  (cd "$REPO" && PYTHONPATH=src ./.venv/bin/python -c "
import sqlite3, os
from iphone_image.selector import Selector
c=sqlite3.connect('file:'+os.path.expanduser('~/Desktop/iphone/iphone-image.sqlite')+'?mode=ro',uri=True)
w,pr=Selector(source='screenshot', media_type='photo', older_than='1y').where()
print(c.execute(f\"SELECT COUNT(*) FROM assets a WHERE {w} AND a.local_status NOT IN ('LOCAL_VERIFIED','RELEASED')\", pr).fetchone()[0])" 2>/dev/null || echo "?")
}
while pgrep -f "cycle.sh photo .* screenshot" >/dev/null; do
  say "alive: $(df -g / | awk 'NR==2{print $4}') GiB free, $(left) screenshots left, on $(pmset -g batt | grep -o "'[^']*'" | head -1), last: $(grep 'screenshot/photo\]' "$CYCLE" | tail -1 | cut -c1-110)"
  sleep 600
done
why=$(grep 'screenshot/photo\]' "$CYCLE" | tail -1 | cut -c22-150)
say "SCREENSHOT JOB GONE. Last lines:"
grep 'screenshot/photo\]' "$CYCLE" | tail -4 >> "$LOG"
if grep 'screenshot/photo\]' "$CYCLE" | tail -3 | grep -q "nothing left to fetch"; then
  osascript -e 'display notification "Old screenshots are all fetched and uploaded. Next: audit, then remove from the phone." with title "iphone-image: screenshot job finished" sound name "Glass"' >/dev/null 2>&1
else
  osascript -e "display notification \"$(echo "$why" | tr -d '"')\" with title \"iphone-image: screenshot job STOPPED\" sound name \"Sosumi\"" >/dev/null 2>&1
fi
