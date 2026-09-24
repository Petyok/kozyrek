#!/usr/bin/env python3
"""Checks for the capture side that can go wrong silently: state CAS and
rendering, audio TTL selection, stop-reason mapping, suspend gaps, repair of a
dead worker, mic/app detection, Ogg duration of a killed file.
Run: ./test_capture.py (needs gst-launch-1.0 for the Ogg check; no audio devices)."""
import contextlib, io, json, os, signal, subprocess, sys, tempfile, time

ROOT = tempfile.mkdtemp(prefix="kzrcap-test-")
os.environ["KOZYREC_STATE_DIR"] = os.path.join(ROOT, "state")
os.environ["KOZYREC_CALLS"] = os.path.join(ROOT, "Calls")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import state, prune, capture, kozyrec  # noqa: E402


@contextlib.contextmanager
def fake_systemd(units=None, broken=False, stop_ok=True):
    """Replace capture.run for systemctl: units = {name: is-active word}
    (missing → a collected unit: "inactive", rc 4); broken → every call times
    out like a hung user manager. Yields the list of stop calls made."""
    units = units or {}
    stops, real = [], capture.run

    def run(cmd, timeout=5):
        if cmd[:2] != ["systemctl", "--user"]:
            return real(cmd, timeout)
        if broken:
            return 127, "Command '...' timed out after 5 seconds"
        if cmd[2] == "is-active":
            word = units.get(cmd[3], None)
            return (0 if word == "active" else 3 if word else 4), (word or "inactive") + "\n"
        if cmd[2] == "stop":
            stops.append(cmd[-1].removesuffix(".service"))
            return (0 if stop_ok else 1), ""
        return 1, ""

    capture.run = run
    try:
        yield stops
    finally:
        capture.run = real


def _reset():
    try:
        os.unlink(state.STATE)
    except OSError:
        pass


# ── state ──────────────────────────────────────────────────────────────────
def test_cas_refuses_the_wrong_state_or_session_and_writes_nothing():
    _reset()
    a = state.set("ask", "/s/1", duration_s=10)
    assert state.set("done", "/s/1", expect="transcribing") is None
    assert state.set("transcribing", "/s/2", expect="ask") is None       # other session
    assert state.read() == a
    b = state.set("transcribing", "/s/1", expect=("ask", "error"), worker_pid=1)
    assert b["state"] == "transcribing" and "duration_s" not in b          # nothing carries over
    assert b["at"] > a["at"]


def test_at_is_strictly_increasing_even_within_one_clock_tick():
    _reset()
    ats = [state.set("idle")["at"] for _ in range(20)]
    assert all(y > x for x, y in zip(ats, ats[1:]))


def test_idle_never_belongs_to_a_session():
    _reset()
    state.set("ask", "/s/1")
    rec = state.set("idle", "/s/1", expect="ask")
    assert rec is not None and "session" not in rec


def test_starting_from_idle_needs_no_session_match():
    _reset()
    state.set("idle")
    assert state.set("starting", "/s/new", expect="idle") is None
    assert state.set("starting", "/s/new", expect="idle", match_session=False)["session"] == "/s/new"


def test_update_fn_is_applied_once_under_the_lock():
    _reset()
    state.set("recording", "/s/1", _gaps=[])
    add = lambda cur: None if cur["_gaps"] else {"_gaps": [1]}
    assert state.update("recording", "/s/1", fn=add)["_gaps"] == [1]
    assert state.update("recording", "/s/1", fn=add) is None
    assert state.read()["_gaps"] == [1]


def test_public_line_hides_internals_and_renders_errors_as_ev_error():
    line = state.public({"ev": "state", "state": "error", "at": 1, "msg": "x", "_bt0": 3})
    assert line == {"ev": "error", "state": "error", "at": 1, "msg": "x"}
    assert state.public({"ev": "state", "state": "ask", "at": 1})["ev"] == "state"


