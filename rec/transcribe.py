"""kozyrec transcription: detached worker, stages, progress/ETA, cancel, resume.

    transcribe <dir>   preflight, fork a low-priority worker, state → transcribing
    cancel             stop the worker, state → ask (audio and checkpoints kept)
    _worker <dir>      the pipeline (never run by hand)

Pipeline over the session's two tracks (mic = the user + residual echo,
desktop = remote participants only):

    prepare → diarize (both tracks, Nemotron-3-Diarization) → plan (mic echo
    channels, ASR islands) → asr (nemotron-3.5, speech islands only) → merge

Every stage checkpoints into <session>/.transcribe/ (atomic writes), so a
cancelled or failed run resumes at the first unfinished island.

The two model stages run on an engine: the GPU host when it is idle
(offload.py), else the laptop. Both run the same loops (diarize_track,
recognize_islands) and the laptop commits every result as it arrives, so a
lost host hands over to the laptop at the island it stopped at.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path

import numpy as np

import merge
import nemo_speech as ns
import offload
import state

KOZYREC = Path(__file__).resolve().with_name("kozyrec.py")
MODEL_DIR = Path(os.environ.get("KOZYREC_MODEL_DIR", Path.home() / ".local/share/kozyrec/models"))
# Orukeet (Parakeet TDT 0.6B v3 fine-tune, 25 languages, oruk/orukeet @ b59c13a),
# converted with NeMo-Speech.cpp's convert_model.py (q8_0). Offline only: one
# full-attention pass per island. On this repo's calls it beat Nemotron 3.5
# streaming by a wide margin (2026-10-06 A/B).
ASR_MODEL = {
    "file": "orukeet-v0.1.0.q8_0.gguf",
    "revision": "b59c13a733fce5cf193230d0fa317ee7f145a108",
    "sha256": "f3b5569dc4a86deee279fa484bb6985572088beba4b78775218d658918e0b297",
}
DIAR_MODEL = {
    "file": "Nemotron-3-Diarization.q8_0.gguf",
    "revision": "f667ed73aee57d40cc39428eb768b4fd87a0a29e",
    "sha256": "08456d9e22cd9a323c0364d98375f3746d6e68507ebb705cd46438c534c7a3a1",
}
DIAR_PRESET = "v3-offline"
ISLAND_MAX_S = 120.0
TRACKS = ("desktop", "mic")
SR = ns.SAMPLE_RATE
DIAR_BLOCK = 2 * SR  # 2.0 s
ENV_HOP = int(merge.FRAME_S * SR)
MIN_FREE_BYTES = 200 * 1024 * 1024

STAGES = ("prepare", "diarize", "asr", "merge")
PERF = os.path.join(state.STATE_DIR, "perf.json")
# Measured on the laptop under load (lead's benchmark, 2026-09-25); perf.json refines them.
# gpu_*: an RTX 3060 host and a ~1 MB/s upload, measured 2026-09-28.
PERF_DEFAULTS = {"rtf_diar": 0.26, "rtf_asr": 0.9, "load_diar_s": 2.0, "load_asr_s": 8.0,
                 "gpu_rtf_diar": 0.004, "gpu_rtf_asr": 0.015, "gpu_load_diar_s": 3.0,
                 "gpu_load_asr_s": 1.0, "gpu_up_s_per_mb": 1.0}
ETA_MIN_MEASURED_S = 20.0


class Cancelled(Exception):
    pass


class Failure(Exception):
    """A failure with a short user-facing message."""

    def __init__(self, msg, retry=True):
        super().__init__(msg)
        self.msg, self.retry = msg, retry


_cancel = threading.Event()


def _check_cancel():
    if _cancel.is_set():
        raise Cancelled()


# ---- audio ---------------------------------------------------------------------------

FFMPEG_SIGNAL_EXIT = 255  # ffmpeg's exit code after a trapped termination signal


class TrackReader:
    """Sequential 16 kHz mono float32 decode of one track through an ffmpeg pipe.
    `data` (the file's bytes) is fed on ffmpeg's stdin instead of opening `path`:
    the offload host never writes the audio to its disk."""

    def __init__(self, path=None, data=None):
        self.path = path if data is None else "<memory>"
        self.pos = 0  # samples delivered so far
        # stderr goes to a file, not a pipe: a chatty decoder must never block on
        # a full stderr pipe while we only drain stdout.
        self.err = tempfile.TemporaryFile()
        self.proc = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-v", "error", "-i", "pipe:0" if data is not None else str(path),
             "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"],
            stdin=subprocess.DEVNULL if data is None else subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.err,
        )
        self._feeder = None
        if data is not None:
            self._feeder = threading.Thread(target=self._feed, args=(data,), daemon=True)
            self._feeder.start()

    def _feed(self, data):
        # A reader closed before the end of the track kills ffmpeg: that pipe error is expected.
        try:
            self.proc.stdin.write(data)
        except OSError:
            pass
        try:
            self.proc.stdin.close()
        except OSError:
            pass

    def read(self, n):
        """Up to n samples; a short (or empty) block means the track ended. The
        end is only accepted when ffmpeg exited cleanly: a killed decoder (cancel's
        SIGTERM hits the whole process group) must not look like the track's end,
        or a truncated checkpoint would be committed."""
        want = n * 4
        buf = bytearray()
        while len(buf) < want:
            chunk = self.proc.stdout.read(want - len(buf))
            if not chunk:
                break
            buf += chunk
        if len(buf) < want:
            self._check_exit()
        usable = len(buf) - len(buf) % 4
        block = np.frombuffer(bytes(buf[:usable]), dtype=np.float32)
        self.pos += block.size
        return block

    def _check_exit(self):
        rc = self.proc.wait()
        self.err.seek(0)
        err = self.err.read()[-2000:].decode("utf-8", "replace").strip().splitlines()
        if rc == 0:
            return
        # ffmpeg traps SIGTERM/SIGINT/SIGHUP and exits 255 (never used for errors);
        # an untrappable kill (OOM killer) shows up as a negative code.
        if _cancel.is_set() or rc in (FFMPEG_SIGNAL_EXIT, -signal.SIGTERM):
            # The worker's own handler may not have run yet: the group got the signal too.
            _cancel.set()
            raise Cancelled()
        cause = RuntimeError(f"{self.path}: ffmpeg rc={rc}: {err[-1] if err else ''}")
        if rc < 0:
            raise Failure("audio decoder died", retry=True) from cause
        raise Failure("audio unreadable", retry=False) from cause

    def skip_to(self, sample):
        while self.pos < sample:
            if self.read(min(DIAR_BLOCK, sample - self.pos)).size == 0:
                return

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()
        if self._feeder:
            self._feeder.join()
        self.proc.stdout.close()
        self.err.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def probe_duration(path):
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True, text=True, timeout=30,
        ).stdout.strip()
        return float(out)
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


def envelope_db(block):
    n = block.size // ENV_HOP * ENV_HOP
    if n == 0:
        return np.empty(0, dtype=np.float32)
    frames = block[:n].reshape(-1, ENV_HOP).astype(np.float64)
    return (10.0 * np.log10(np.mean(frames * frames, axis=1) + 1e-10)).astype(np.float32)


def shift_envelope(env, offset_s):
    """Track-frame envelope → call-frame envelope (track t0 = call offset_s)."""
    k = int(round(offset_s / merge.FRAME_S))
    if k > 0:
        return np.concatenate([np.full(k, np.nan, dtype=env.dtype), env])
    return env[-k:] if k < 0 else env


# ---- model loops (the laptop and the offload host run these same two) --------------


def diarize_track(diar, rd, on_progress):
    """One track through a diarizer: ([(s, e, speaker)] in track time, duration_s,
    per-frame dB envelope). on_progress(labelled_s) after every block."""
    env_parts = []
    with diar.stream() as st:
        while True:
            block = rd.read(DIAR_BLOCK)
            if block.size == 0:
                break
            env_parts.append(envelope_db(block))
            st.push(block)
            on_progress(st.labelled_seconds())
            _check_cancel()
        _check_cancel()  # never commit a checkpoint of a cancelled pass
        st.finish()
        segs = st.segments()
    env = np.concatenate(env_parts) if env_parts else np.empty(0, dtype=np.float32)
    return segs, rd.pos / SR, env


def recognize_islands(rec, rd, off, islands, on_progress, on_island):
    """ASR over the call-time islands of one track (track t0 = call `off`), one
    offline pass per island. on_progress(seconds into the current island);
    on_island(record, island_len) once an island is complete, the only point a
    result may be committed."""
    for s, e in islands:
        _check_cancel()
        start = max(0.0, s - off)
        rd.skip_to(int(start * SR))
        begin_s = rd.pos / SR + off
        block = rd.read(int(max(0.0, e - off - rd.pos / SR) * SR))
        words, langs = rec.recognize(block) if block.size else ([], [])
        on_progress(block.size / SR)
        _check_cancel()
        on_island({
            "key": _island_key((s, e)), "s": round(begin_s, 3), "e": round(e, 3),
            "lang": langs, "words": [[w, round(a, 3), round(b, 3)] for w, a, b in words],
        }, e - s)


class LocalEngine:
    """The laptop's CPU: the fallback, and the only engine with offloading off."""

    where = "laptop"
    perf = ""  # perf.json key prefix

    def __init__(self, lib):
        self.lib = lib

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def upload_mb(self, names, tracks):
        return 0.0

    def upload(self, names, tracks):
        pass

    def diarizer(self):
        return ns.Diarizer(self.lib, MODEL_DIR / DIAR_MODEL["file"], DIAR_PRESET)

    def recognizer(self):
        return ns.Recognizer(self.lib, MODEL_DIR / ASR_MODEL["file"], None)

    def diarize(self, diar, name, info, on_progress):
        with TrackReader(info["path"]) as rd:
            return diarize_track(diar, rd, on_progress)

    def recognize(self, rec, name, info, islands, on_progress, on_island):
        with TrackReader(info["path"]) as rd:
            recognize_islands(rec, rd, info["offset"], islands, on_progress, on_island)


# ---- checkpoints -------------------------------------------------------------------------


class Checkpoints:
    def __init__(self, session):
        self.dir = Path(session) / ".transcribe"
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        return self.dir / name

    def load_json(self, name):
        return state.read_json(str(self.path(name)))

    def save_json(self, name, obj):
        state.write_json(str(self.path(name)), obj)

    def save_array(self, name, arr):
        tmp = self.path(name + ".tmp")
        with open(tmp, "wb") as f:
            np.save(f, arr)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path(name))

    def load_array(self, name):
        try:
            return np.load(self.path(name)).astype(np.float32)
        except (OSError, ValueError):
            return None

    def asr_records(self, track):
        """Completed island records; a torn last line (killed mid-write) is ignored."""
        out = []
        try:
            with open(self.path(f"asr_{track}.jsonl"), encoding="utf-8") as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except json.JSONDecodeError:
                        break
        except FileNotFoundError:
            pass
        return out

    def repair_asr(self, track):
        """Rewrite the island log without a torn last line (worker killed mid-write)."""
        path = self.path(f"asr_{track}.jsonl")
        if not path.exists():
            return
        good = self.asr_records(track)
        with open(path, encoding="utf-8") as f:
            if sum(1 for _ in f) == len(good):
                return
        tmp = self.path(f"asr_{track}.jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in good)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def append_asr(self, track, record):
        with open(self.path(f"asr_{track}.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())


# ---- progress --------------------------------------------------------------------------


def load_perf():
    """Measured speeds (EMA over runs); defaults until the first run."""
    perf = dict(PERF_DEFAULTS)
    saved = state.read_json(PERF) or {}
    perf.update({k: float(v) for k, v in saved.items() if k in PERF_DEFAULTS and v})
    perf["_measured"] = [k for k in saved if k in PERF_DEFAULTS]
    return perf


def save_perf(perf, key, measured, alpha=0.3):
    if measured is None or measured <= 0:
        return
    first = key not in perf["_measured"]
    perf[key] = measured if first else perf[key] * (1 - alpha) + measured * alpha
    if first:
        perf["_measured"].append(key)
    state.write_json(PERF, {k: round(perf[k], 4) for k in perf["_measured"]})


class Progress:
    """One overall monotonic bar over weighted stages, written to state.PROGRESS
    at most once a second (heartbeat every 3 s even when nothing moved)."""

    def __init__(self, session):
        self.session = session
        self.lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.costs = {s: 1.0 for s in STAGES}
        self.stage, self.track, self.host = "prepare", None, "laptop"
        self.frac, self.base, self.scale = 0.0, 0.0, 1.0 / len(STAGES)
        self.stage_frac, self.audio_s, self.audio_total = 0.0, 0.0, 0.0
        self.stage_rtf_assumed = None
        self.measure_t0 = self.measure_audio0 = None
        self.measured_s = 0.0
        self.eta, self.eta_t = None, 0.0
        self.dirty, self.last_write = True, 0.0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def set_costs(self, **costs):
        with self.lock:
            self.costs.update(costs)

    def set_host(self, host):
        """Where the models run now: the popup shows it."""
        with self.lock:
            self.host, self.dirty = host, True

    def begin(self, stage, audio_total=0.0, rtf=None):
        with self.lock:
            rest = sum(self.costs[s] for s in STAGES[STAGES.index(stage):])
            self.base = self.frac
            self.scale = (1.0 - self.base) / rest if rest > 0 else 0.0
            self.stage, self.track = stage, None
            self.stage_frac, self.audio_s, self.audio_total = 0.0, 0.0, float(audio_total)
            self.stage_rtf_assumed = rtf
            self.measure_t0 = self.measure_audio0 = None
            self.dirty = True

    def advance(self, audio_s, track=None):
        now = time.monotonic()
        with self.lock:
            self.track = track or self.track
            self.audio_s = min(audio_s, self.audio_total) if self.audio_total else audio_s
            if self.measure_t0 is None:
                self.measure_t0, self.measure_audio0 = now, self.audio_s
            sf = self.audio_s / self.audio_total if self.audio_total > 0 else 0.0
            self.stage_frac = max(self.stage_frac, min(1.0, sf))
            self.frac = max(self.frac, self.base + self.scale * self.costs[self.stage] * self.stage_frac)
            self._update_eta(now)
            self.dirty = True

    def end(self):
        with self.lock:
            self.stage_frac = 1.0
            self.frac = max(self.frac, min(1.0, self.base + self.scale * self.costs[self.stage]))
            self.dirty = True

    def mark(self, audio_s):
        """Start measuring throughput now (after a model load) at audio_s."""
        with self.lock:
            self.measure_t0, self.measure_audio0 = time.monotonic(), audio_s

    def measured_rtf(self):
        with self.lock:
            done = self.audio_s - (self.measure_audio0 or 0.0)
            if self.measure_t0 is None or done <= 0:
                return None
            return (time.monotonic() - self.measure_t0) / done

    def _update_eta(self, now):
        elapsed = now - self.measure_t0
        done = self.audio_s - self.measure_audio0
        if elapsed + self.measured_s < ETA_MIN_MEASURED_S or done <= 0:
            return
        rtf = elapsed / done
        factor = 1.0
        if self.stage_rtf_assumed:
            factor = min(2.0, max(0.5, rtf / self.stage_rtf_assumed))
        future = sum(self.costs[s] for s in STAGES[STAGES.index(self.stage) + 1:])
        raw = (self.audio_total - self.audio_s) * rtf + future * factor
        cur = self._eta_now(now)
        if cur is None or raw > cur * 1.1:
            new = raw
        else:
            new = min(cur, 0.3 * raw + 0.7 * cur)
        self.eta, self.eta_t = new, now

    def _eta_now(self, now):
        return None if self.eta is None else max(0.0, self.eta - (now - self.eta_t))

    def close_measure(self):
        """Keep measured time across stages (eta needs >= 20 s measured overall)."""
        with self.lock:
            if self.measure_t0 is not None:
                self.measured_s += time.monotonic() - self.measure_t0

    def snapshot(self):
        with self.lock:
            eta = self._eta_now(time.monotonic())
            return {
                "ev": "progress", "session": self.session, "stage": self.stage,
                "frac": round(self.frac, 4), "stage_frac": round(self.stage_frac, 4),
                "eta_s": None if eta is None else int(round(eta)),
                "audio_s": round(self.audio_s, 1), "audio_total_s": round(self.audio_total, 1),
                "track": self.track, "host": self.host, "at": time.time(),
            }

    def _run(self):
        while not self._stop.wait(0.25):
            try:
                self.flush()
            except OSError as exc:  # e.g. disk full: the bar just freezes, the job goes on
                print(f"progress write failed: {exc}", file=sys.stderr, flush=True)

    def flush(self, force=False):
        # One writer at a time: the heartbeat thread and the worker's final flush
        # share state.write_json's per-pid tmp name, so unserialised writes race
        # (one rename finds the tmp already gone).
        with self._write_lock:
            now = time.monotonic()
            if force or (self.dirty and now - self.last_write >= 1.0) or now - self.last_write >= 3.0:
                snap = self.snapshot()
                self.dirty, self.last_write = False, now
                state.write_json(state.PROGRESS, snap)

    def stop(self):
        self._stop.set()
        self._thread.join()


def clear_progress(session):
    cur = state.read_json(state.PROGRESS)
    if isinstance(cur, dict) and cur.get("session") == session:
        try:
            os.unlink(state.PROGRESS)
        except FileNotFoundError:
            pass


# ---- pipeline -------------------------------------------------------------------------


def _session_meta(session):
    meta = state.read_json(os.path.join(session, "session.json"), {}) or {}
    tracks = {}
    for name in TRACKS:
        info = (meta.get("tracks") or {}).get(name) or {}
        path = Path(session) / (info.get("file") or f"{name}.opus")
        if path.exists() and path.stat().st_size > 0:
            tracks[name] = {"path": path, "offset": float(info.get("start_offset_s") or 0.0)}
    return meta, tracks


def _diarize(engine, ck, tracks, est, prog, perf):
    p = engine.perf
    todo = [t for t in TRACKS if t in tracks and ck.load_json(f"diar_{t}.json") is None]
    total = sum(est[t] for t in tracks)
    prog.begin("diarize", total, perf[p + "rtf_diar"])
    done_s = sum(est[t] for t in tracks if t not in todo)
    for name in todo:
        _check_cancel()
        # A fresh model per track: on CUDA, NeMo-Speech.cpp 97a15afa5c leaks state
        # between streams of one model, and the next track goes silent after 21 s.
        t_load = time.monotonic()
        with engine.diarizer() as diar:
            save_perf(perf, p + "load_diar_s", time.monotonic() - t_load)
            prog.mark(done_s)
            segs, duration, env = engine.diarize(
                diar, name, tracks[name], lambda s: prog.advance(done_s + s, name))
        prog.close_measure()
        off = tracks[name]["offset"]
        segs = [[round(s + off, 3), round(e + off, 3), k] for s, e, k in segs]
        ck.save_array(f"env_{name}.npy", shift_envelope(env, off).astype(np.float16))
        ck.save_json(f"diar_{name}.json", {"segments": segs, "duration_s": round(duration, 3),
                                           "offset_s": off})
        done_s += est[name]
        prog.advance(done_s, name)
        save_perf(perf, p + "rtf_diar", prog.measured_rtf())
    prog.end()


def _plan(ck, meta, tracks):
    diar = {t: ck.load_json(f"diar_{t}.json") for t in tracks}
    segs = {t: [tuple(s) for s in diar[t]["segments"]] for t in tracks}
    env = {t: ck.load_array(f"env_{t}.npy") for t in tracks}
    cuts = [float(g["at_s"]) for g in meta.get("gaps") or [] if "at_s" in g]
    desk = segs.get("desktop", [])
    cls = {"me": [], "echo": [], "erl_db": merge.DEFAULT_ERL_DB, "tau_s": merge.DEFAULT_TAU_S, "channels": {}}
    if "mic" in tracks:
        cls = merge.classify_mic_channels(segs["mic"], desk, env["mic"], env.get("desktop"),
                                          aec=bool(meta.get("aec", True)))
    islands = {}
    for t in tracks:
        chosen = segs[t] if t == "desktop" else [x for x in segs[t] if x[2] in cls["me"]]
        total = diar[t]["duration_s"] + diar[t]["offset_s"]
        islands[t] = merge.build_islands([(s, e) for s, e, _ in chosen], total, cuts,
                                         max_len=ISLAND_MAX_S)
    plan = {"classification": cls, "islands": islands, "cuts": cuts}
    ck.save_json("plan.json", plan)
    return plan, segs, env


def _island_key(isl):
    return f"{isl[0]:.3f}-{isl[1]:.3f}"


def _asr_todo(ck, tracks, plan):
    todo = {}
    for t in tracks:
        ck.repair_asr(t)
        done = {r["key"] for r in ck.asr_records(t)}
        todo[t] = [isl for isl in plan["islands"][t] if _island_key(isl) not in done]
    return todo


def _asr(engine, ck, tracks, plan, prog, perf):
    p = engine.perf
    todo = _asr_todo(ck, tracks, plan)
    total = sum(e - s for t in tracks for s, e in plan["islands"][t])
    remaining = sum(e - s for t in todo for s, e in todo[t])
    prog.begin("asr", total, perf[p + "rtf_asr"])
    done_s = total - remaining
    if remaining > 0:
        t_load = time.monotonic()
        with engine.recognizer() as rec:
            save_perf(perf, p + "load_asr_s", time.monotonic() - t_load)
            prog.mark(done_s)
            for t in TRACKS:
                if not todo.get(t):
                    continue

                def island(record, length, t=t):
                    nonlocal done_s
                    ck.append_asr(t, record)
                    done_s += length
                    prog.advance(done_s, t)

                engine.recognize(rec, t, tracks[t], todo[t],
                                 lambda x, t=t: prog.advance(done_s + x, t), island)
        prog.close_measure()
        save_perf(perf, p + "rtf_asr", prog.measured_rtf())
    prog.end()


def _pending(ck, meta, tracks):
    """Tracks a model still has to read: all of them until every track is
    diarized (the plan needs them all), then those with an island left."""
    if any(ck.load_json(f"diar_{t}.json") is None for t in tracks):
        return [t for t in TRACKS if t in tracks]
    plan, _, _ = _plan(ck, meta, tracks)
    todo = _asr_todo(ck, tracks, plan)
    return [t for t in TRACKS if todo.get(t)]


def _engines(lib, ck, meta, tracks):
    """The GPU host while it is idle and takes the job (a lost link gets
    offload.TRIES - 1 reconnects), then the laptop, which always finishes."""
    for _ in range(offload.TRIES):
        if not _pending(ck, meta, tracks):
            break
        remote = offload.connect()
        if remote is None:
            break
        yield remote
    yield LocalEngine(lib)


def _model_stages(engine, ck, meta, tracks, est, prog, perf):
    p = engine.perf
    audio = sum(est.values())
    asr_guess = 0.55 * est.get("desktop", 0.0) + 0.35 * est.get("mic", 0.0)
    pending = _pending(ck, meta, tracks)
    up_mb = engine.upload_mb(pending, tracks)
    prog.set_host(engine.where)
    prog.set_costs(prepare=3.0 + perf["gpu_up_s_per_mb"] * up_mb,
                   diarize=len(tracks) * perf[p + "load_diar_s"] + perf[p + "rtf_diar"] * audio,
                   asr=perf[p + "load_asr_s"] + perf[p + "rtf_asr"] * asr_guess,
                   merge=2.0 + audio / 3600.0)
    prog.begin("prepare")
    t_up = time.monotonic()
    engine.upload(pending, tracks)
    if up_mb >= 1.0:  # smaller uploads measure the round trip, not the link
        save_perf(perf, "gpu_up_s_per_mb", (time.monotonic() - t_up) / up_mb)
    prog.end()

    _diarize(engine, ck, tracks, est, prog, perf)
    _check_cancel()
    plan, segs, env = _plan(ck, meta, tracks)
    asr_s = sum(e - s for t in tracks for s, e in plan["islands"][t])
    prog.set_costs(asr=perf[p + "load_asr_s"] + perf[p + "rtf_asr"] * asr_s)
    _asr(engine, ck, tracks, plan, prog, perf)
    _check_cancel()
    return plan, segs, env


def _merge(session, ck, meta, tracks, plan, segs, env, engine, wall_s):
    cls, cuts = plan["classification"], plan["cuts"]
    desk_words = [w for r in ck.asr_records("desktop") for w in merge.island_words(r)] \
        if "desktop" in tracks else []
    mic_words = [w for r in ck.asr_records("mic") for w in merge.island_words(r)] \
        if "mic" in tracks else []
    desk_segs = segs.get("desktop", [])
    for w, k in zip(desk_words, merge.assign_speakers(desk_words, desk_segs)):
        w["spk"] = k
    merge.fold_minor_speakers(desk_words)
    labels = merge.speaker_labels(desk_words)
    for w in desk_words:
        w["spk"] = labels[w["spk"]]
    kept, dropped = merge.echo_filter(
        mic_words, desk_words, desk_segs, env.get("mic"), env.get("desktop"), cls["erl_db"], cls["tau_s"])
    for w in kept:
        w["spk"] = merge.ME
    turns = merge.build_turns(desk_words + kept, cuts)
    durations = [ck.load_json(f"diar_{t}.json")["duration_s"] + tracks[t]["offset"] for t in tracks]
    meta = dict(meta, duration_s=max(durations or [float(meta.get("duration_s") or 0.0)]))
    stats = {
        "echo_dropped_words": len(dropped),
        "mic_me_channels": cls["me"], "mic_echo_channels": cls["echo"],
        "mic_all_echo": bool(cls["echo"]) and not cls["me"],
        "mic_channels": cls["channels"],
        "erl_db": round(cls["erl_db"], 1), "tau_s": round(cls["tau_s"], 3),
        "wall_s": round(wall_s, 1),
    }
    doc = merge.build_document(session, meta, turns, {}, dropped, stats, engine)
    json_path, md_path = Path(session) / "transcript.json", Path(session) / "transcript.md"
    state.write_json(str(json_path), doc)
    tmp = md_path.with_name(".transcript.md.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(merge.render_markdown(doc))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, md_path)
    return doc, md_path, json_path


def run_pipeline(session, prog):
    t_start = time.monotonic()
    perf = load_perf()
    meta, tracks = _session_meta(session)
    if not tracks:
        raise Failure("audio unreadable", retry=False)
    for m in (ASR_MODEL, DIAR_MODEL):
        if not (MODEL_DIR / m["file"]).exists():
            raise Failure("models missing")
    try:
        lib = ns.Lib()
    except (FileNotFoundError, OSError) as e:
        raise Failure("runtime missing (libnemo_speech_asr_c.so)") from e
    ck = Checkpoints(session)

    est = {}
    for t, info in tracks.items():
        done = ck.load_json(f"diar_{t}.json")
        est[t] = done["duration_s"] if done else (
            probe_duration(info["path"]) or float(meta.get("duration_s") or 0.0) or 60.0)

    hosts = []
    for eng in _engines(lib, ck, meta, tracks):
        if eng.where not in hosts:
            hosts.append(eng.where)
        try:
            with eng:
                plan, segs, env = _model_stages(eng, ck, meta, tracks, est, prog, perf)
            break
        except offload.Lost as exc:  # only a remote engine loses its link
            print(f"worker: {eng.where} lost: {exc}; the next engine resumes", file=sys.stderr, flush=True)

    prog.begin("merge", 1.0)
    engine = {
        "nemo_speech": lib.version(),
        "asr": dict(ASR_MODEL, mode="offline"),
        "diar": dict(DIAR_MODEL, preset=DIAR_PRESET),
        "hosts": hosts,
    }
    doc, md, js = _merge(session, ck, meta, tracks, plan, segs, env, engine, time.monotonic() - t_start)
    prog.advance(1.0)
    prog.end()
    return doc, md, js


# ---- verbs ---------------------------------------------------------------------------


def _alive(pid):
    try:
        os.kill(pid, 0)
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"_worker" in f.read()
    except (OSError, TypeError):
        return False


def _lower_priority():
    # nice 19 (+ ionice -c3 around the exec), not SCHED_IDLE: under any other
    # CPU load SCHED_IDLE starves the worker completely and the bar freezes.
    os.nice(19)


def _preflight(session):
    """Refusals that should not even start a worker: (msg, retry) or None."""
    _, tracks = _session_meta(session)
    if not tracks:
        return "audio unreadable", False
    if any(not (MODEL_DIR / m["file"]).exists() for m in (ASR_MODEL, DIAR_MODEL)):
        return "models missing", True
    if ns.find_library() is None:
        return "runtime missing (libnemo_speech_asr_c.so)", True
    if shutil.disk_usage(session).free < MIN_FREE_BYTES:
        return "low disk", True
    return None


def cmd_transcribe(session_dir):
    session = os.path.abspath(os.path.expanduser(session_dir))
    cur = state.read()
    here = cur.get("session") == session
    if cur.get("state") == "transcribing" and here and _alive(cur.get("worker_pid")):
        state.emit(cur)
        return 0
    # A transcribing record whose worker is gone is a failed run: restart it.
    allowed = ("ask", "error", "transcribing")
    if cur.get("state") not in allowed or not here:
        state.emit(state.error_line(f"cannot transcribe while {cur.get('state')}", session=session,
                                    retry=False, during="transcribe"))
        return 1
    refused = _preflight(session)
    if refused:
        msg, retry = refused
        rec = state.set("error", session, expect=allowed, msg=msg, retry=retry, during="transcribe")
        state.emit(rec or state.read())
        return 1

    tdir = Path(session) / ".transcribe"
    tdir.mkdir(exist_ok=True)
    cmd = [sys.executable, str(KOZYREC), "_worker", session]
    if shutil.which("ionice"):
        cmd = ["ionice", "-c3"] + cmd
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = ":".join(p for p in (str(ns.LIB_DIR), env.get("LD_LIBRARY_PATH")) if p)
    env["OMP_WAIT_POLICY"] = "PASSIVE"  # idle OpenMP threads sleep instead of spinning
    with open(tdir / "worker.log", "ab") as log:
        log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} transcribe\n".encode())
        log.flush()
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=log,
                                start_new_session=True, preexec_fn=_lower_priority, env=env,
                                close_fds=True)
    rec = state.set("transcribing", session, expect=allowed, since=time.time(), worker_pid=proc.pid)
    if rec is None:
        os.killpg(proc.pid, signal.SIGKILL)
        state.emit(state.read())
        return 1
    state.emit(rec)
    return 0


def _stop_worker(pid, grace=5.0):
    if not _alive(pid):
        return
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 2.0)):
        try:
            os.killpg(pid, sig)
        except ProcessLookupError:
            return
        except PermissionError:
            os.kill(pid, sig)
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if not _alive(pid):
                return
            time.sleep(0.05)


