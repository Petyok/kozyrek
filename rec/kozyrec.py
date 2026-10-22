#!/usr/bin/env python3
"""kozyrec — record a call, then turn it into a speaker-labelled transcript.

    kozyrec start              begin recording (idempotent: re-prints a live recording)
    kozyrec stop [--reason R]  stop recording → state ask (idempotent)
    kozyrec status [--follow]  print the state; --follow streams changes and
                               exits right after a stable state (idle/ask/done/error)
    kozyrec dismiss [<dir>]    ask/done/error → idle, files kept; with <dir>, only
                               if the record is still that session's
    kozyrec discard            ask/done/error → idle, the whole session deleted
    kozyrec transcribe <dir>   start/resume transcription (detached worker)
    kozyrec cancel             stop transcription → back to ask

Output: one JSON object per line on stdout (see state.py). Every verb is short
and kill-safe: the long-lived work runs in transient systemd --user units
(capture.py) or a detached worker (transcribe.py), and every transition is an
atomic compare-and-set on ~/.local/state/kozyrec/state.json. A verb killed
half-way loses only its own output; `status` repairs whatever it left behind.

Recording stays on this laptop (PipeWire → Ogg files in ~/Calls); the models
run on a GPU host while its GPU is idle, if one is set up, else here (see docs/rec.md).
"""
import os, signal, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state, capture, prune  # noqa: E402

SCRIPT = os.path.abspath(__file__)
START_GRACE_S = 20      # a `starting` older than this with no recorder is stale
CANCEL_GRACE_S = 15     # a worker may die this long after `cancel` without being an error
STOP_REISSUE_S = 5      # a `stopping` this old on a still-active unit gets its stop job again
LIVE_EVERY_S = 5


def carry(rec):
    """A record's own fields, for re-setting it under a new state."""
    return {k: v for k, v in rec.items() if k not in ("ev", "state", "at", "session")}


def pid_alive(pid):
    try:
        os.kill(int(pid), 0)
        with open(f"/proc/{int(pid)}/cmdline", "rb") as f:
            return b"kozyrec" in f.read()
    except (OSError, ValueError, TypeError):
        return False


def has_audio(session):
    return any(capture.ogg_opus_info(os.path.join(session, n))[0] >= 1.0
               for n in ("mic.opus", "desktop.opus"))


def inside_calls(path):
    root = os.path.realpath(state.CALLS)
    p = os.path.realpath(path or "")
    return p != root and p.startswith(root + os.sep)


# ── repair: whatever a killed verb / crashed unit / reboot left behind ─────
def reconcile(rec, now=None):
    now = time.time() if now is None else now
    s, session = rec["state"], rec.get("session")
    age = now - float(rec.get("at") or 0)

    # Every repair below acts only on a definite answer from systemd: a
    # timed-out `systemctl is-active` (None) must not kill a live recording.
    if s == "starting" and age >= START_GRACE_S:
        live = capture.unit_live(capture.REC_UNIT)
        if live is None:
            return rec
        if live:
            # `start` was killed after launching the recorder: adopt it.
            fields = carry(rec)
            fields.setdefault("since", rec.get("at"))
            fields.setdefault("aec", capture.unit_live(capture.AEC_UNIT) is not False)
            fields.setdefault("max_min", capture.MAX_MIN)
            fields.setdefault("_bt0", capture.boot_offset())
            fields.setdefault("_gaps", [])
            return state.set("recording", session, expect="starting", **fields) or state.read()
        capture.stop_unit(capture.AEC_UNIT)
        if session and has_audio(session):
            return capture.settle(rec, None, expect="starting") or state.read()
        new = state.set("idle", expect="starting", session=session)
        if new is not None and session and inside_calls(session):
            state.rmtree(session, ignore_errors=True)
        return new or state.read()

    if s in ("recording", "stopping"):
        rec_state = capture.unit_state(capture.REC_UNIT)
        if rec_state is None:
            return rec
        if rec_state not in capture.LIVE_STATES:
            # The recorder is gone but _finalize never ran (crash of the user
            # manager, power loss, reboot).
            capture.stop_unit(capture.AEC_UNIT)
            return capture.settle(rec, None) or state.read()
        if s == "stopping" and rec_state == "active" and age >= STOP_REISSUE_S:
            # `stopping` was persisted but no stop job is running: the verb
            # was killed before queueing it, or systemctl refused. Re-issuing
            # is harmless (a stop job on a stopping unit is a no-op).
            capture.stop_unit(capture.REC_UNIT)
        return rec

    if s == "transcribing":
        pid, cancel_at = rec.get("worker_pid"), rec.get("cancel_at")
        grace = cancel_at is not None and now - float(cancel_at) < CANCEL_GRACE_S
        if pid and not grace and not pid_alive(pid):
            return state.set("error", session, expect="transcribing", msg="transcriber died",
                             retry=True, during="transcribe") or state.read()

    if (s != "starting" and capture.unit_live(capture.AEC_UNIT) is True
            and capture.unit_live(capture.REC_UNIT) is False):
        capture.stop_unit(capture.AEC_UNIT)           # left over by a finalize that never ran
    return rec