def test_unreadable_state_file_reads_as_idle():
    os.makedirs(state.STATE_DIR, exist_ok=True)
    with open(state.STATE, "w") as f:
        f.write('{"state": "recor')                                          # torn by hand
    assert state.read()["state"] == "idle"
    with open(state.STATE, "w") as f:
        json.dump({"state": "bogus", "at": 5}, f)
    assert state.read()["state"] == "idle"


# ── TTL ────────────────────────────────────────────────────────────────────
def _session(name, files):
    p = os.path.join(state.CALLS, name)
    os.makedirs(p, exist_ok=True)
    for f in files:
        full = os.path.join(p, f)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        open(full, "w").close()
    return p


def test_ttl_selects_only_old_inactive_sessions():
    now = time.mktime(time.strptime("2026-09-25_12-00-00", prune.NAME_FMT))
    day = 86400
    s = [("/c/old", now - 8 * day), ("/c/edge", now - 7 * day + 60), ("/c/new", now - day),
         ("/c/active", now - 30 * day), ("/c/unknown", None)]
    assert prune.expired(s, now, active="/c/active") == ["/c/old"]


def test_prune_removes_audio_and_checkpoints_but_keeps_transcripts():
    now = time.mktime(time.strptime("2026-09-25_12-00-00", prune.NAME_FMT))
    old = _session("2026-09-10_10-00-00", ["mic.opus", "desktop.opus", "transcript.md",
                                           "transcript.json", "session.json", ".transcribe/asr.jsonl"])
    act = _session("2026-09-01_10-00-00", ["mic.opus", "desktop.opus"])
    new = _session("2026-09-24_10-00-00", ["mic.opus", "desktop.opus"])
    touched = prune.prune(active=act, now=now)
    assert touched == [old]
    assert sorted(os.listdir(old)) == ["session.json", "transcript.json", "transcript.md"]
    assert sorted(os.listdir(act)) == ["desktop.opus", "mic.opus"]
    assert sorted(os.listdir(new)) == ["desktop.opus", "mic.opus"]
    assert prune.session_time("/x/2026-09-10_10-00-00") == time.mktime(
        time.strptime("2026-09-10_10-00-00", prune.NAME_FMT))


# ── stop reasons, gaps ─────────────────────────────────────────────────────
def test_stop_reason_marker_wins_and_bare_exits_are_crashes():
    assert capture.stop_reason("user", "timeout") == "user"     # stop hit TimeoutStopSec → SIGKILL
    assert capture.stop_reason("suspend", "success") == "suspend"
    assert capture.stop_reason(None, "timeout") == "max_duration"  # RuntimeMaxSec
    assert capture.stop_reason(None, "success") == "recorder_died"  # gst exited on its own
    assert capture.stop_reason(None, "signal") == "recorder_died"
    assert capture.stop_reason(None, None) == "recorder_died"       # reboot / power loss


def test_first_stop_reason_wins():
    s = _session("marker", [])
    assert capture.write_marker(s, "suspend") and not capture.write_marker(s, "user")
    assert capture.read_marker(s) == "suspend"


def test_gaps_ignore_jitter_and_are_recorded_once():
    rec = {"since": 1000.0, "_bt0": 50.0, "_gaps": []}
    assert capture.add_gap(rec, 51.0, 1100.0) is None                 # 1 s: jitter
    g = capture.add_gap(rec, 80.0, 1100.0)["_gaps"]
    assert g == [{"at_s": 70.0, "dur_s": 30.0, "why": "suspend"}]    # 100 s wall − 30 s asleep
    rec["_gaps"] = g
    assert capture.add_gap(rec, 80.5, 1100.5) is None                 # same sleep again: no-op
    g2 = capture.add_gap(rec, 90.0, 1200.0)["_gaps"]                  # a second, later sleep
    assert g2[1] == {"at_s": 160.0, "dur_s": 10.0, "why": "suspend"}
    assert capture.add_gap({"since": 1.0, "_gaps": []}, 99.0, 2.0) is None   # no baseline


