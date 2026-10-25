#!/usr/bin/env bash
# Builds Photo Sentinel.app and installs it to ~/Applications.
#
#   bash scripts/build-app.sh            # test, build, bundle, install, launch
#   bash scripts/build-app.sh --no-open  # same, without launching
#
# Ad-hoc signed: it runs on this Mac only. Sharing it needs a Developer ID and
# notarisation (Phase D). Phase A reads the Python engine's ledger and logs.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "== tests"
swift test >/dev/null 2>&1 || { swift test 2>&1 | tail -20; echo "tests failed, not building"; exit 1; }

echo "== build"
swift build -c release
BIN="$(swift build -c release --show-bin-path)/PhotoSentinel"

APP="build/Photo Sentinel.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$BIN" "$APP/Contents/MacOS/PhotoSentinel"
cp Resources/Info.plist "$APP/Contents/Info.plist"
cp Resources/AppIcon.icns "$APP/Contents/Resources/AppIcon.icns"
codesign --force --sign - "$APP"
codesign --verify "$APP"

DEST="$HOME/Applications/Photo Sentinel.app"
mkdir -p "$HOME/Applications"
if pgrep -x PhotoSentinel >/dev/null; then
    pkill -TERM -x PhotoSentinel 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do pgrep -x PhotoSentinel >/dev/null || break; sleep 0.5; done
    pkill -KILL -x PhotoSentinel 2>/dev/null || true
fi
rm -rf "$DEST"
cp -R "$APP" "$DEST"
echo "== installed: $DEST"

if [[ "${1:-}" != "--no-open" ]]; then
    open "$DEST"
    echo "== launched"
fi