def cmd_cancel():
    cur = state.read()
    if cur.get("state") != "transcribing":
        state.emit(cur)
        return 0
    session = cur["session"]
    state.update("transcribing", session, cancel_at=time.time())
    _stop_worker(cur.get("worker_pid"))
    rec = state.set("ask", session, expect="transcribing", **state.ask_fields(session))
    clear_progress(session)
    state.emit(rec or state.read())
    return 0


def _classify(exc):
    if isinstance(exc, Failure):
        return exc.msg, exc.retry
    if isinstance(exc, MemoryError) or (isinstance(exc, ns.NemoError) and exc.status == ns.OUT_OF_MEMORY):
        return "out of memory", True
    if isinstance(exc, ns.NemoError):
        first = (str(exc).splitlines() or ["runtime error"])[0]
        return f"nemo: {first}"[:120], True
    if isinstance(exc, OSError) and exc.errno == 28:
        return "low disk", True
    return f"{type(exc).__name__}: {exc}"[:120], True


def worker_main(session_dir):
    session = os.path.abspath(session_dir)
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: _cancel.set())
    # The parent records our pid right after fork; do nothing until it has.
    deadline = time.monotonic() + 10
    while True:
        cur = state.read()
        if cur.get("state") == "transcribing" and cur.get("session") == session \
                and cur.get("worker_pid") == os.getpid():
            break
        if time.monotonic() > deadline or _cancel.is_set():
            return 1
        time.sleep(0.1)

    print(f"worker {os.getpid()} start {session}", file=sys.stderr, flush=True)
    prog = Progress(session)
    try:
        doc, md, js = run_pipeline(session, prog)
        prog.stop()  # heartbeat thread ends before the last write
        prog.flush(force=True)
    except Cancelled:
        prog.stop()
        print("worker cancelled", file=sys.stderr, flush=True)
        return 130
    except BaseException as exc:  # noqa: BLE001 - every failure must reach the bar
        prog.stop()
        traceback.print_exc()
        msg, retry = _classify(exc)
        rec = state.set("error", session, expect="transcribing", msg=msg, retry=retry, during="transcribe")
        if rec:
            clear_progress(session)
        return 1
    rec = state.set("done", session, expect="transcribing", transcript=str(md), json=str(js),
                    duration_s=doc["duration_s"], speakers=len(doc["speakers"]))
    if rec:
        clear_progress(session)
    print(f"worker done in {doc['stats']['wall_s']} s", file=sys.stderr, flush=True)
    return 0
