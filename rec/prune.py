"""Audio TTL: recordings are heavy, transcripts are small.

Audio (mic.opus, desktop.opus) and transcription checkpoints (.transcribe/) of
any session older than AUDIO_TTL_DAYS are deleted; transcript.md/.json and
session.json stay forever. Runs opportunistically from start / transcribe /
dismiss / discard — no timer, no resident process. The active session is never
touched.
"""
import os, time

import state

AUDIO_TTL_DAYS = 7
AUDIO = ("mic.opus", "desktop.opus")
CHECKPOINTS = ".transcribe"
NAME_FMT = "%Y-%m-%d_%H-%M-%S"


def session_time(path):
    """Start time of a session: from its dir name, else its mtime."""
    name = os.path.basename(path.rstrip("/"))
    try:
        return time.mktime(time.strptime(name[:19], NAME_FMT))
    except ValueError:
        try:
            return os.stat(path).st_mtime
        except OSError:
            return None


def expired(sessions, now, active=None, ttl_days=AUDIO_TTL_DAYS):
    """sessions: [(path, start_unix|None)] -> paths whose audio must go."""
    cutoff = now - ttl_days * 86400
    act = os.path.realpath(active) if active else None
    return [p for p, t in sessions
            if t is not None and t < cutoff and os.path.realpath(p) != act]


def prune(active=None, now=None, root=None):
    """Delete expired audio. Returns the session dirs that lost something."""
    root = root or state.CALLS
    now = time.time() if now is None else now
    try:
        names = os.listdir(root)
    except OSError:
        return []
    sessions = [(p, session_time(p)) for p in (os.path.join(root, n) for n in names)
                if os.path.isdir(p) and not os.path.islink(p)]
    touched = []
    for path in expired(sessions, now, active):
        hit = False
        for name in AUDIO:
            try:
                os.unlink(os.path.join(path, name))
                hit = True
            except OSError:
                pass
        ck = os.path.join(path, CHECKPOINTS)
        if os.path.isdir(ck):
            state.rmtree(ck, ignore_errors=True)
            hit = True
        if hit:
            touched.append(path)
    return touched
