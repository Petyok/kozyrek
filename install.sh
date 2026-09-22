#!/usr/bin/env bash
# kozyrek installer. Idempotent. Does NOT edit hyprland.conf: prints the lines.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LINK="$HOME/.config/quickshell/kozyrek"

command -v qs >/dev/null || { echo "quickshell (qs) not found — pacman -S quickshell"; exit 1; }
command -v brightnessctl >/dev/null || echo "note: brightnessctl missing — brightness OSD will not work"
command -v python3 >/dev/null || { echo "python3 not found (zhor needs it)"; exit 1; }

if [[ ! -e $SRC/zhor/Zhor.qml ]]; then
    if [[ -d $SRC/.git ]]; then git -C "$SRC" submodule update --init zhor
    else echo "zhor/ is empty and this is not a git checkout — clone https://github.com/Petyok/zhor into $SRC/zhor"; exit 1; fi
fi

mkdir -p "$(dirname "$LINK")"
ln -sfn "$SRC" "$LINK"
echo "linked $LINK -> $SRC"

if [[ -e $HOME/.config/quickshell/shell.qml ]]; then
    echo "WARNING: ~/.config/quickshell/shell.qml exists. Quickshell then ignores named configs,"
    echo "         so 'qs -c kozyrek' will not work until you move that file away."
fi

cat <<EOT

Add to ~/.config/hypr/hyprland.conf (and remove your old bar / swayosd / mako):

  exec-once = qs -c kozyrek
  bind  = SUPER SHIFT, ESCAPE,         exec, qs -c kozyrek ipc call zhor toggle
  bind  = SUPER, COMMA,                exec, qs -c kozyrek ipc call dnd toggle
  binde = , XF86AudioRaiseVolume,      exec, qs -c kozyrek ipc call osd volume +5
  binde = , XF86AudioLowerVolume,      exec, qs -c kozyrek ipc call osd volume -5
  binde = , XF86AudioMute,             exec, qs -c kozyrek ipc call osd volume mute
  binde = , XF86MonBrightnessUp,       exec, qs -c kozyrek ipc call osd brightness +5
  binde = , XF86MonBrightnessDown,     exec, qs -c kozyrek ipc call osd brightness -5
  binde = , XF86KbdBrightnessUp,       exec, qs -c kozyrek ipc call osd kbd +5
  binde = , XF86KbdBrightnessDown,     exec, qs -c kozyrek ipc call osd kbd -5

If mako is installed, mask it so D-Bus activation cannot steal the notification name:
  systemctl --user mask mako.service
EOT
