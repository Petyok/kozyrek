#!/usr/bin/env python3
"""kozydict — dictation: the mic through streaming ASR, typed into the focused window.

One process per dictation. Dictation.qml starts it on the hotkey and writes
`stop` to stdin on the next press; it types whatever is still pending and exits.
The mic starts first, so nothing said while the model loads is lost.

The recognizer is the call recorder's Nemotron streaming model (rec/) on this
CPU, in 1.12 s chunks: ~950 MB of anonymous memory, gone with the process
(this 8 GB laptop cannot keep it resident). Three threads: the capture (mic →
Gate → queue, the waveform level, auto-stop), the recognizer (the main one) and
the Typist, so a paste never holds up decoding.

stdout, one JSON object per line:
  {"ev": "state", "state": "listening" | "finishing"}
  {"ev": "level", "v": 0..1}       20 Hz, loudness above the noise floor (the waveform)
  {"ev": "text", "text": "..."}    the transcript so far; all of it is already typed
  {"ev": "warn", "msg": "..."}
  {"ev": "done", "reason": "user" | "silence" | "limit"}
  {"ev": "error", "msg": "..."}    then exit status 1
"""
import json
import math
import os
import queue
import subprocess
import sys
import threading
import time
import unicodedata
from collections import deque

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "rec"))
import nemo_speech as ns  # noqa: E402
from transcribe import ASR_MODEL, ASR_RIGHT_CONTEXT, MODEL_DIR  # noqa: E402

RATE = ns.SAMPLE_RATE
BLOCK_S = 0.05
BLOCK = int(RATE * BLOCK_S)
CAPTURE = ["pw-record", "--raw", "--format", "f32", "--rate", str(RATE), "--channels", "1",
           "--latency", "50ms", "-P", '{ media.name = "kozyrek dictation" }', "-"]
SILENCE_STOP_S = 10  # like macOS: a dictation ends by itself after this long without speech
MAX_S = 300          # one dictation at most
NO_AUDIO_S = 3       # not a single sample this long after the start: capture is broken
MUTED_S = 1          # nothing but exact zeros this long: the source is muted
# After a stop, the last words come out once the recognizer has heard this much
# silence after them: a chunk boundary can be up to 1.12 s away (see dictate()).
TAIL_S = 1.5


class Failure(Exception):
    pass


def level_db(block):
    return 20 * math.log10(max(float(np.sqrt(np.mean(block * block))), 1e-10))


class Gate:
    """Chooses the 50 ms blocks the recognizer gets.

    The ASR runs at about 1x real time on this CPU, so feeding it long pauses
    would make the text lag further behind with every pause. Speech and the
    HANG_S after it are fed, the rest of a pause is skipped, and the recognizer
    catches up while you think. HANG_S covers the model's right context (1.12 s),
    so the last words of a phrase come out during the pause rather than with
    the next phrase; reopening replays PREROLL_S so a soft onset is not clipped.

    Speech = more than MARGIN_DB above the noise floor, the FLOOR_PCT percentile
    of the last FLOOR_S of levels, capped at FLOOR_CAP_DB: a floor learnt from
    someone talking non-stop from the first second (or from loud music) would
    otherwise sit at speech level and skip the speech itself. Above the cap
    everything counts as speech, so the worst case is "no catching up", never
    "words dropped". Exact digital silence (a starting or muted source) stays out
    of the floor, or it would pin it at -200 dB. The gate starts open.
    """

    MARGIN_DB = 10
    FLOOR_CAP_DB = -50  # a quiet room on the built-in mic sits at -55..-70 dBFS
    HANG_S = 1.5
    PREROLL_S = 0.3
    FLOOR_S = 5
    FLOOR_PCT = 5

    def __init__(self, block_s=BLOCK_S):
        self.block_s = block_s
        self.levels = deque(maxlen=round(self.FLOOR_S / block_s))
        self.pre = deque(maxlen=round(self.PREROLL_S / block_s))
        self.hang_blocks = round(self.HANG_S / block_s)
        self.hang = self.hang_blocks  # blocks the gate stays open for without speech
        self.quiet_s = 0.0  # time since the last speech block (or since the start)

    def floor(self):
        return float(np.percentile(self.levels, self.FLOOR_PCT)) if self.levels else None

    @property
    def fed_quiet_s(self):
        """The silence since the last speech that was fed: the hang used so far."""
        return (self.hang_blocks - self.hang) * self.block_s

    def step(self, block, db):
        """-> (blocks to feed in order, level 0..1 for the waveform)"""
        if db > -150:
            self.levels.append(db)
        floor = self.floor()
        if floor is not None:
            floor = min(floor, self.FLOOR_CAP_DB)
        speech = floor is not None and db > floor + self.MARGIN_DB
        if speech:
            out = ([] if self.hang > 0 else list(self.pre)) + [block]
            self.pre.clear()
            self.hang = self.hang_blocks
            self.quiet_s = 0.0
        else:
            self.quiet_s += self.block_s
            if self.hang > 0:
                self.hang -= 1
                out = [block]
            else:
                self.pre.append(block)
                out = []
        level = 0.0 if floor is None else min(1.0, max(0.0, (db - floor - 4) / 30))
        return out, level


