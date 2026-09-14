#!/usr/bin/env bash
# Reports how far the Mac's Photos library has got syncing from iCloud.
# Read-only: works on a copy, never touches the live database.
set -euo pipefail

TARGET="${1:-94180}"   # expected final item count, from the Photos app on the iPhone
LIB="${PHOTOS_LIBRARY:-$HOME/Pictures/Photos Library.photoslibrary}"
DB="$LIB/database/Photos.sqlite"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [ ! -r "$DB" ]; then
    echo "Cannot read $DB"
    echo "Grant Terminal Full Disk Access, or set PHOTOS_LIBRARY."
    exit 1
fi

# Copy the WAL too, or the counts lag behind whatever Photos has just written.
cp "$DB" "$TMP/p.sqlite"
[ -r "$DB-wal" ] && cp "$DB-wal" "$TMP/p.sqlite-wal" 2>/dev/null || true
[ -r "$DB-shm" ] && cp "$DB-shm" "$TMP/p.sqlite-shm" 2>/dev/null || true

q() { sqlite3 "$TMP/p.sqlite" "$1" 2>/dev/null || echo "?"; }

TOTAL=$(q "select count(*) from ZASSET where ZTRASHEDSTATE=0;")
IMAGES=$(q "select count(*) from ZASSET where ZTRASHEDSTATE=0 and ZKIND=0;")
VIDEOS=$(q "select count(*) from ZASSET where ZTRASHEDSTATE=0 and ZKIND=1;")
LOCAL=$(q "select count(*) from ZASSET where ZTRASHEDSTATE=0 and ZCLOUDLOCALSTATE=0;")
REMOTE=$(q "select count(*) from ZASSET where ZTRASHEDSTATE=0 and ZCLOUDLOCALSTATE<>0;")
NEWEST=$(q "select datetime(max(ZDATECREATED)+978307200,'unixepoch') from ZASSET where ZTRASHEDSTATE=0;")
SIZE=$(du -sh "$LIB" 2>/dev/null | awk '{print $1}')
FREE=$(df -h /System/Volumes/Data | awk 'NR==2{print $4}')

PCT=0
[ "$TARGET" -gt 0 ] && PCT=$(( TOTAL * 100 / TARGET ))

printf '\n  Photos library sync\n  -------------------\n'
printf '  assets            %'"'"'d  of ~%'"'"'d expected  (%d%%)\n' "$TOTAL" "$TARGET" "$PCT"
printf '  photos / videos   %'"'"'d / %'"'"'d\n' "$IMAGES" "$VIDEOS"
printf '  local / in iCloud %'"'"'d / %'"'"'d\n' "$LOCAL" "$REMOTE"
printf '  newest item       %s\n' "$NEWEST"
printf '  library on disk   %s   (free: %s)\n\n' "$SIZE" "$FREE"

if [ "$VIDEOS" -eq 0 ]; then
    echo "  NOT DONE: zero videos. Video syncs late, so this is the clearest signal."
elif [ "$TOTAL" -lt $(( TARGET * 98 / 100 )) ]; then
    echo "  NOT DONE: still short of the expected count."
elif [ "$REMOTE" -eq 0 ]; then
    echo "  SUSPICIOUS: nothing is iCloud-only. Check you picked Optimise Mac Storage,"
    echo "  not Download Originals, or the disk will fill."
else
    echo "  LOOKS DONE. Run the same command again in a few minutes; if the numbers"
    echo "  have not moved, it has settled."
fi
