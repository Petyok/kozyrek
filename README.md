# 🐐rek

[![vibecoded](https://img.shields.io/badge/vibecoded-100%25-b69ad6)](#model-credits) [![release](https://img.shields.io/github/v/release/Petyok/kozyrek)](https://github.com/Petyok/kozyrek/releases) [![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE) ![quickshell](https://img.shields.io/badge/quickshell-0.3-705492)

One Quickshell process for a Hyprland desktop: bar, notification daemon and
OSD. Built to replace HyprPanel + swayosd + two standalone widgets on a 2-core
laptop where every resident process counts.

Measured on the target laptop (i5-5350U, 8 GB), 10 min idle after the switch: **142 MB PSS, 1.8 % CPU** for the whole bar + notification daemon + OSD, plus 10 MB for the zhor collector. It replaced ~440 MB across seven processes (HyprPanel 103, its clock and zhor widgets 72+77, swayosd 34, nwg-dock 79, nm-applet 8, and the shared Qt libraries counted once more).

> ⚠️ Personal config that happens to be public. Hardcoded for one laptop:
> 1440×900, `intel_backlight`, `smc::kbd_backlight`, wg0/wg1/singbox_tun VPN
> pills, Batumi weather. Fork and edit `shell.qml` / `modules/`; there is no
> config file on purpose.

## What's in the bar

| left | center | right |
|---|---|---|
| power menu · workspaces (app icons) · window title | [zhor](https://github.com/Petyok/zhor) · clock / calendar / weather | VPN pills · keyboard layout · DND · wifi · bluetooth · battery · tray |

Plus: notification popups (top-right, DND, actions, images) and an OSD for
volume / screen brightness / keyboard brightness driven over IPC.

## Install

```
git clone --recursive https://github.com/Petyok/kozyrek ~/kozyrek && ~/kozyrek/install.sh
```
`install.sh` prints the `hyprland.conf` lines. Quickshell ignores named configs
while `~/.config/quickshell/shell.qml` exists — move it away first.

## Layout

One module per file under `modules/`; `shell.qml` is the layout. `Theme.qml`
holds the palette, `Kozy.qml` the shared state (fullscreen flag, DND, the one
open popup). Pure helpers live in `lib/parse.js` and are the only unit-tested
part (`node --test test_parse.mjs`); `./test_kozyrek.sh` checks the config
loads without QML warnings.

Dev mode: `KOZYREK_DEV=1 qs -p ~/kozyrek` puts the bar at the bottom, ignores
exclusive zones and skips the notification daemon, so it can run next to your
current bar.

## Model credits

Designed and written with Claude (Fable 5.1) in Claude Code; the spec and the
plan were reviewed adversarially by a second Claude instance before a line of
QML was written. Human in the loop: taste, testing on the actual laptop, and
the goat.
