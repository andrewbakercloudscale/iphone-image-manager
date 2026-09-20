#!/bin/bash
# Wait for the video cycle to end, then run the screenshot cycle.
#
# Sequential on purpose: two PhotoKit exporters against one library is a
# device-stability risk this project has always avoided, and the disk only has
# room for one 15 GB chunk in flight plus the floor.
#
# Starts only if the video job ended by *finishing*. If it stopped on the disk
# floor or a failed release, screenshots starting behind it would just hit the
# same wall and bury the reason.
#
# The Mac copies are released as they go, like every other job. An earlier
# version held them (RELEASE=0) so remove-from-iphone would find a Mac copy, but
# removal is gated on Drive now (docs/SAFETY.md 1b), so there is nothing to hold
# them for and holding would only fill the disk.
set -u
LOG=~/.iphone-image/cycle.log
say() { echo "[$(date '+%m-%d %H:%M:%S')] [chain] $*" | tee -a "$LOG"; }

say "waiting for the camera/video cycle to end"
while pgrep -f "cycle.sh video" >/dev/null; do sleep 120; done

if ! grep "camera/video\]\|\[video\]" "$LOG" | tail -n 8 | grep -q "nothing left to fetch"; then
  say "the video cycle did not finish cleanly (see the last camera/video lines), so screenshots are NOT starting"
  exit 3
fi

say "video job finished. Starting screenshots older than 1y"
exec ~/.iphone-image/cycle.sh photo 4 screenshot 1y
