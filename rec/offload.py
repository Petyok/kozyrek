"""kozyrec offload: the two model stages on the GPU host while it is idle.

Laptop side: connect() opens ONE remote shell (GPU_CMD_FILE) per engine. Its stdin
carries a bootstrap (this module and the ones it imports, as source), then the
tracks and the stage requests; its stdout brings back JSON lines: hello,
progress, results. The worker commits every result as it arrives, exactly like
its own, so a lost link costs at most the island in flight.

Host side: serve() runs the laptop's own loops (transcribe.diarize_track and
recognize_islands) on the GPU and keeps the call off the host's disks:

  - the audio stays in RAM and reaches ffmpeg through a pipe; results exist
    only in RAM and on the SSH channel; kozyrek's code is imported from memory;
  - the process locks its memory (no swap) and cannot dump core;
  - it exits as soon as the laptop says bye or closes the channel, and after
    WATCHDOG_S without a byte from the laptop (asleep, or gone).

Busy = a GPU utilisation sample >= BUSY_UTIL_PCT, less than MIN_FREE_MIB of
free VRAM, or a running or queued ComfyUI job. Busy, unreachable or not set up
(rec/gpu-setup.sh): connect() returns None and the laptop does the job.
"""

import base64
import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import numpy as np

# One line: the command that runs a shell command on the GPU host, e.g. `ssh gpu-box`;
# the remote command is appended as one more argument. No file (or empty) = never offload.
GPU_CMD_FILE = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "kozyrek" / "gpu-host"
ROOT = "/opt/kozyrec"  # on the host, made by rec/gpu-setup.sh
MODULES = ("state", "merge", "nemo_speech", "transcribe", "offload")  # shipped to the host
TRIES = 2  # connections per transcription: a lost link gets one reconnect
HELLO_TIMEOUT_S = 25.0  # connect + python start + the busy probe; longer = unreachable
REPLY_TIMEOUT_S = 120.0  # a silent host is a lost host
PING_S = 10.0
WATCHDOG_S = 60.0  # host: this long without a byte from the laptop ends the process
BUSY_UTIL_PCT = 20
MIN_FREE_MIB = 2500  # both models on the GPU with headroom
PROBE_SAMPLES, PROBE_GAP_S = 5, 0.2
COMFY_QUEUE = "http://127.0.0.1:8188/queue"
PROGRESS_EVERY_S = 0.25
CHUNK = 1 << 20
BOOT = "import sys;exec(sys.stdin.buffer.read(int(sys.stdin.buffer.readline())))"

# prctl / mlockall constants (linux/prctl.h, sys/mman.h)
PR_SET_DUMPABLE = 4
MCL_CURRENT, MCL_FUTURE, MCL_ONFAULT = 1, 2, 4


class Lost(Exception):
    """The host failed or the link broke: the next engine resumes from the checkpoints."""


def _log(msg):
    print(f"offload: {msg}", file=sys.stderr, flush=True)


def _tr():
    import transcribe  # imported lazily: transcribe imports this module
    return transcribe


def busy_reason(samples, comfy):
    """Why the host must not take a job now, or None when it is free.
    samples: [(utilisation %, free MiB)] taken PROBE_GAP_S apart; comfy: the
    reply of ComfyUI's /queue, None when ComfyUI does not answer."""
    if not samples:
        return "no GPU visible"
    util = max(u for u, _ in samples)
    if util >= BUSY_UTIL_PCT:
        return f"GPU {util}% busy"
    free = min(f for _, f in samples)
    if free < MIN_FREE_MIB:
        return f"{free} MiB of VRAM free"
    if comfy and (comfy.get("queue_running") or comfy.get("queue_pending")):
        return "ComfyUI has a job"
    return None


# ---- laptop side ---------------------------------------------------------------------


def _gpu_cmd():
    try:
        return shlex.split(GPU_CMD_FILE.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        return []


def _remote_command():
    lib = f"{ROOT}/nemo-speech/lib"
    env = {"KOZYREC_MODEL_DIR": f"{ROOT}/models", "KOZYREC_NEMO_LIB": lib, "LD_LIBRARY_PATH": lib,
           "TMPDIR": "/dev/shm", "OMP_WAIT_POLICY": "PASSIVE"}
    assigns = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items())
    return f"env {assigns} {ROOT}/venv/bin/python -u -c {shlex.quote(BOOT)}"