def auto_stop(rec, free=None):
    """Recording-time guards shared by status and the follower. Idempotent:
    any number of callers, any of them killed mid-way."""
    session = rec.get("session")
    bt, now = capture.boot_offset(), time.time()
    new = state.update("recording", session, fn=lambda cur: capture.add_gap(cur, bt, now))
    gaps = (new or rec).get("_gaps") or []
    # Decided from the persisted gaps, not from who appended one: a follower
    # killed between the two steps must not lose the stop.
    if any(g.get("dur_s", 0) >= capture.SUSPEND_STOP_S for g in gaps):
        return do_stop("suspend", wait=False)[0]
    if free is not None and free < capture.LOW_DISK_STOP:
        return do_stop("low_disk", wait=False)[0]
    return None


def do_stop(reason="user", wait=True):
    """-> (record, error message or None)."""
    rec = state.read()
    if rec["state"] == "starting" and wait:
        deadline = time.monotonic() + 6
        while rec["state"] == "starting" and time.monotonic() < deadline:
            time.sleep(0.1)
            rec = state.read()
    if rec["state"] == "recording":
        # Queue the stop job first, persist `stopping` second: killed in
        # between, _finalize still lands in ask (it accepts recording too).
        mine = capture.write_marker(rec["session"], reason)
        if not capture.stop_unit(capture.REC_UNIT):
            if mine:                                   # don't label a later stop
                capture.clear_marker(rec["session"])
            return state.read(), "Could not stop the recorder (systemd did not answer)"
        state.set("stopping", rec["session"], expect="recording", **carry(rec))
    elif rec["state"] == "stopping":
        capture.stop_unit(capture.REC_UNIT)          # re-issuing a stop job is a no-op
    if wait:
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            rec = state.read()
            if rec["state"] not in ("recording", "stopping"):
                break
            if capture.unit_live(capture.REC_UNIT) is False:
                rec = reconcile(rec)
                if rec["state"] not in ("recording", "stopping"):
                    break
            time.sleep(0.1)
    return state.read(), None


def with_live(rec, live=None):
    if rec["state"] not in ("recording", "stopping"):
        return rec
    live = capture.live_fields(rec) if live is None else live
    return {**rec, **{k: v for k, v in live.items() if not k.startswith("_")}}


