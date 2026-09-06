#!/usr/bin/env bash
set -euo pipefail
LINK="$HOME/.config/quickshell/kozyrek"
qs kill -c kozyrek >/dev/null 2>&1 && echo "stopped kozyrek" || true
[[ -L $LINK ]] && rm "$LINK" && echo "removed $LINK"
echo "done. Remove the exec-once/bind lines from hyprland.conf yourself; ~/kozyrek is untouched."