_LOADER = r'''
import importlib.abc, importlib.util, linecache, sys


class _Sources(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    """The laptop's modules, imported from memory: nothing of kozyrek on this disk."""

    def find_spec(self, name, path=None, target=None):
        return importlib.util.spec_from_loader(name, self) if name in SOURCES else None

    def create_module(self, spec):
        return None

    def exec_module(self, module):
        src, fn = SOURCES[module.__name__], f"/kozyrek/{module.__name__}.py"
        module.__file__ = fn
        linecache.cache[fn] = (len(src), None, src.splitlines(True), fn)
        exec(compile(src, fn, "exec"), module.__dict__)


sys.meta_path.insert(0, _Sources())
import offload
offload.serve()
'''


def _payload():
    here = Path(__file__).resolve().parent
    sources = {m: (here / f"{m}.py").read_text(encoding="utf-8") for m in MODULES}
    return (f"SOURCES = {sources!r}\n" + _LOADER).encode()


def connect():
    """A RemoteEngine on the GPU host when it is idle and set up, else None; the
    reason goes to the worker log. Raises only transcribe.Cancelled."""
    cmd = _gpu_cmd()
    if not cmd:
        return None
    try:
        proc = subprocess.Popen(cmd + [_remote_command()],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, close_fds=True)
    except OSError as exc:
        _log(f"{cmd[0]} failed to start: {exc}; the laptop transcribes")
        return None
    eng = RemoteEngine(proc)
    try:
        payload = _payload()
        eng._write(b"%d\n" % len(payload) + payload)
        hello = eng._until("hello", timeout=HELLO_TIMEOUT_S)
    except Lost as exc:
        eng.kill()
        _log(f"GPU host unreachable ({exc}); the laptop transcribes")
        return None
    except BaseException:
        eng.kill()
        raise
    if not hello.get("free"):
        eng.close()
        _log(f"GPU host busy ({hello.get('why')}); the laptop transcribes")
        return None
    _log(f"GPU host free ({hello.get('gpu')}), nemo-speech {hello.get('version')}")
    eng.start()
    return eng


class RemoteEngine:
    """transcribe's engine interface over one remote shell. Every failure is Lost."""

    where = "gpu"
    perf = "gpu_"  # perf.json key prefix

    def __init__(self, proc):
        self.proc = proc
        self._events = queue.Queue()
        self._wlock = threading.Lock()
        self._closed = threading.Event()
        self._sent = set()
        threading.Thread(target=self._read, daemon=True).start()

    def start(self):
        threading.Thread(target=self._pinger, daemon=True).start()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, *exc):
        if exc_type is None:
            self.close()
        else:
            self.kill()

    # -- channel --

    def _read(self):
        for line in self.proc.stdout:
            try:
                ev = json.loads(line)
            except ValueError:  # a login banner or the like
                continue
            if isinstance(ev, dict):
                self._events.put(ev)
        self._events.put(None)

    def _write(self, data):
        try:
            with self._wlock:
                self.proc.stdin.write(data)
                self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            _tr()._check_cancel()
            raise Lost(f"link closed ({exc})") from exc

    def _send(self, obj):
        self._write(json.dumps(obj, ensure_ascii=False).encode() + b"\n")

    def _pinger(self):
        while not self._closed.wait(PING_S):
            try:
                self._send({"op": "ping"})
            except Exception:  # noqa: BLE001 - the main thread sees the same failure
                return

    def _next(self, timeout=REPLY_TIMEOUT_S):
        deadline = time.monotonic() + timeout
        while True:
            _tr()._check_cancel()
            try:
                ev = self._events.get(timeout=0.25)
            except queue.Empty:
                if time.monotonic() > deadline:
                    raise Lost(f"no answer for {timeout:.0f} s") from None
                continue
            if ev is None:
                _tr()._check_cancel()
                try:
                    rc = self.proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    rc = None
                raise Lost(f"link closed (rc={rc})")
            if ev.get("ev") == "fail":
                raise Lost(f"host failed: {ev.get('msg')}")
            return ev

    def _until(self, kind, on_progress=None, timeout=REPLY_TIMEOUT_S):
        while True:
            ev = self._next(timeout)
            if ev.get("ev") == "p" and on_progress:
                on_progress(ev["v"])
            elif ev.get("ev") == kind:
                return ev
            else:
                raise Lost(f"unexpected {ev.get('ev')!r} while waiting for {kind!r}")

    def close(self):
        """The job is done: bye, and the host drops everything at once."""
        self._closed.set()
        try:
            self._send({"op": "bye"})
            self.proc.stdin.close()
        except Exception:  # noqa: BLE001 - closing a dead link
            pass
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.kill()

    def kill(self):
        # Our end of stdin first: even an ssh orphaned by its wrapper's death then
        # sends EOF, and the host exits on EOF.
        self._closed.set()
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    # -- engine interface (see transcribe.LocalEngine) --

    def upload_mb(self, names, tracks):
        return sum(tracks[t]["path"].stat().st_size for t in names if t not in self._sent) / 1e6

    def upload(self, names, tracks):
        for t in names:
            if t in self._sent:
                continue
            data = tracks[t]["path"].read_bytes()
            self._write(json.dumps({"op": "audio", "track": t, "size": len(data)}).encode() + b"\n" + data)
            self._sent.add(t)

    def diarizer(self):
        return _RemoteModel(self, "diar")

    def recognizer(self):
        return _RemoteModel(self, "asr")

    def diarize(self, diar, name, info, on_progress):
        self._send({"op": "diarize", "track": name})
        ev = self._until("diar", on_progress)
        env = np.frombuffer(base64.b64decode(ev["env"]), dtype=np.float16).astype(np.float32)
        return [tuple(s) for s in ev["segments"]], float(ev["duration_s"]), env

    def recognize(self, rec, name, info, islands, on_progress, on_island):
        self._send({"op": "asr", "track": name, "offset": info["offset"],
                    "islands": [list(i) for i in islands]})
        while True:
            ev = self._next()
            kind = ev.get("ev")
            if kind == "p":
                on_progress(ev["v"])
            elif kind == "island":
                on_island(ev["record"], ev["len"])
            elif kind == "asr_done":
                return
            else:
                raise Lost(f"unexpected {kind!r} during asr")


