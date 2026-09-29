#!/bin/bash
# Wait for the screenshot cycle to end, then back up WhatsApp media older than
# 18 months: photos first (small, many), then videos (most of the bytes).
#
# The owner's decision, 2026-09-29: one flat Drive folder, "WhatsApp Media",
# clashing names numbered, favourites and their own albums kept on the phone.
# Config: cloud.whatsapp_destination, organization.flat_channels,
# remove_from_iphone.keep_user_album_channels.
#
# Sequential for the same reason as after-video.sh: one PhotoKit exporter at a
# time. Each step starts only if the one before it FINISHED, so a stop on the
# disk floor or a failed release is not buried under the next job.
set -u
LOG=~/.iphone-image/cycle.log
say() { echo "[$(date '+%m-%d %H:%M:%S')] [chain] $*" | tee -a "$LOG"; }
finished() { grep "\[$1\]" "$LOG" | tail -n 8 | grep -q "nothing left to fetch"; }

say "waiting for the screenshot cycle to end"
while pgrep -f "cycle.sh photo .* screenshot" >/dev/null; do sleep 120; done
if ! finished "screenshot/photo"; then
  say "the screenshot cycle did not finish cleanly, so WhatsApp is NOT starting"
  exit 3
fi

nohup ~/.iphone-image/watch-job.sh whatsapp 18m > /dev/null 2>&1 &

say "screenshots finished. Starting WhatsApp photos older than 18m"
caffeinate -dimsu ~/.iphone-image/cycle.sh photo 40 whatsapp 18m
if ! finished "whatsapp/photo"; then
  say "the WhatsApp photo cycle did not finish cleanly, so WhatsApp videos are NOT starting"
  exit 3
fi

say "WhatsApp photos finished. Starting WhatsApp videos older than 18m"
exec caffeinate -dimsu ~/.iphone-image/cycle.sh video 40 whatsapp 18m
