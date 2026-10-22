#!/usr/bin/env bash
# Smoke test: the config must load and keep running for 3 s without a single QML
# WARN/ERROR. Runs in dev mode so it never fights the live bar for the exclusive
# zone or the notification D-Bus name.
set -u
cd "$(dirname "$0")"
LOG=$(mktemp)
# Own tvd socket: tvd takes over an existing socket, which would cut off the live bar's.
TVSOCK=$(mktemp -u)
KOZYREK_DEV=1 KOZYREK_TV_SOCK="$TVSOCK" timeout 3 qs -p "$PWD" --no-color >"$LOG" 2>&1
rc=$?; rm -f "$TVSOCK"
if [[ $rc -ne 124 ]]; then cat "$LOG"; echo "FAIL: qs exited early (rc=$rc)"; rm -f "$LOG"; exit 1; fi
if grep -E '^\s*(WARN|ERROR)' "$LOG"; then echo "FAIL: QML warnings/errors above"; rm -f "$LOG"; exit 1; fi
rm -f "$LOG"; echo "OK: config loads clean"