class _RemoteModel:
    def __init__(self, eng, what):
        self.eng, self.what = eng, what

    def __enter__(self):
        self.eng._send({"op": "open", "what": self.what})
        self.eng._until("opened")
        return self

    def __exit__(self, exc_type, *exc):
        if exc_type is None:
            self.eng._send({"op": "close", "what": self.what})


# ---- host side -----------------------------------------------------------------------


def _harden():
    """No page of this process may reach the host's disk: no swap, no core
    dump. Returns why that could not be ensured, or None."""
    import ctypes
    import resource
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))  # inherited by ffmpeg
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        return f"prctl: {os.strerror(ctypes.get_errno())}"
    if libc.mlockall(MCL_CURRENT | MCL_FUTURE | MCL_ONFAULT) != 0:
        return f"mlockall: {os.strerror(ctypes.get_errno())}"
    return None


def _gpu_samples():
    out = []
    for i in range(PROBE_SAMPLES):
        if i:
            time.sleep(PROBE_GAP_S)
        try:
            r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.free",
                                "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
            util, free = (int(x) for x in r.stdout.splitlines()[0].split(","))
        except (OSError, subprocess.SubprocessError, ValueError, IndexError):
            break  # no driver, or a driver/library mismatch: no usable GPU
        out.append((util, free))
    return out


def _comfy_queue():
    import urllib.request
    try:
        with urllib.request.urlopen(COMFY_QUEUE, timeout=2) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


class _Out:
    def __init__(self, stream):
        self.stream, self.lock = stream, threading.Lock()

    def send(self, obj):
        data = json.dumps(obj, ensure_ascii=False).encode() + b"\n"
        try:
            with self.lock:
                self.stream.write(data)
                self.stream.flush()
        except OSError:
            os._exit(0)  # the laptop is gone


