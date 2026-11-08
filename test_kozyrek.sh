#!/usr/bin/env bash
# Smoke test: the config must load and keep running for 3 s without a single QML
# WARN/ERROR. Runs in dev mode so it never fights the live bar for the exclusive
# zone or the notification D-Bus name.
set -u
cd "$(dirname "$0")"
LOG=$(mktemp)
# Own tvd socket: tvd takes over an existing socket, which would cut off the live bar's.
TVSOCK=$(mktemp -u)
# Own collab helper too: the live one keeps the watched sessions.
COLLABSOCK=$(mktemp -u); COLLABUNIT="kozyrek-collab-test-$$"
KOZYREK_DEV=1 KOZYREK_TV_SOCK="$TVSOCK" KOZYREK_COLLAB_SOCK="$COLLABSOCK" KOZYREK_COLLAB_UNIT="$COLLABUNIT" KOZYREK_COLLAB_NOTIFY=0 \
    timeout 3 qs -p "$PWD" --no-color >"$LOG" 2>&1
rc=$?; rm -f "$TVSOCK"
systemctl --user stop "$COLLABUNIT" 2>/dev/null; rm -f "$COLLABSOCK"
if [[ $rc -ne 124 ]]; then cat "$LOG"; echo "FAIL: qs exited early (rc=$rc)"; rm -f "$LOG"; exit 1; fi
if grep -E '^\s*(WARN|ERROR)' "$LOG"; then echo "FAIL: QML warnings/errors above"; rm -f "$LOG"; exit 1; fi
rm -f "$LOG"; echo "OK: config loads clean"