# ── verbs ──────────────────────────────────────────────────────────────────
def cmd_start():
    rec = reconcile(state.read())
    s = rec["state"]
    if s in ("starting", "recording", "stopping"):
        state.emit(with_live(rec))
        return 0
    if s in ("ask", "transcribing"):
        state.emit(rec)                               # decide / finish that one first
        return 0
    if s in ("done", "error"):
        state.set("idle", expect=s)                   # start implies dismiss
    prune.prune()

    mic = capture.find_mic()
    if not mic:
        state.emit(state.error_line("No microphone found", during="record", retry=True))
        return 1
    os.makedirs(state.CALLS, exist_ok=True)
    need = capture.MAX_MIN * 60 * capture.BYTES_PER_S + capture.PREFLIGHT_SPARE
    if capture.disk_free(state.CALLS) < need:
        state.emit(state.error_line(f"Less than {need // 2**20} MB free in {state.CALLS}",
                                    during="record", retry=True))
        return 1
    live = capture.unit_live(capture.REC_UNIT)
    if live is None:
        state.emit(state.error_line("systemd --user did not answer; try again", during="record",
                                    retry=True))
        return 1
    if live:
        state.emit(state.error_line("A recorder unit is already running", during="record", retry=True))
        return 1

    session = os.path.join(state.CALLS, time.strftime(prune.NAME_FMT))
    try:
        os.mkdir(session)
    except FileExistsError:
        time.sleep(1.0)
        session = os.path.join(state.CALLS, time.strftime(prune.NAME_FMT))
        os.mkdir(session)
    if state.set("starting", session, expect="idle", match_session=False, device=mic,
                 max_min=capture.MAX_MIN) is None:
        os.rmdir(session)                             # lost a race with another start
        state.emit(with_live(state.read()))
        return 0
    try:
        aec = capture.start_aec(mic)
        capture.start_recorder(session, mic, aec, SCRIPT)
        fields = {"since": time.time(), "aec": aec, "device": mic, "max_min": capture.MAX_MIN,
                  "_bt0": capture.boot_offset(), "_gaps": [], "_sinks": [capture.default_sink()]}
        state.update("starting", session, **fields)
        if not capture.wait_audio(session) and capture.unit_live(capture.REC_UNIT) is False:
            raise capture.CaptureError("The recorder stopped right away")
    except (capture.CaptureError, OSError) as e:
        capture.stop_unit(capture.REC_UNIT, block=True)
        capture.stop_unit(capture.AEC_UNIT, block=True)
        if state.read().get("session") == session and inside_calls(session):
            state.rmtree(session, ignore_errors=True)
        # No session in the record: there is nothing left to retry or delete.
        state.set("error", expect="starting", msg=str(e), retry=True, during="record")
        state.emit(state.read())
        return 1
    new = state.set("recording", session, expect="starting", **fields)
    state.emit(with_live(new or state.read()))
    return 0


def cmd_stop(argv):
    reason = "user"
    if "--reason" in argv:
        i = argv.index("--reason")
        reason = argv[i + 1] if i + 1 < len(argv) else reason
    rec, err = do_stop(reason)
    if err:
        state.emit(state.error_line(err, session=rec.get("session"), during="record", retry=True))
        return 1
    state.emit(with_live(rec))
    return 0


def _progress_for(rec):
    prog = state.read_json(state.PROGRESS)
    if isinstance(prog, dict) and prog.get("session") == rec.get("session"):
        prog["ev"] = "progress"
        return prog
    return None


def cmd_status(argv):
    if "--follow" in argv:
        return follow()
    rec = reconcile(state.read())
    live = None
    if rec["state"] == "recording":
        live = capture.live_fields(rec)
        if auto_stop(rec, live.get("_free")):
            rec, live = state.read(), None
    state.emit(with_live(rec, live))
    if rec["state"] == "transcribing":
        prog = _progress_for(rec)
        if prog:
            state.emit(prog)
    return 0