# ── repair ─────────────────────────────────────────────────────────────────
def test_dead_transcriber_becomes_a_retryable_error_but_not_while_cancelling():
    with fake_systemd():
        _dead_transcriber_checks()


def _dead_transcriber_checks():
    dead = 2 ** 22 + 12345                                              # above pid_max: never alive
    _reset()
    rec = state.set("transcribing", "/s/1", since=1.0, worker_pid=dead, cancel_at=time.time())
    assert kozyrec.reconcile(rec)["state"] == "transcribing"
    rec = state.set("transcribing", "/s/1", since=1.0, worker_pid=dead)
    out = kozyrec.reconcile(rec)
    assert out["state"] == "error" and out["retry"] is True and out["during"] == "transcribe"
    assert out["session"] == "/s/1"


def test_stale_starting_without_a_recorder_goes_idle_and_removes_the_empty_dir():
    with fake_systemd():
        _stale_starting_checks()


def _stale_starting_checks():
    s = _session("2026-09-25_09-00-00", [])
    _reset()
    state.set("starting", s)
    fresh = state.read()
    assert kozyrec.reconcile(fresh, now=fresh["at"] + 1)["state"] == "starting"   # within grace
    out = kozyrec.reconcile(fresh, now=fresh["at"] + kozyrec.START_GRACE_S + 1)
    assert out["state"] == "idle" and not os.path.exists(s)


def test_discard_deletes_only_inside_calls_and_refuses_while_live():
    with contextlib.redirect_stdout(io.StringIO()), fake_systemd():
        _discard_checks()


def _discard_checks():
    s = _session("2026-09-25_08-00-00", ["mic.opus"])
    _reset()
    state.set("recording", s)
    assert kozyrec.cmd_discard() == 1 and os.path.exists(s)
    state.set("ask", s)
    assert kozyrec.cmd_discard() == 0 and not os.path.exists(s) and state.read()["state"] == "idle"
    outside = tempfile.mkdtemp(prefix="kzrcap-outside-")
    state.set("error", outside, msg="x")
    kozyrec.cmd_discard()
    assert os.path.isdir(outside) and state.read()["state"] == "idle"


def test_unanswered_systemctl_never_ends_a_live_recording():
    s = _session("2026-09-25_07-00-00", ["mic.opus", "desktop.opus"])
    _reset()
    rec = state.set("recording", s, since=time.time() - 60, aec=True)
    with fake_systemd(broken=True) as stops:
        assert capture.unit_state(capture.REC_UNIT) is None
        assert kozyrec.reconcile(rec, now=rec["at"] + 3600)["state"] == "recording"
        st = state.set("starting", s, expect="recording")
        assert kozyrec.reconcile(st, now=st["at"] + 3600)["state"] == "starting"
        assert stops == [] and os.path.isdir(s)
        state.set("idle")
        real_mic, capture.find_mic = capture.find_mic, lambda: "alsa_input.test"
        try:
            with contextlib.redirect_stdout(io.StringIO()) as out:
                assert kozyrec.cmd_start() == 1                  # refuses, doesn't guess
        finally:
            capture.find_mic = real_mic
        line = json.loads(out.getvalue())
        assert line["ev"] == "error" and "at" not in line
    assert stops == [] and state.read()["state"] == "idle"
    # A definite answer still repairs: a collected unit (rc 4) is gone.
    with fake_systemd() as stops:
        assert capture.unit_live(capture.REC_UNIT) is False
        assert capture.unit_state("kozyrec-aec") == "inactive"


