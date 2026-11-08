# Call recorder (`modules/Rec.qml` + `rec/`)

One pill records a Meet / Zoom / Telegram call; a second click stops it and
asks **"Turn recorded audio to text?"**. Yes runs the transcription with a
progress bar in the pill; No deletes the recording. The recording and the
transcript stay on the laptop. If a GPU host is set up and its GPU is idle,
the models run there, on a copy that only ever exists in its RAM
([Offload to a GPU host](#offload-to-a-gpu-host)); otherwise on the laptop.

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
| transcribing | progress fill + percent | popup: stage, where it runs (gpu / laptop), elapsed, time left, Cancel |
| done | ready badge + "Transcript ready" notification | open the transcript (Open in the notification does the same: the pill goes idle) |
| error | red | popup: Retry / Delete recording |

Enter = Yes in the ask popup. Esc or a click elsewhere only closes it: No
deletes the recording, so it is never a reflex key. Cancel during
transcription goes back to the question (audio and checkpoints kept, Yes
resumes where it stopped). Hotkey: `qs -c kozyrek ipc call rec toggle`.
`qs -c kozyrek ipc call rec refresh` makes the pill re-read the state; the
notification's Open calls it after `kozyrec.py dismiss <session>`, which
leaves a newer call alone if the notification is clicked late.

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
-c3`) that drives NeMo-Speech.cpp through its C ABI (`rec/nemo_speech.py`,
ctypes): in-process on the laptop, or on a GPU host when it is idle. The
CLI prints no progress for a single file; the C ABI gives exact progress
(audio pushed and computed) and cancellation between diarization blocks and
ASR islands.

1. **diarize** both tracks with Nemotron-3-Diarization (preset `v3-offline`),
   loading the model afresh per track: on CUDA, NeMo-Speech.cpp 97a15afa5c leaks
   state between streams of one model, and the second track went silent after
   its first 21 s chunk.
   Desktop channels become Speaker 1..N. A mic channel that is active ≥ 85 %
   inside desktop speech and not clearly louder than the echo level is echo;
   the rest is **Me**.
2. **asr** with Orukeet (`oruk/orukeet`, a September 2026 fine-tune of
   Parakeet TDT 0.6B v3; 25 languages with ru/en detected on its own), one
   offline full-attention pass per speech island (≤ 120 s): all desktop speech
   and the Me parts of the mic. It replaced Nemotron 3.5 ASR streaming, which
   garbled Russian calls (A/B on a 26 min call, 2026-10-06); dictation keeps
   Nemotron because it has to stream.
3. **merge** (`rec/merge.py`): mic words that repeat desktop words within
   ±1.5 s, or sit at the echo level, are dropped (kept in `transcript.json`
   under `dropped`), then words are grouped into turns and ordered by time.

Every stage checkpoints into `<session>/.transcribe/`, so Retry and a
cancelled run resume. Diarization and ASR are never loaded at the same time:
peak ≈ 1.6 GB RSS, a 120 s island through the offline recognizer.

Measured on the i5-5350U (turbo off, other load present): diarization RTF
≈ 0.26 per track, ASR RTF ≈ 0.9 on speech. A 1 h call therefore takes
roughly an hour to transcribe on the laptop; the ETA in the popup is
measured, not guessed. On an RTX 3060 host a 2:06 call took 16 s and a
21 min call 29 s, connection and busy check included.

## Phone calls (import)

Android does not let an app record the other side of a call, but Samsung's
Phone app does (Phone → ⋮ → Settings → Record calls, auto-record on). Its
files have both sides mixed digitally, with no echo:

```
kozyrec import "Call recording Name_261026_101500.m4a"
```

This makes a normal session with the file as its only track (transcoded to
`desktop.opus`) and moves the pill to "Turn recorded audio to text?", so
transcription, offload and the 7-day audio TTL work as for a laptop call. With
no mic track there is no **Me**: everyone is Speaker 1..N. The session is
named after the call's start (file mtime minus duration), so the same file
never imports twice. It works only while the pill is idle (done/error are
dismissed first). Getting the file to the laptop (e.g. Syncthing) is up to you.

## Offload to a GPU host

Offloading is off until `~/.config/kozyrek/gpu-host` exists. It holds one
line: the command that runs a shell command on the host, e.g. `ssh gpu-box`
(kozyrec appends the remote command as one more argument). Before the model
stages the worker asks the host whether its GPU is free, over one such
connection that then carries the whole job (`rec/offload.py`). Busy means: any
of five `nvidia-smi` samples 0.2 s apart at ≥ 20 % utilisation, less than
2500 MiB of free VRAM (an idle model that holds the card counts), or a running
or queued ComfyUI job. Busy, no answer within 25 s (link down), or not set up:
the laptop transcribes as before, and `.transcribe/worker.log` says why.

The host runs the laptop's own loops (`diarize_track`, `recognize_islands`),
shipped as source with every job, on a CUDA build of the same NeMo-Speech.cpp
commit with the same models. Results come back per track (diarization) and
per island (ASR) and are checkpointed on the laptop as they arrive, so a lost
link costs at most the island in flight: the worker reconnects once, then
finishes on the laptop from the checkpoints.

What the GPU host holds, and for how long:

- The Opus tracks and the results, in the RAM of one process, for the length
  of the job. Nothing is written to its disks: the audio reaches ffmpeg
  through a pipe, results leave over the SSH channel, kozyrek's code is
  imported from memory. Its stderr (NeMo's model lines, tracebacks) goes to
  the laptop's `worker.log`; sshd logs the login as for any SSH session.
- The process locks its memory (`mlockall`), so none of it goes to swap, and
  cannot dump core (`RLIMIT_CORE` 0, not dumpable).
- It exits when the job is done, when the laptop closes the channel (Cancel),
  and 60 s after the last byte from the laptop, which pings every 10 s: a
  laptop that sleeps or loses the link leaves nothing on the host for more
  than a minute.
- In transit it is SSH. There is no second layer of encryption: nothing is
  at rest there to protect, and the host has to hear the audio to transcribe
  it.

Not covered: root on the GPU host can read the process's memory while the job
runs, and ffmpeg's decode buffers (a separate process) are not locked.

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
| `orukeet-v0.1.0.q8_0.gguf` (calls) | `oruk/orukeet` @ `b59c13a`, converted (below) | 714 456 704 B | `f3b5569dc4a86deee279fa484bb6985572088beba4b78775218d658918e0b297` |
| `Nemotron-3-Diarization.q8_0.gguf` | `nvidia/Nemotron-3-Diarization` @ `f667ed7` | 107 012 128 B | `08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1` |
| `nemotron-3.5-asr-streaming-0.6b.q8_0.gguf` (dictation) | `nvidia/nemotron-3.5-asr-streaming-0.6b` @ `1c8deae` | 741 548 352 B | `a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae` |

No NeMo-Speech.cpp GGUF of Orukeet is published. Convert it in the source
checkout (`pip install -r requirements.txt` in a venv first) under its final
file name, which the GGUF stores as `general.name`, or the sha256 differs:

```
python convert_model.py oruk/orukeet --revision b59c13a733fce5cf193230d0fa317ee7f145a108 \
    --outtype q8_0 --outfile ~/.local/share/kozyrec/models/orukeet-v0.1.0.q8_0.gguf
```

or copy it from a GPU host's `/opt/kozyrec/models/orukeet-v0.1.0.q8_0.gguf`.

Also needed: `gst-launch-1.0` with gst-plugins-base and gst-plugin-pipewire,
`ffmpeg`, `python3` with numpy, PipeWire's `libspa-aec-webrtc`.

Tests: `cd rec && python3 test_capture.py && python3 -m unittest`, then
`node --test test_parse.mjs` and `./test_kozyrek.sh` from the repo root.

### GPU host

Once, from the laptop (idempotent; the build takes ~4 min), then name the
host for kozyrec:

```
ssh gpu-box bash -s < rec/gpu-setup.sh
mkdir -p ~/.config/kozyrek && echo "ssh gpu-box" > ~/.config/kozyrek/gpu-host
```

It installs under `/opt/kozyrec`: a venv with numpy, NeMo-Speech.cpp
`cuda-asr` at the laptop's commit, the diarizer from Hugging Face and Orukeet
converted from its `.nemo` (in `/opt/kozyrec/conv-venv`), both at the laptop's
revisions and sha256-checked. Nothing there ever holds call audio.
