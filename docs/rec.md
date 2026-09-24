# Call recorder (`modules/Rec.qml` + `rec/`)

One pill records a Meet / Zoom / Telegram call; a second click stops it and
asks **"Turn recorded audio to text?"**. Yes runs a local transcription with a
progress bar in the pill; No deletes the recording. Everything stays on the
laptop.

```
~/Calls/2026-09-25_14-03-12/
  mic.opus         built-in mic after echo cancellation, 16 kHz mono Opus 32 kbps
  desktop.opus     everything the speakers play (the other side), Opus 24 kbps
  session.json     start time, duration, echo-canceller flag, clock gaps
  transcript.md    "[01:23] Me: …" / "[01:30] Speaker 1: …"
  transcript.json  words with times, speakers, languages, dropped echo words
```

Audio (and the `.transcribe/` checkpoints) is deleted 7 days after the
recording (`rec/prune.py`, `AUDIO_TTL_DAYS`); transcripts are kept. Pruning runs
on `start`, `transcribe`, `dismiss` and `discard`, so there is no timer.

## Pill

| state | pill | click |
|---|---|---|
| idle | mic glyph, dim | start recording |
| recording | blinking red dot + timer, red border (amber on a warning) | stop → ask |
| ask | `?` badge; the popup opens under the pill | reopen the popup |
| transcribing | progress fill + percent | popup: stage, elapsed, time left, Cancel |
| done | ready badge + "Transcript ready" notification | open the transcript |
| error | red | popup: Retry / Delete recording |

Enter = Yes in the ask popup. Esc or a click elsewhere only closes it: No
deletes the recording, so it is never a reflex key. Cancel during
transcription goes back to the question (audio and checkpoints kept, Yes
resumes where it stopped). Hotkey: `qs -c kozyrek ipc call rec toggle`.

Idle cost is zero: no process, no timer. The only child the bar keeps is
`kozyrec.py status --follow`, and only while something is live; it exits by
itself on idle / ask / done / error. Quickshell kills children on reload, so
the recorder and the transcriber never are the bar's children.

## Capture

No headphones, so the mic hears the speakers. Two tracks fix that:

- `desktop`: the default sink's monitor. It never contains your voice and
  follows the default sink (a Bluetooth headset connecting mid-call).
- `mic`: the internal mic (pinned by `device.form_factor=internal`) through
  PipeWire's WebRTC echo canceller (AEC3) in `monitor.mode`, with the speaker
  output as the reference. It runs as a separate `pipewire -c <runtime>/aec.conf`
  process only while recording, at `priority.session=1`, so the system default
  mic and speakers never change and the call apps keep their own audio path.

Recording is one `gst-launch-1.0 -e` with two `pipewiresrc` branches in the
transient systemd user unit `kozyrec-rec` (the canceller is `kozyrec-aec`).
The unit name is the lock; `KillSignal=SIGINT` finalizes the Ogg files;
`RuntimeMaxSec=90min` caps a recording at 1.5 h; `ExecStopPost=kozyrec.py
_finalize` writes `session.json` and the ask state even if gst dies. A
suspend of ≥ 60 s ends the recording (`/usr/local/bin/lid-delayed-suspend.sh`
suspends 30 s after the lid closes and ignores user inhibitors). Measured:
canceller ≈ 3.5 % of one core / 6 MB PSS, recorder ≈ 6 % / 11 MB, ~25 MB per
hour of audio.

Muting yourself inside Meet / Zoom / Telegram does not mute the OS mic: what
you say while "muted" is recorded. The desktop track records all system
sound, music and notifications included.

## Transcription

`kozyrec.py transcribe <dir>` forks a detached worker (`nice 19`, `ionice
-c3`) that drives NeMo-Speech.cpp in-process through its C ABI
(`rec/nemo_speech.py`, ctypes). The CLI prints no progress for a single file;
the C ABI gives exact progress (audio pushed and computed) and cancellation
between blocks.

1. **diarize** both tracks with Nemotron-3-Diarization (preset `v3-offline`).
   Desktop channels become Speaker 1..N. A mic channel that is active ≥ 85 %
   inside desktop speech and not clearly louder than the echo level is echo;
   the rest is **Me**.
2. **asr** with Nemotron 3.5 ASR 0.6B (cache-aware streaming, right context
   13 = 1.12 s chunks, language `auto` per utterance for mixed ru/en) over
   speech islands only: all desktop speech and the Me parts of the mic.
3. **merge** (`rec/merge.py`): mic words that repeat desktop words within
   ±1.5 s, or sit at the echo level, are dropped (kept in `transcript.json`
   under `dropped`), then words are grouped into turns and ordered by time.

Every stage checkpoints into `<session>/.transcribe/`, so Retry and a
cancelled run resume. Diarization and ASR are never loaded at the same time:
peak ≈ 1 GB RSS. The full-attention offline recognizer is deliberately not
used: on a 120 s island it took ~2.9 GB and pushed the laptop into swap.

Measured on the i5-5350U (turbo off, other load present): diarization RTF
≈ 0.26 per track, ASR RTF ≈ 0.9 on speech. A 1 h call therefore takes
roughly an hour to transcribe; the ETA in the popup is measured, not guessed.

## Setup (already done on the target laptop)

NeMo-Speech.cpp is built from source: Nemotron-3-Diarization support landed
after the v0.1.0 release. The system `sentencepiece` package was linked
against an older abseil and did not load, so a static one is built with the
project's own script.

```
git clone https://github.com/NVIDIA/NeMo-Speech.cpp ~/.local/src/nemo-speech
cd ~/.local/src/nemo-speech && git checkout 97a15afa5c
git submodule update --init --depth 1 ggml
CXXFLAGS="-include cstdint" CMAKE_POLICY_VERSION_MINIMUM=3.5 JOBS=3 scripts/build_sentencepiece_static.sh
cmake --preset cpu-asr -G Ninja -DNEMO_SPEECH_BUILD_MIC_CAPTURE=OFF -DNEMO_SPEECH_BUILD_TTS=OFF \
      -DCMAKE_INSTALL_PREFIX=$HOME/.local/opt/nemo-speech
cmake --build --preset cpu-asr -j3 && cmake --install build/cpu-asr --prefix $HOME/.local/opt/nemo-speech
```

Models in `~/.local/share/kozyrec/models/` (override: `KOZYREC_MODEL_DIR`; the
runtime library: `KOZYREC_NEMO_LIB`):

| file | from | size | sha256 |
|---|---|---|---|
| `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` | `nvidia/nemotron-3.5-asr-streaming-0.6b` @ `1c8deae` | 741 548 352 B | `a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae` |
| `Nemotron-3-Diarization.q8_0.gguf` | `nvidia/Nemotron-3-Diarization` @ `f667ed7` | 107 012 128 B | `08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1` |

Also needed: `gst-launch-1.0` with gst-plugins-base and gst-plugin-pipewire,
`ffmpeg`, `python3` with numpy, PipeWire's `libspa-aec-webrtc`.

Tests: `cd rec && python3 test_capture.py && python3 -m unittest`, then
`node --test test_parse.mjs` and `./test_kozyrek.sh` from the repo root.