def test_stuck_stopping_gets_its_stop_job_again():
    s = _session("2026-09-25_06-00-00", ["mic.opus", "desktop.opus"])
    _reset()
    rec = state.set("stopping", s, since=time.time() - 60)
    live = {capture.REC_UNIT: "active", capture.AEC_UNIT: "active"}
    with fake_systemd(live) as stops:
        kozyrec.reconcile(rec, now=rec["at"] + 1)                # the stop may just be slow
        assert stops == []
        assert kozyrec.reconcile(rec, now=rec["at"] + kozyrec.STOP_REISSUE_S + 1)["state"] == "stopping"
        assert stops == [capture.REC_UNIT]
    with fake_systemd({capture.REC_UNIT: "deactivating"}) as stops:
        kozyrec.reconcile(rec, now=rec["at"] + 60)               # a stop job is running
        assert stops == []


def test_a_refused_stop_keeps_recording_and_leaves_no_stop_reason():
    s = _session("2026-09-25_05-00-00", ["mic.opus", "desktop.opus"])
    _reset()
    state.set("recording", s, since=time.time() - 60)
    with fake_systemd({capture.REC_UNIT: "active"}, stop_ok=False):
        rec, err = kozyrec.do_stop("user", wait=False)
    assert err and rec["state"] == "recording"
    assert capture.read_marker(s) is None                        # a later max_duration isn't "user"


def test_transient_errors_carry_no_at():
    assert "at" not in state.error_line("No microphone found", during="record")


# ── detection ──────────────────────────────────────────────────────────────
def test_mic_prefers_the_internal_alsa_capture():
    src = lambda name, **p: {"name": name, "properties": {"media.class": "Audio/Source", **p}}
    sources = [src("x.monitor", **{"device.class": "monitor", "device.api": "alsa"}),
               src("bluez_input.A4", **{"device.api": "bluez5"}),
               src("usb_mic", **{"device.api": "alsa", "device.form_factor": "webcam"}),
               src("builtin", **{"device.api": "alsa", "device.form_factor": "internal"})]
    assert capture.pick_mic(sources) == "builtin"
    assert capture.pick_mic(sources[:3]) == "usb_mic"
    assert capture.pick_mic(sources[:2]) is None


def test_call_apps_are_recognised_by_binary_or_name():
    st = lambda **p: {"properties": p}
    streams = [st(**{"application.process.binary": "brave", "application.name": "Brave"}),
               st(**{"application.process.binary": "ZoomWebviewHost"}),
               st(**{"application.name": "Telegram Desktop", "application.process.binary": "Telegram"}),
               st(**{"application.process.binary": "gst-launch-1.0", "application.name": "kozyrec"}),
               st(**{"application.process.binary": "mpv"})]
    assert capture.call_apps(streams) == ["brave", "telegram", "zoom"]


# ── Ogg ────────────────────────────────────────────────────────────────────
def _record(path, secs, sig):
    p = subprocess.Popen(["gst-launch-1.0", "-q", "-e", "audiotestsrc", "is-live=true", "wave=sine",
                          "!", "audio/x-raw,rate=16000,channels=1", "!", "opusenc", "bitrate=24000",
                          "!", "oggmux", "max-page-delay=500000000",
                          "!", "filesink", f"location={path}", "buffer-mode=unbuffered"])
    time.sleep(secs)
    p.send_signal(sig)
    p.wait(10)


def test_ogg_duration_survives_a_killed_writer():
    clean, killed = os.path.join(ROOT, "clean.opus"), os.path.join(ROOT, "killed.opus")
    _record(clean, 2.5, signal.SIGINT)
    _record(killed, 2.5, signal.SIGKILL)
    d, eos = capture.ogg_opus_info(clean)
    assert eos and 2.0 < d < 3.2, (d, eos)
    d, eos = capture.ogg_opus_info(killed)
    assert not eos and 1.5 < d < 3.2, (d, eos)                         # ≤ 0.5 s of pages lost
    assert capture.ogg_opus_info(os.path.join(ROOT, "missing.opus")) == (0.0, False)


if __name__ == "__main__":
    ran = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            ran += 1
    import shutil
    shutil.rmtree(ROOT, ignore_errors=True)
    print(f"ok — {ran} checks passed")
