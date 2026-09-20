#!/bin/bash
# Records whether the video job is ALIVE, every 10 minutes, and says loudly when
# it is not. A watcher that greps the log for events is silent when the process
# is gone, which is how 12 hours were lost on 2026-09-19/20.
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
w,pr=Selector(source='camera', media_type='video').where()
print(c.execute(f\"SELECT COUNT(*) FROM assets a WHERE {w} AND a.local_status NOT IN ('LOCAL_VERIFIED','RELEASED')\", pr).fetchone()[0])" 2>/dev/null || echo "?")
}
while pgrep -f "cycle.sh video" >/dev/null; do
  say "alive: $(df -g / | awk 'NR==2{print $4}') GiB free, $(left) videos left, on $(pmset -g batt | grep -o "'[^']*'" | head -1), last: $(grep 'camera/video\]' "$CYCLE" | tail -1 | cut -c1-110)"
  sleep 600
done
say "JOB GONE. Last lines:"
grep 'camera/video\]\|\[chain\]' "$CYCLE" | tail -4 >> "$LOG"
