# Changelog

## [Unreleased]

### Added
- Call recorder pill (first in the right zone): click records the call (built-in mic through an on-demand WebRTC echo canceller + the speaker output), click again stops and asks "Turn recorded audio to text?". Yes transcribes locally with NeMo-Speech.cpp (Nemotron 3.5 ASR + Nemotron-3-Diarization, ru/en auto) with a progress fill in the pill and a "Transcript ready" notification; No deletes the recording. Transcripts go to `~/Calls/<date>/transcript.md` + `.json`; audio is deleted after 7 days; recordings stop at 1.5 h. See `docs/rec.md`.
- `rec` IPC target: `qs -c kozyrek ipc call rec toggle` (bound to Super+Alt+R).
- `Pill.qml`: optional `progress` fill and `edge` border colour.
- Dictation, macOS style: tap right Cmd (`bindr = SUPER, SUPER_R`), speak Russian and/or English, and the words are pasted into the focused window as they are recognized; tap again or stay silent for 10 s to stop. A frosted capsule at the bottom shows a live waveform, the latest words and the detected language (`Dictation.qml`, `dict/kozydict.py`). The clipboard is restored afterwards. Recognition runs on the laptop's CPU with the call recorder's model, loaded per dictation and freed after it. See `docs/dict.md`.
- `dict` IPC target: `qs -c kozyrek ipc call dict toggle`.
- `rec/nemo_speech.py`: interim transcripts (`stream(interim=True)` + `.text`).

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
