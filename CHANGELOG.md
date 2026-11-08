# Changelog

## [Unreleased]

### Added
- TV remote overlay (`Tv.qml`, `tv/tvd.py`, `tv/tvctl`) for CVTE / MHShare TVs: touchpad, d-pad, Back/Home/Menu, volume, keyboard control while it has focus. `tv` IPC target: `ipc call tv toggle` / `ipc call tv key volup`. See `docs/tv.md`.
- Screen mirroring to the TV: "Mirror my screen" in the TV overlay / `tvctl mirror on|off` (`tv/mirror.py`, a Cast Streaming sender for the TV's "Chrome Mirroring" receiver; VAAPI or x264 video, plus what the laptop plays as Opus). Mouse wheel over the touchpad sends d-pad Up/Down (scrolls pages); the apps strip scrolls with the wheel and no longer shows duplicates. See `docs/tv.md`.
- `tvctl yt <link|id>` and a "YouTube link" field in the TV overlay: open a YouTube video or playlist in the TV's own YouTube app (adb VIEW intent), streamed from YouTube, nothing sent from the laptop. "Mirror a window" button / `tvctl mirror window` (experimental, portal window picker).
- The TV overlay's touchpad now shows the TV's own screen live (`tv/view.py`: the TV's `screenrecord` over adb, decoded with ffmpeg; starts when the panel opens, stops when it closes; `tvctl view on|off|status`). Tap on the picture to click there, type in the text field to see it appear. See `docs/tv.md`.
- Timers and alarms in the clock dropdown. One field: `10m`, `1h30m`, `90s` or a bare `10` (minutes) start a timer, `07:30` sets an alarm for the next 07:30, and any text after it is the label; 5/10/30 min chips for the common cases. The pill shows the nearest one (⏳ countdown / ⏰ time) in place of the date. When one fires you get a critical notification (60 s) and a sound; alarms ring through do-not-disturb. Stored in `timers.json` in the state dir, so they survive a restart (one that came due while the bar was off rings when it starts). One-shot, no repeat.
- Call recorder pill (first in the right zone): click records the call (built-in mic through an on-demand WebRTC echo canceller + the speaker output), click again stops and asks "Turn recorded audio to text?". Yes transcribes locally with NeMo-Speech.cpp (Orukeet, a Parakeet TDT 0.6B v3 fine-tune, + Nemotron-3-Diarization; ru/en auto) with a progress fill in the pill and a "Transcript ready" notification; No deletes the recording. Transcripts go to `~/Calls/<date>/transcript.md` + `.json`; audio is deleted after 7 days; recordings stop at 1.5 h. See `docs/rec.md`.
- `rec` IPC target: `qs -c kozyrek ipc call rec toggle` (bound to Super+Alt+R).
- `Pill.qml`: optional `progress` fill and `edge` border colour.
- Dictation, macOS style: tap right Cmd (`bindr = SUPER, SUPER_R`), speak Russian and/or English, and the words are pasted into the focused window as they are recognized; tap again or stay silent for 10 s to stop. A frosted capsule at the bottom shows a live waveform, the latest words and the detected language (`Dictation.qml`, `dict/kozydict.py`). The clipboard is restored afterwards. Recognition runs on the laptop's CPU with Nemotron 3.5 ASR (streaming), loaded per dictation and freed after it. See `docs/dict.md`.
- `dict` IPC target: `qs -c kozyrek ipc call dict toggle`.
- `rec/nemo_speech.py`: interim transcripts (`stream(interim=True)` + `.text`).
- Screen recording pill next to the clock, visible while `wf-recorder` runs: blinking red camera, elapsed time, mic state. Click stops, right click toggles the mic. Reads `~/.local/bin/screenrec.sh status`, follows the recorder with `waitpid`, and survives a bar reload with the right time.
- Area recordings (`ScreenRecFrame.qml`): the screen outside the recorded rectangle is dimmed, a red ring sits just outside it, and a floating time / mic / Stop bar is placed outside it (above, else below, right, left). Nothing is drawn over the area, so the video stays clean; only the controls take input.
- `screenrec` IPC target: `qs -c kozyrek ipc call screenrec refresh`, called by `screenrec.sh` once the recorder is up.
- Call transcription on a GPU host: when its GPU is idle (under 20 % load, ≥ 2.5 GB VRAM free, no ComfyUI job), the diarization and ASR stages run there over one remote shell, else on the laptop as before; a 21 min call takes ~30 s instead of ~20 min. The host keeps the call only in the RAM of one memory-locked, non-dumpable process, writes nothing to disk, and drops it when the job ends, on Cancel, or 60 s after the laptop falls silent. Results are checkpointed on the laptop as they arrive; a lost link reconnects once, then the laptop finishes. The transcribing popup shows where it runs. Off until `~/.config/kozyrek/gpu-host` names the command that reaches the host (e.g. `ssh gpu-box`); setup: `rec/gpu-setup.sh`. See `docs/rec.md`.
- Ping pill next to Wi-Fi: round-trip to 1.1.1.1 every 5 s (one long-lived `ping -O`), so "Wi-Fi connected but no internet" is visible. It shows only while something is wrong (≥ 150 ms yellow, red "lost" for an unanswered probe, dim "offline" while there is no route) and for a minute after; otherwise the latency is a "Ping … · graph ›" line in the Wi-Fi popup. Click opens a latency graph with 10m / 30m / 1h / 3h / 8h / 24h / 3d ranges: bars are the average round-trip per column, red from the top is packet loss, hover a column for its time, avg, max and loss. History (every probe for the last hour, per-minute for 3 days) lives in `ping.json` in the state dir, saved every 5 min and on reload.
- КРЕПОСТНОЙ pill (`modules/Krepostnoy.qml`) for the unattended work agent: dim `⛓` while it listens, `⚒` with a progress fill while it works, then `✔ N коммитов · ТЫ НЕ НУЖЕН`, `¯\_(ツ)_/¯`, a pulsing `✋ ЗАБУКСОВАЛ · ПОМОГИ` or `☠ СМЕРТЬ В НИЩЕТЕ` until you open the popup; a lone `☠` when the unit is off. Reads `~/.local/state/krepostnoy/status.json` (override: `KOZYREK_KREPOSTNOY_STATUS`) and polls the `krepostnoy` user unit. Click lists the last 8 runs with `omp` (resume the session), `diff` and `✕` (hide the run, remembered in the shell state dir) buttons; right click starts/stops the unit.
- omp chat pill (`modules/Collab.qml`, `Collab.qml`, `collab/`): follow omp Collab sessions from the bar as tabs: live transcript (streaming text, thinking, tool calls with output, images), subagents and their transcripts (kill / revive / message), a message box, Stop, and the agent's `ask` questions. Sessions are listed from the hosts in `~/.config/kozyrek/collab-hosts` (`label = command`, e.g. `ssh my-server`) or joined from a pasted link; nothing is joined automatically. Notifications when a watched session finishes a turn, asks something or ends. The helper (`collab/kozyrek-collab.ts`, Bun, omp's own guest code vendored in `collab/vendor/`) runs as the `kozyrek-collab` user unit, keeps room links in memory only, and survives bar reloads. `collab` IPC target: `ipc call collab toggle`. See `docs/collab.md`.

### Changed
- Fewer pills on a normal day: DND shows only while it is on (Super+, turns it on), the keyboard layout only when it is not EN.
- The recording popup says "The recording stays on this laptop." instead of "Local only: nothing leaves this laptop.", which is no longer true of transcription.
- Notification popups are shown over fullscreen windows (games); they were hidden until you left fullscreen.
- Critical notifications go away after 15 s (or the sender's own timeout) instead of staying until clicked.

### Fixed
- TV remote: a press on the touchpad now puts the TV's cursor exactly there (within 1 px) and clicks there. The TV moves its cursor by how far the finger moves, not to where it touches, so a press used to continue from wherever the cursor was left; `tvd` now drags to the top-left corner first (the cursor clamps there) and on to the target in the same gesture.
- Opening the transcript from the "Transcript ready" notification now clears the pill too (it kept offering to open the transcript). The action dismisses only that session, so a late click never clears a newer call; new IPC `rec refresh` makes the pill re-read the state.
- The clock no longer slides under the right zone when it grows into the middle (a connected Bluetooth device's name): the centre zone stays centred while it fits and otherwise sits between the side zones.
- Call recorder: the other side of a call is recorded in full again. When the call app (Google Meet in Chrome) pulled PipeWire down to short cycles, the speaker-output track kept only ~21 ms of every ~120 ms and the transcript credited the remote speaker's words, heard through the mic, to "Me". The recorder's PipeWire inputs now negotiate 32 buffers instead of 2 (`min-buffers=32` in `rec/capture.py`); under the same conditions the loss went from 3 of 6 runs to 0 of 12.

## [0.1.4] - 2026-09-23

### Added
- Volume pill in the right zone: level and mute state; click opens a mute switch and a 0–150 % slider, right click toggles mute, scroll steps by 5 %.

## [0.1.3] - 2026-09-23

### Added
- `dnd` IPC target: `qs -c kozyrek ipc call dnd toggle` flips do-not-disturb from a hotkey.

## [0.1.2] - 2026-09-23

### Added
- Keyboard layout pill (EN/RU) in the right zone; click cycles the layout.

### Fixed
- Volume keys no longer unmute: the OSD showed "unmuted" after lowering the volume while muted.

## [0.1.1] - 2026-09-21

### Fixed
- OSD (volume/brightness/keyboard) is shown over fullscreen windows; it was hidden together with the popups.

## [0.1.0] - 2026-09-07
- Initial release: bar (power, workspaces with app icons, window title, zhor,
  clock/calendar/weather, VPN, DND, wifi, bluetooth, battery, tray),
  notification daemon with popups, IPC-driven OSD.
