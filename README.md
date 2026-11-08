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
| power menu · workspaces (app icons) · window title | [zhor](https://github.com/Petyok/zhor) · clock / calendar / weather / timers + alarms · screen recording | call recorder · omp chat · VPN pills · keyboard layout · DND · volume · ping · wifi · bluetooth · battery · tray |

Plus: notification popups (top-right, DND, actions, images) and an OSD for
volume / screen brightness / keyboard brightness driven over IPC.

The call recorder is one pill: click to record a call, click again, answer
"Turn recorded audio to text?", and a local speaker-labelled transcript
(Me / Speaker N, Russian and English) lands in `~/Calls/`. It works without
headphones (echo cancellation on the mic, the other side taken from the
speaker output). Setup, internals and limits: [docs/rec.md](docs/rec.md).

Screen recording shows next to the clock while `wf-recorder` runs: a red
camera, the elapsed time and whether the mic is in the recording. Click stops,
right click toggles the mic. Recording an area also dims the rest of the screen,
rings the area in red and floats the same controls (time, mic, Stop) just
outside it, above when there is room, so none of it lands in the video. It is
driven by a personal script outside this repo, `~/.local/bin/screenrec.sh`
(`status` / `mic` verbs, stop by running it again), which calls
`qs -c kozyrek ipc call screenrec refresh` after it starts the recorder.

Dictation, macOS style: tap right Cmd and talk, Russian and English, mixed
freely. The words land in whatever window has focus while you speak; tap
again, or pause for 10 s, to stop. A frosted capsule at the bottom shows the
mic level and the latest words. Recognition runs on the laptop's CPU with the
call recorder's model, loaded only for the dictation. Setup, internals and
limits: [docs/dict.md](docs/dict.md).

TV remote for a CVTE / MHShare TV (the one the MHShare phone app drives): a
floating overlay with a touchpad that is the TV screen in miniature, d-pad,
Back/Home/Menu and volume, plus `tv/tvctl` for keybinds and scripts. The link
to the TV is opened on first use and dropped after 2 min idle. "Mirror my screen"
shows the laptop screen on the TV (Cast Streaming, no browser). Protocol and
limits: [docs/tv.md](docs/tv.md).

omp chat: follow [omp](https://omp.sh) Collab sessions from the bar, on this
laptop or on a server, as tabs in a popup: the live transcript (streaming text,
thinking, tool calls with their output, images), subagents and their
transcripts, a message box, Stop, and the agent's questions to answer. A
notification says when a watched session finishes a turn or asks something.
Setup, internals and limits: [docs/collab.md](docs/collab.md).

## Install

```
git clone --recursive https://github.com/Petyok/kozyrek ~/kozyrek && ~/kozyrek/install.sh
```
`install.sh` prints the `hyprland.conf` lines. Quickshell ignores named configs
while `~/.config/quickshell/shell.qml` exists — move it away first.

## Layout

One module per file under `modules/`; `shell.qml` is the layout. `Theme.qml`
holds the palette, `Kozy.qml` the shared state (fullscreen flag, DND, the one
open popup). Pure helpers live in `lib/parse.js` (`node --test test_parse.mjs`);
the recorder's and the dictation's Python helpers live in `rec/` and `dict/` with their own tests,
the omp chat's helper in `collab/`;
`./test_kozyrek.sh` checks the config loads without QML warnings.

Dev mode: `KOZYREK_DEV=1 qs -p ~/kozyrek` puts the bar at the bottom, ignores
exclusive zones and skips the notification daemon, so it can run next to your
current bar.

## Model credits

Designed and written with Claude (Fable 5.1) in Claude Code; the spec and the
plan were reviewed adversarially by a second Claude instance before a line of
QML was written. Human in the loop: taste, testing on the actual laptop, and
the goat.
