# Dictation (`Dictation.qml` + `dict/`)

Tap right Cmd and talk, Russian and English mixed freely. The words are pasted
into the window that has focus while you speak. Tap again, click the capsule,
or stay silent for 10 s to stop; a dictation ends by itself after 5 min.
Everything runs on the laptop.

```
bindr = SUPER, SUPER_R, exec, qs -c kozyrek ipc call dict toggle
layerrule = blur on, ignore_alpha 0.3, no_anim on, match:namespace ^kozyrek-dict$
```

`bindr` fires on release, so right Cmd still works as a modifier: Super_R+X
does not start a dictation. The layer rule gives the capsule its frosted
blur. Both lines go in `~/.config/hypr/hyprland.conf` (`install.sh` prints
them).

## Capsule

A frosted capsule floats at the bottom centre on the Overlay layer, over
fullscreen windows too. It never takes keyboard focus, so the paste lands in
the window you were in. Only the capsule itself takes clicks.

| state | capsule |
|---|---|
| listening | mic orb whose halo follows your voice, a 22-bar waveform of the last 1.1 s, the latest words (older ones scroll off to the left), an RU / EN badge for the script of the last letter |
| warning | amber text, e.g. "Microphone is muted" |
| finishing | the orb pulses while the last words are decoded and pasted |
| error | red orb and message, hidden after 3.5 s; a notification keeps the full text |

Idle cost is zero: no process, no timer, no window.

## How it works

One `dict/kozydict.py` process per dictation, a child of the bar: a
dictation lasts seconds, and a bar reload that kills it loses one phrase.
`stop` on its stdin ends it. The helper runs three threads:

- **capture**: `pw-record` from the default source in 50 ms blocks. It sends
  the level for the waveform and handles the 10 s auto-stop.
- **recognizer**: Nemotron 3.5 ASR 0.6B, the call recorder's model, through
  `rec/nemo_speech.py` with interim results, language `auto`, 1.12 s chunks.
  The mic starts before the model loads, so nothing said meanwhile is lost.
- **Typist**: pastes the difference between the transcript and what is
  already on screen. It always types the newest transcript and skips the ones
  that came and went during a paste, so a slow paste never holds up decoding.

**Gate.** On this CPU the model runs at about 1x real time, so feeding it
long pauses would push the text further behind after every pause. Blocks
more than 10 dB above the noise floor count as speech. Speech and the 1.5 s
after it are fed to the model; the rest of a pause is skipped, and the model
catches up while you think. When the gate reopens, the 0.3 s before the
speech is fed first, so a soft onset is not clipped. The floor is the 5th
percentile of the last 5 s, but never higher than -50 dBFS. Without that cap,
someone who talks from the first second (or over loud music) would teach the
gate that speech is the floor. With it, the worst case is "no catching up",
never "words dropped".

**Stop.** The words said just before a stop sit in a chunk that is not full
yet. The helper feeds zeros until the model has heard 1.5 s of silence after
the last speech (`TAIL_S`, counting what the gate already fed), then takes the
text. It does not call the model's `finish()`: its padding decodes 2-3 more
chunks, ~2 s on this CPU. On 19 measured stops, from mid-word to 2 s after the
last word, `finish()` changed nothing in 15, added a final period in 3 and
guessed a cut-off word differently in 1. The most silence any stop needed was
1.2 s.

**Paste, not typing.** `wtype` invents a keymap per call, and
Chromium/Electron read some of its keycodes as Escape, Backspace or Enter, so
Cyrillic letters and even ASCII punctuation vanish or erase the character
before them (wtype issues #31, #71; reproduced in Brave). Each piece of text is
therefore copied with `wl-copy --type text/plain;charset=utf-8` and pasted with
Ctrl+V. Without the explicit type, a lone "!" is offered as
application/octet-stream and nothing pastes it. The helper then waits 0.15 s
for the app to read the clipboard. A revision (rare: not one in ~400 interim
results) erases with `wtype -k BackSpace`, which is safe. What the clipboard
held before the first paste is put back after the last, unless you copied
something else during the dictation.

## Measured (i5-5350U, 8 GB)

- **Memory**: ~950 MB of anonymous memory while a dictation runs, nothing
  after it. The model is not kept resident on this laptop.
- **First words**: the model reads its weights into memory on the first
  chunk. That chunk takes 2.9-5 s instead of 0.8-1.5 s, so the first words
  show up 3-5 s after the tap. The text then trails your voice by 2-4 s until
  a few pauses let the gate catch up. Over the whole 41 s take, a word reached
  the screen a median 1.1 s after it was said (p90 1.7 s).
- **Steady state**: 0.8-1.5 s of compute per 1.12 s chunk, depending on heat
  and battery.
- **Stop to done**: the 1.5 s tail costs 1-2 chunks (1-2 s) when you stop
  mid-sentence, less after a short pause, and nothing after 1.5 s of silence.
  `finish()` alone took 1.5-2.9 s.
- **Accuracy** on two clean takes: 23.4 % WER on a 41 s mixed ru/en script
  (dates, tech words), 26.1 % on 14 s of fast Russian.
- **Under load** (a cargo build plus Blender, swap full), the model falls far
  behind real time. One run showed its first text after ~100 s.

## Limits

- The last sentence can end without a period (see Stop).
- Ctrl+V has to mean paste in the focused window. It does in browsers,
  Electron, Qt/GTK apps, and in kitty through its `ctrl+v` mapping. It does
  not in vim's normal mode or in terminals that paste on Ctrl+Shift+V.
- Clipboard managers see every piece of the dictation go through the
  clipboard.
- A busy CPU makes the text lag; nothing lowers other processes' priority.

## Setup

The model and the NeMo-Speech.cpp library are the call recorder's: see
[rec.md](rec.md#setup-already-done-on-the-target-laptop). You also need
`pw-record` (PipeWire), `wl-clipboard` and `wtype`.

Tests: `cd dict && python3 -m unittest` (the gate, the edit, the Typist and
the session loop around a fake recognizer). Then run `node --test
test_parse.mjs` and `./test_kozyrek.sh` from the repo root.