def follow():
    """Print the state now, then every change, whoever caused it. Exit right
    after a stable state. SIGTERM/SIGKILL just end it; it never cancels."""
    signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
    printed, prog_at, live, live_t, check_t = None, None, None, 0.0, 0.0
    while True:
        rec = state.read()
        t = time.monotonic()
        if t - check_t >= LIVE_EVERY_S or rec["state"] in state.STABLE or printed is None:
            rec = reconcile(rec)
            check_t = t
        if rec["state"] == "recording":
            if live is None or t - live_t >= LIVE_EVERY_S:
                live, live_t = capture.live_fields(rec), t
                sink = capture.default_sink()
                state.update("recording", rec["session"], fn=lambda cur: (
                    {"_sinks": (cur.get("_sinks") or []) + [sink]}
                    if sink and sink not in (cur.get("_sinks") or []) else None))
            if auto_stop(rec, live.get("_free")):
                live = None
                continue
            line = state.public(with_live(rec, live))
        else:
            live = None
            line = state.public(rec)
        if line != printed:
            state.emit(line)
            printed = line
        if rec["state"] == "transcribing":
            prog = _progress_for(rec)
            if prog and prog.get("at") != prog_at:
                state.emit(prog)
                prog_at = prog.get("at")
        if rec["state"] in state.STABLE:
            return 0
        time.sleep(0.25 if rec["state"] in ("starting", "stopping") else
                   0.5 if rec["state"] == "transcribing" else 1.0)


def cmd_dismiss(argv):
    # A session dir comes from a transcript notification, which can be clicked
    # long after: it must never dismiss a newer call's question or result.
    rec = state.read()
    only = os.path.abspath(os.path.expanduser(argv[0])) if argv else None
    if rec["state"] in ("ask", "done", "error") and only in (None, rec.get("session")):
        rec = state.set("idle", expect=rec["state"], session=rec.get("session")) or state.read()
        prune.prune(active=rec.get("session"))
    state.emit(with_live(rec))
    return 0


def cmd_discard():
    rec = state.read()
    s, session = rec["state"], rec.get("session")
    if s in ("starting", "recording", "stopping", "transcribing"):
        state.emit(state.error_line(f"Can't delete while {s}", session=session, during="record"))
        return 1
    if s in ("ask", "done", "error"):
        new = state.set("idle", expect=s, session=session)
        if new is not None and session and inside_calls(session):
            state.rmtree(session, ignore_errors=True)
        rec = new or state.read()
        prune.prune(active=rec.get("session"))
    state.emit(rec)
    return 0


def cmd_finalize():
    """ExecStopPost of kozyrec-rec: runs after every end of gst — clean stop,
    crash, RuntimeMaxSec. Never fails the unit."""
    session = os.environ.get("KOZYREC_SESSION", "")
    result = os.environ.get("SERVICE_RESULT")
    capture.stop_unit(capture.AEC_UNIT)
    try:
        os.unlink(os.path.join(capture.RUN_DIR, "aec.conf"))
    except OSError:
        pass
    rec = state.read()
    if rec.get("session") == session and rec["state"] in ("recording", "stopping"):
        new = capture.settle(rec, result)
        print(f"kozyrec: finalized {session}: result={result} "
              f"exit={os.environ.get('EXIT_CODE')}/{os.environ.get('EXIT_STATUS')} "
              f"-> {new and new['state']}", file=sys.stderr)
    return 0


def main(argv):
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    verb, rest = argv[0], argv[1:]
    if verb == "start":
        return cmd_start()
    if verb == "stop":
        return cmd_stop(rest)
    if verb == "status":
        return cmd_status(rest)
    if verb == "dismiss":
        return cmd_dismiss(rest)
    if verb == "discard":
        return cmd_discard()
    if verb == "_finalize":
        return cmd_finalize()
    if verb in ("transcribe", "_worker") and not rest:
        state.emit(state.error_line(f"usage: kozyrec {verb} <session-dir>", during="transcribe"))
        return 2
    if verb == "transcribe":
        prune.prune(active=rest[0])
        import transcribe
        return transcribe.cmd_transcribe(os.path.abspath(rest[0]))
    if verb == "cancel":
        import transcribe
        return transcribe.cmd_cancel()
    if verb == "_worker":
        import transcribe
        return transcribe.worker_main(os.path.abspath(rest[0]))
    print(__doc__.strip(), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