class _Inbox:
    """The laptop's half of the channel, read on a thread. EOF or bye: the job
    is over or cancelled; WATCHDOG_S without a byte (the laptop pings every
    PING_S): the laptop sleeps or is gone. Either way `exit` at once, and the
    audio and every result go with the process."""

    def __init__(self, stream, timeout=WATCHDOG_S, exit=os._exit):
        self.stream, self.timeout, self.exit = stream, timeout, exit
        self.q, self.seen = queue.Queue(), time.monotonic()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()

    def get(self):
        return self.q.get()

    def _read(self):
        try:
            for line in iter(self.stream.readline, b""):
                self.seen = time.monotonic()
                msg = json.loads(line)
                if msg["op"] == "bye":
                    break
                if msg["op"] == "ping":
                    continue
                if msg["op"] == "audio":
                    msg["data"] = self._read_exact(int(msg["size"]))
                self.q.put(msg)
        except Exception:  # noqa: BLE001 - a torn channel ends the job like EOF
            traceback.print_exc()
        self.exit(0)

    def _read_exact(self, n):
        buf = bytearray(n)
        view, got = memoryview(buf), 0
        while got < n:
            k = self.stream.readinto(view[got:got + CHUNK])
            if not k:
                raise EOFError(f"audio cut short at {got} of {n} bytes")
            got += k
            self.seen = time.monotonic()
        return buf

    def _watch(self):
        while True:
            time.sleep(min(1.0, self.timeout / 4))
            if time.monotonic() - self.seen > self.timeout:
                print(f"offload: no word from the laptop for {self.timeout:g} s; exiting",
                      file=sys.stderr, flush=True)
                self.exit(3)
                return


def _throttled(out):
    last = [0.0]

    def on_progress(v):
        now = time.monotonic()
        if now - last[0] >= PROGRESS_EVERY_S:
            last[0] = now
            out.send({"ev": "p", "v": round(v, 2)})
    return on_progress


def serve():
    """The host's end of connect(): hello (free or why not), then ops until bye/EOF."""
    out = _Out(sys.stdout.buffer)
    samples = _gpu_samples()
    why = _harden() or busy_reason(samples, _comfy_queue())
    lib = None
    if why is None:
        import nemo_speech as ns
        tr = _tr()
        try:
            lib = ns.Lib()
        except OSError as exc:
            why = f"runtime missing: {exc}"
        missing = [m["file"] for m in (tr.ASR_MODEL, tr.DIAR_MODEL) if not (tr.MODEL_DIR / m["file"]).exists()]
        if why is None and missing:
            why = f"models missing: {', '.join(missing)}"
    gpu = f"{max(u for u, _ in samples)}% busy, {min(f for _, f in samples)} MiB free" if samples else None
    out.send({"ev": "hello", "free": why is None, "why": why, "gpu": gpu,
              "version": lib.version() if lib and why is None else None})
    if why is not None:
        return

    inbox = _Inbox(sys.stdin.buffer)
    audio, models = {}, {}
    try:
        while True:
            msg = inbox.get()
            op = msg["op"]
            if op == "audio":
                audio[msg["track"]] = msg["data"]
            elif op == "open":
                t0 = time.monotonic()
                models[msg["what"]] = (
                    ns.Diarizer(lib, tr.MODEL_DIR / tr.DIAR_MODEL["file"], tr.DIAR_PRESET, gpu=0)
                    if msg["what"] == "diar" else
                    ns.Recognizer(lib, tr.MODEL_DIR / tr.ASR_MODEL["file"], None, gpu=0))
                out.send({"ev": "opened", "what": msg["what"], "s": round(time.monotonic() - t0, 2)})
            elif op == "close":
                models.pop(msg["what"]).close()
            elif op == "diarize":
                with tr.TrackReader(data=audio[msg["track"]]) as rd:
                    segs, duration, env = tr.diarize_track(models["diar"], rd, _throttled(out))
                out.send({"ev": "diar", "segments": [list(s) for s in segs], "duration_s": duration,
                          "env": base64.b64encode(env.astype(np.float16).tobytes()).decode()})
            elif op == "asr":
                with tr.TrackReader(data=audio[msg["track"]]) as rd:
                    tr.recognize_islands(
                        models["asr"], rd, float(msg["offset"]), [tuple(i) for i in msg["islands"]],
                        _throttled(out),
                        lambda record, length: out.send({"ev": "island", "record": record, "len": length}))
                out.send({"ev": "asr_done"})
            else:
                raise ValueError(f"unknown op {op!r}")
    except Exception as exc:  # noqa: BLE001 - every failure must reach the laptop
        traceback.print_exc()
        out.send({"ev": "fail", "msg": f"{type(exc).__name__}: {exc}"[:200]})
        os._exit(1)
