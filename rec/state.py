"""kozyrec persisted state: one record, one file, atomic writes.

The record lives in ~/.local/state/kozyrec/state.json and IS the state line the
bar sees: {"ev":"state","state":…,"at":…,…}. Every verb and every follower reads
it; every transition goes through set(), which takes a short flock, can
compare-and-set against the current state, and stamps `at` (strictly
increasing, so the bar's reducer can drop stale replays).

Keys starting with "_" are internal bookkeeping and never printed.
Stdlib only: this module is imported by every short verb.
"""
import fcntl, json, os, sys, time

HOME = os.path.expanduser("~")
STATE_DIR = os.environ.get("KOZYREC_STATE_DIR") or os.path.join(HOME, ".local/state/kozyrec")
CALLS = os.environ.get("KOZYREC_CALLS") or os.path.join(HOME, "Calls")
STATE = os.path.join(STATE_DIR, "state.json")
PROGRESS = os.path.join(STATE_DIR, "progress.json")
LOCK = os.path.join(STATE_DIR, ".lock")

STATES = ("idle", "starting", "recording", "stopping", "ask", "transcribing", "done", "error")
STABLE = ("idle", "ask", "done", "error")
IDLE = {"ev": "state", "state": "idle", "at": 0}


def write_json(path, obj):
    """tmp + fsync + rename: readers see the old file or the new one, never half."""
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    # Not tempfile: importing it costs ~15 ms on every short verb. The pid
    # makes the name unique among live writers.
    tmp = os.path.join(d, f".tmp-{os.getpid()}-{os.path.basename(path)}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def rmtree(path, ignore_errors=True):
    """shutil.rmtree, imported on use: shutil costs ~50 ms per short verb here."""
    import shutil
    shutil.rmtree(path, ignore_errors=ignore_errors)


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def read():
    """The current record. A missing or unreadable file reads as idle."""
    rec = read_json(STATE)
    if not isinstance(rec, dict) or rec.get("state") not in STATES:
        return dict(IDLE)
    rec["ev"] = "state"
    return rec


class locked:
    """flock around a read-modify-write of the record. Short: never hold it
    across a subprocess that can hang."""

    def __enter__(self):
        os.makedirs(STATE_DIR, exist_ok=True)
        self.fd = os.open(LOCK, os.O_RDWR | os.O_CREAT, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        os.close(self.fd)   # closing drops the lock


def _matches(rec, expect, session):
    """CAS: `expect` is a state or tuple of states; with a session given, the
    record must also belong to that session."""
    if expect is not None:
        allowed = (expect,) if isinstance(expect, str) else tuple(expect)
        if rec.get("state") not in allowed:
            return False
        if session is not None and rec.get("session") != session:
            return False
    return True


def set(state, session=None, expect=None, match_session=True, **fields):
    """Replace the record (nothing carries over), stamp `at`, write atomically.

    expect: a state or tuple of states the current record must be in — plus,
    when `session` is given and match_session, the same session. On mismatch
    nothing is written and None is returned. Prints nothing; callers emit()
    the result."""
    if state not in STATES:
        raise ValueError(f"unknown state {state!r}")
    with locked():
        cur = read()
        if not _matches(cur, expect, session if match_session else None):
            return None
        rec = {"ev": "state", "state": state,
               "at": max(time.time(), float(cur.get("at") or 0) + 1e-3)}
        if session is not None and state != "idle":   # idle belongs to no session
            rec["session"] = session
        rec.update(fields)
        write_json(STATE, rec)
        return rec


def update(expect, session=None, fn=None, **fields):
    """Merge fields into the current record without changing state or `at`
    (internal bookkeeping such as `_gaps`). With `fn`, the fields are computed
    from the record read under the lock — fn(rec) -> dict | None (None: no
    change) — so concurrent callers can't double-apply. Returns the new record
    or None."""
    with locked():
        cur = read()
        if not _matches(cur, expect, session):
            return None
        if fn is not None:
            more = fn(cur)
            if not more:
                return None
            fields = {**fields, **more}
        cur.update(fields)
        write_json(STATE, cur)
        return cur


def public(rec):
    """The line the bar sees. A persisted error goes out as ev:error so the
    reducer picks up msg/retry/during."""
    out = {k: v for k, v in rec.items() if not k.startswith("_")}
    out["ev"] = "error" if out.get("state") == "error" else "state"
    return out


def emit(obj):
    """One JSON object per line on stdout, flushed. State records are
    rendered through public()."""
    if obj.get("ev") in ("state", "error") and "state" in obj:
        obj = public(obj)
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader (a QML Process) went away: nothing left to tell anyone.
        os._exit(0)


def error_line(msg, **fields):
    """A transient error line (not persisted): the verb failed before any
    state changed, e.g. start refused by preflight."""
    return {"ev": "error", "msg": msg, "at": time.time(), **fields}


def ask_fields(session):
    """The fields of an `ask` record for `session`, rebuilt from its
    session.json, so every path into ask (stop, auto-stop, cancelled
    transcription) produces the same line: duration_s, stop_reason, warn."""
    meta = read_json(os.path.join(session, "session.json"), {}) or {}
    return {"duration_s": round(float(meta.get("duration_s") or 0), 1),
            "stop_reason": meta.get("stop_reason") or "user",
            "warn": meta.get("warn")}