def edit(typed, text):
    """(backspaces, tail) that turn `typed`, already on screen, into `text`.

    The recognizer only appends in practice (measured: not one revision in
    ~400 interim results over mixed ru/en), so this is almost always (0, delta);
    a revision erases back to the first changed character.
    """
    keep = len(os.path.commonprefix([typed, text]))
    return len(typed) - keep, text[keep:]


def _run(args, data=b"", capture=True):
    # wl-copy forks a child that serves the clipboard and keeps inherited fds
    # open: capturing its output would wait for that child. capture=False.
    out = subprocess.PIPE if capture else subprocess.DEVNULL
    try:
        r = subprocess.run(args, input=data, stdout=out, stderr=out, timeout=10)
    except FileNotFoundError:
        raise Failure(f"{args[0]} is not installed")
    except subprocess.TimeoutExpired:
        raise Failure(f"{args[0]} hung")
    return r


def _wtype(*args):
    r = _run(["wtype", *args])
    if r.returncode:
        raise Failure("wtype failed: " + (r.stderr.decode(errors="replace").strip() or f"exit {r.returncode}"))


class Paster:
    """Puts text into the focused window the way you would: clipboard, then Ctrl+V.

    Typing it key by key with wtype does not work: wtype invents a keymap per
    call, and Chromium/Electron read some of its keycodes as Escape, Backspace
    or Enter, so Cyrillic letters and even ASCII punctuation vanish or erase the
    character before them (wtype issues #31, #71; reproduced in Brave). Ctrl+V
    pastes everywhere here, kitty included (mapped in kitty.conf). A revision's
    backspaces still go through wtype: a lone BackSpace keysym is safe.

    The clipboard is yours: what it held is saved before the first paste and
    put back after the last, unless you copied something else in between.
    """

    SETTLE_S = 0.15  # the app reads the clipboard asynchronously after Ctrl+V

    def __init__(self):
        self.saved = None  # (mime, bytes), () when it was empty; None until the first paste
        self.last = None

    def __call__(self, backspaces, text):
        if backspaces:
            _wtype(*["-k", "BackSpace"] * backspaces)
        if not text:
            return
        if self.saved is None:
            self.saved = self._take()
        data = text.encode()
        # Always typed: left to guess, wl-copy calls a lone "!" application/octet-stream,
        # and no app pastes that as text.
        if _run(["wl-copy", "--type", "text/plain;charset=utf-8"], data, capture=False).returncode:
            raise Failure("wl-copy failed")
        _wtype("-M", "ctrl", "-k", "v", "-m", "ctrl")
        self.last = data
        time.sleep(self.SETTLE_S)

    @staticmethod
    def _take():
        r = _run(["wl-paste", "--list-types"])
        types = r.stdout.decode(errors="replace").split() if r.returncode == 0 else []
        if not types:
            return ()
        # One type is all wl-copy can offer back: an image beats its text alias.
        mime = next((t for t in types if t.startswith("image/")), None) \
            or next((t for t in types if t.startswith("text/plain")), types[0])
        r = _run(["wl-paste", "--no-newline", "--type", mime])
        return (mime, r.stdout) if r.returncode == 0 else ()

    def restore(self):
        if self.saved is None:
            return
        now = _run(["wl-paste", "--no-newline"])
        if now.returncode or now.stdout != self.last:
            return  # copied something during the dictation: that wins
        if self.saved:
            mime, data = self.saved
            _run(["wl-copy", "--type", mime], data, capture=False)
        else:
            _run(["wl-copy", "--clear"], capture=False)


_out = threading.Lock()


def emit(**ev):
    line = json.dumps(ev, ensure_ascii=False)
    with _out:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def listen(proc, q, stop, info, emit):
    """Capture thread: mic blocks → Gate → queue; the level for the waveform; auto-stop.
    Always ends with pw-record gone, info["quiet_fed_s"] set and a None queued."""
    gate = Gate()
    t0 = time.monotonic()
    zeros_s, warned = 0.0, False
    try:
        while not stop.is_set():
            raw = proc.stdout.read(BLOCK * 4)
            if len(raw) < BLOCK * 4:
                if not stop.is_set():
                    info["error"] = f"microphone capture ended (pw-record exit {proc.wait()})"
                break
            block = np.frombuffer(raw, np.float32)
            info["audio"] = True
            if not warned:
                zeros_s = zeros_s + BLOCK_S if not block.any() else 0.0
                if zeros_s >= MUTED_S:
                    warned = True
                    emit(ev="warn", msg="Microphone is muted")
            feed, level = gate.step(block, level_db(block))
            for b in feed:
                q.put(b)
            emit(ev="level", v=round(level, 3))
            if gate.quiet_s >= SILENCE_STOP_S:
                info["reason"] = "silence"
                break
            if time.monotonic() - t0 >= MAX_S:
                info["reason"] = "limit"
                break
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        proc.stdout.close()
        info["quiet_fed_s"] = gate.fed_quiet_s
        emit(ev="state", state="finishing")
        q.put(None)


