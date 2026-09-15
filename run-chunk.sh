#!/usr/bin/env bash
#
# Run one sync chunk detached, so it survives the terminal closing.
#
# A chunk takes hours. A process started from a terminal is a child of that
# shell and dies with SIGHUP when the window closes, which is a bad way to lose
# an overnight run. This detaches it, keeps the Mac awake, and writes a log you
# can check later.
#
# Usage:
#   ./run-chunk.sh                                  # camera photos, config budget
#   ./run-chunk.sh --source camera --budget 15GB
#   ./run-chunk.sh --source whatsapp --type photo --older-than 1y
#
#   ./run-chunk.sh --status      # is it running, and how far along
#   ./run-chunk.sh --stop        # stop it cleanly; resume with another run
#
# Interrupting is safe. Every asset is journalled before it is fetched, no
# partial file is ever mistaken for a verified one, and the next run continues
# from the ledger rather than starting over.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATE_DIR="${IIM_STATE_DIR:-$HOME/.iphone-image}"
LOG="$STATE_DIR/chunk.log"
PIDFILE="$STATE_DIR/chunk.pid"
PYTHON="$HERE/.venv/bin/python"

mkdir -p "$STATE_DIR"

running() {
    [ -f "$PIDFILE" ] || return 1
    local pid
    pid="$(cat "$PIDFILE" 2>/dev/null || echo)"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# The log accumulates across runs, so anything measured over the whole file
# reports previous runs as this one. --status claimed "5390 assets" for a run
# that had done 488, and it would have reported a long-finished run's error as
# a current one. Everything below reads only the slice since the last banner.
current_run() {
    [ -f "$LOG" ] || return 0
    awk '/^started /{slice = ""} {slice = slice $0 ORS} END {printf "%s", slice}' "$LOG"
}

case "${1:-}" in
--status)
    if running; then
        echo "  running, pid $(cat "$PIDFILE")"
    else
        echo "  not running"
    fi
    if [ -f "$LOG" ]; then
        slice="$(current_run)"
        echo "  log: $LOG"
        # awk counts rather than `grep -c`, which exits 1 on no matches and
        # would take the whole status block down under `set -e`.
        printf '  started:         %s\n' \
            "$(printf '%s\n' "$slice" | awk 'NR==1 {sub(/^started /, ""); print}')"
        printf '  assets this run: %s\n' \
            "$(printf '%s\n' "$slice" | awk '/^[[:space:]]+\[/ {n++} END {print n+0}')"
        echo "  last line:"
        printf '%s\n' "$slice" | tail -1 | sed 's/^/    /'
        if printf '%s\n' "$slice" | grep -qiE '^error:|Traceback'; then
            echo "  ERRORS PRESENT:"
            printf '%s\n' "$slice" | grep -iE '^error:|Traceback' | tail -3 | sed 's/^/    /'
        fi
    fi
    exit 0
    ;;
--stop)
    if running; then
        pid="$(cat "$PIDFILE")"
        # SIGTERM, not SIGKILL: the run finishes the asset in flight and the
        # journal records the interruption rather than being left mid-write.
        kill -TERM "$pid" 2>/dev/null || true
        echo "  asked pid $pid to stop. Resume with another run; nothing is lost."
    else
        echo "  not running"
    fi
    exit 0
    ;;
esac

if running; then
    echo "  a chunk is already running (pid $(cat "$PIDFILE"))." >&2
    echo "  Check it with: $0 --status" >&2
    exit 1
fi

if [ ! -x "$PYTHON" ]; then
    echo "  no virtualenv at $PYTHON" >&2
    echo "  Create one: python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'" >&2
    exit 1
fi

ARGS=("$@")
if [ ${#ARGS[@]} -eq 0 ]; then
    ARGS=(--source camera)
fi

{
    echo "=============================================================="
    echo "started $(date '+%Y-%m-%d %H:%M:%S')  args: ${ARGS[*]}"
    echo "=============================================================="
} >> "$LOG"

# caffeinate keeps the Mac from idling, sleeping its disk, or dimming out from
# under a multi-hour transfer. nohup and setsid detach it from this terminal.
cd "$HERE"
PYTHONPATH="$HERE/src" nohup caffeinate -dimsu \
    "$PYTHON" -u -m iphone_image sync "${ARGS[@]}" --apply \
    >> "$LOG" 2>&1 &

CHILD=$!
echo "$CHILD" > "$PIDFILE"

cat <<INFO

  started, pid $CHILD

  It will keep running when you close this window.

  check     $0 --status
  watch     tail -f $LOG
  stop      $0 --stop

  Interrupting is safe: the next run resumes from the ledger.
INFO