class Typist:
    """Types the transcript on its own thread, so decoding never waits for a paste
    (~0.2 s each with Paster.SETTLE_S). It types the newest transcript: the ones
    that came and went during a paste are skipped, not queued."""

    def __init__(self, type_keys, emit):
        self._type_keys, self._emit = type_keys, emit
        self._want = self._typed = ""
        self._error, self._closed = None, False
        self._cv = threading.Condition()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def show(self, text):
        """Hands over the transcript so far; raises the error typing ran into, if any."""
        text = unicodedata.normalize("NFC", text)
        with self._cv:
            if self._error:
                raise self._error
            self._want = text
            self._cv.notify_all()

    def _run(self):
        while True:
            with self._cv:
                self._cv.wait_for(lambda: self._closed or self._want != self._typed)
                if self._closed:
                    return
                typed, text = self._typed, self._want
            try:
                self._type_keys(*edit(typed, text))
            except Exception as e:  # a Failure, or a bug: either way finish() must not wait forever
                with self._cv:
                    self._error = e
                    self._cv.notify_all()
                return
            with self._cv:
                self._typed = text
                self._cv.notify_all()
            self._emit(ev="text", text=text)

    def finish(self):
        """Waits until the newest transcript is on screen."""
        with self._cv:
            self._cv.wait_for(lambda: self._error or self._typed == self._want)
            if self._error:
                raise self._error

    def close(self):
        """Stops typing (what is on screen stays) once the paste in flight is done."""
        with self._cv:
            self._closed = True
            self._cv.notify_all()
        self._thread.join()


def open_model():
    model = MODEL_DIR / ASR_MODEL["file"]
    if not model.exists():
        raise Failure(f"ASR model not found: {model}")
    try:
        lib = ns.Lib()
    except FileNotFoundError as e:
        raise Failure(str(e))
    return ns.Recognizer(lib, model, ASR_RIGHT_CONTEXT)


def dictate(stop, type_keys, capture=CAPTURE, emit=emit):
    """One dictation, until `stop` is set, a long silence or the time cap. Returns the stop reason.
    type_keys(backspaces, text) edits the focused window; it runs on the Typist's thread."""
    q = queue.Queue()
    info = {"reason": "user", "error": None, "audio": False, "quiet_fed_s": 0.0}
    try:
        proc = subprocess.Popen(capture, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE)
    except FileNotFoundError:
        raise Failure(f"{capture[0]} is not installed")
    listener = threading.Thread(target=listen, args=(proc, q, stop, info, emit), daemon=True)
    listener.start()
    emit(ev="state", state="listening")
    typist = Typist(type_keys, emit)
    try:
        with open_model() as rec, rec.stream("auto", interim=True) as asr:
            t0, ended = time.monotonic(), False
            while not ended:
                try:
                    block = q.get(timeout=0.2)
                except queue.Empty:
                    if not info["audio"] and time.monotonic() - t0 > NO_AUDIO_S:
                        raise Failure("no audio from the microphone")
                    continue
                blocks = []
                while block is not None:  # plus everything that queued up while the last chunk decoded
                    blocks.append(block)
                    try:
                        block = q.get_nowait()
                    except queue.Empty:
                        break
                ended = block is None
                if blocks:
                    asr.push(np.concatenate(blocks))
                typist.show(asr.text)
            # The words said just before the stop sit in a chunk that is not full yet.
            # The Gate's hang already fed some silence after them; zeros make up the rest
            # of TAIL_S (the most any of 12 measured stops needed: 1.2 s). No finish():
            # its padding decodes 2-3 more chunks, ~2 s here, and on 19 stops, from
            # mid-word to 2 s after the last word, it changed nothing in 15, added a
            # final period in 3 and guessed a cut-off word differently in 1.
            pad = round(max(0.0, TAIL_S - info["quiet_fed_s"]) * RATE)
            if pad:
                asr.push(np.zeros(pad, np.float32))
            typist.show(asr.text)
        typist.finish()
    finally:
        stop.set()
        typist.close()
        listener.join(timeout=3)
    if info["error"]:
        raise Failure(info["error"])
    return info["reason"]


def read_commands(stop):
    for line in sys.stdin:
        if line.strip() == "stop":
            break
    stop.set()  # "stop", or the bar closed our stdin


def main():
    stop = threading.Event()
    threading.Thread(target=read_commands, args=(stop,), daemon=True).start()
    paster = Paster()
    try:
        reason = dictate(stop, paster)
    except (Failure, ns.NemoError) as e:
        emit(ev="error", msg=str(e))
        return 1
    finally:
        try:
            paster.restore()
        except Failure as e:
            print("clipboard not restored:", e, file=sys.stderr)
    emit(ev="done", reason=reason)
    return 0


if __name__ == "__main__":
    sys.exit(main())
