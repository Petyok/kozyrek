"""Capture: the internal mic (through an on-demand WebRTC echo canceller) and
the default sink's monitor, recorded side by side into two Ogg Opus files.

Nothing here stays resident. Two transient systemd --user units do the work:

  kozyrec-aec  `pipewire -c <runtime>/aec.conf` — a PipeWire *client* hosting
               module-echo-cancel in monitor mode (reference = monitor of the
               default sink, follows it). Its source node `kozyrec_mic_aec` has
               priority.session=1, so it never becomes anyone's default mic;
               Meet/Zoom/Telegram keep their own mic path and their own AEC.
  kozyrec-rec  one `gst-launch-1.0 -e` with two pipewiresrc branches on one
               pipeline clock. KillSignal=SIGINT → EOS → both files finalised.
               RuntimeMaxSec caps the length. ExecStopPost runs
               `kozyrec.py _finalize`, which writes session.json and moves the
               state to ask whatever way gst ended.

The unit name is the lock: a second systemd-run --unit=kozyrec-rec fails.
Stdlib only.
"""
import json, os, struct, subprocess, time

import state

REC_UNIT = "kozyrec-rec"
AEC_UNIT = "kozyrec-aec"
AEC_NODE = "kozyrec_mic_aec"
MAX_MIN = 90
BYTES_PER_S = 7000                  # 32 + 24 kbps Opus + Ogg overhead ≈ 25 MB/h
LOW_DISK_STOP = 100 * 2**20         # auto-stop below this
LOW_DISK_WARN = 500 * 2**20
PREFLIGHT_SPARE = 200 * 2**20
SUSPEND_STOP_S = 60                 # a suspend this long ends the recording
GAP_MIN_S = 2                       # shorter clock jumps are jitter
NO_CALL_AFTER_S = 120
CALL_APPS = {"brave": "brave", "chrom": "chrome", "zoom": "zoom", "telegram": "telegram"}
RUN_DIR = os.path.join(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}", "kozyrec")
AEC_LIB = "/usr/lib/spa-0.2/aec/libspa-aec-webrtc.so"


class CaptureError(Exception):
    pass


def run(cmd, timeout=5):
    """-> (rc, stdout). Never raises for a missing binary or a hang."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)


# ── PipeWire facts via pactl (11 ms per call) ──────────────────────────────
def _pactl_json(*what):
    rc, out = run(["pactl", "-f", "json", "list", *what])
    try:
        return json.loads(out) if rc == 0 else []
    except ValueError:
        return []


def pick_mic(sources):
    """The built-in mic: an ALSA capture node with form factor internal.
    Pinned by name, so a BT headset connecting never moves the recording."""
    best = None
    for s in sources:
        p = s.get("properties") or {}
        if p.get("media.class") != "Audio/Source" or p.get("device.class") == "monitor":
            continue
        if p.get("device.api") != "alsa":
            continue
        if p.get("device.form_factor") == "internal":
            return s.get("name")
        best = best or s.get("name")
    return best


def find_mic():
    return pick_mic(_pactl_json("sources"))


def mic_state(name):
    """ok | muted | missing — the OS-level mute (an in-call mute in Meet/Zoom
    does NOT mute the OS mic, so the recording still hears you)."""
    rc, out = run(["pactl", "get-source-mute", name])
    if rc != 0:
        return "missing"
    return "muted" if "yes" in out else "ok"


def call_apps(streams):
    """Which call apps currently play or capture audio -> sorted names."""
    found = set()
    for s in streams:
        p = s.get("properties") or {}
        hay = " ".join(str(p.get(k, "")) for k in
                       ("application.process.binary", "application.name")).lower()
        for needle, label in CALL_APPS.items():
            if needle in hay:
                found.add(label)
    return sorted(found)


def live_calls():
    return call_apps(_pactl_json("sink-inputs") + _pactl_json("source-outputs"))


def default_sink():
    rc, out = run(["pactl", "get-default-sink"])
    return out.strip() if rc == 0 else None


def has_source(name):
    rc, out = run(["pactl", "list", "short", "sources"])
    return rc == 0 and any(line.split("\t")[1:2] == [name] for line in out.splitlines())


def disk_free(path):
    try:
        st = os.statvfs(path)
        return st.f_bavail * st.f_frsize
    except OSError:
        return 0


# ── systemd units ──────────────────────────────────────────────────────────
UNIT_STATES = ("active", "reloading", "refreshing", "activating", "deactivating",
               "inactive", "failed", "maintenance")
LIVE_STATES = ("active", "reloading", "refreshing", "activating", "deactivating")


def unit_state(unit):
    """active | activating | deactivating | inactive | failed | … — or None
    when systemd could not be asked (timeout, bus error, odd output). Only
    rc 0 (active), rc 3 (not active) and rc 4 (no such unit — a collected
    transient unit prints "inactive" with rc 4 on systemd 261) with one known
    word are answers; anything else is unknown, and unknown must never
    trigger a repair."""
    rc, out = run(["systemctl", "--user", "is-active", unit])
    word = out.strip()
    if rc in (0, 3, 4) and word in UNIT_STATES:
        return word
    return None


def unit_live(unit):
    """True | False | None (unknown)."""
    s = unit_state(unit)
    return None if s is None else s in LIVE_STATES


def stop_unit(unit, block=False):
    """Queue (or, with block, run) a stop job. -> True if systemctl took it."""
    rc, _ = run(["systemctl", "--user", "stop"] + ([] if block else ["--no-block"])
                + [unit + ".service"], timeout=15)
    return rc == 0


def aec_conf(mic):
    return f"""# written by kozyrec for one recording; removed when it ends
context.properties = {{ log.level = 2 }}
context.spa-libs = {{
    audio.convert.* = audioconvert/libspa-audioconvert
    support.*       = support/libspa-support
}}
context.modules = [
    {{ name = libpipewire-module-rt args = {{ }} flags = [ ifexists nofail ] }}
    {{ name = libpipewire-module-protocol-native }}
    {{ name = libpipewire-module-client-node }}
    {{ name = libpipewire-module-adapter }}
    {{ name = libpipewire-module-echo-cancel
      args = {{
          library.name   = aec/libspa-aec-webrtc
          monitor.mode   = true
          audio.rate     = 16000
          audio.channels = 1
          audio.position = [ MONO ]
          node.latency   = 1024/48000
          aec.args = {{
              webrtc.high_pass_filter      = true
              webrtc.noise_suppression     = false
              webrtc.transient_suppression = false
              webrtc.voice_detection       = false
              webrtc.gain_control          = false
          }}
          capture.props = {{
              node.name          = "kozyrec_aec_capture"
              target.object      = "{mic}"
              node.dont-fallback = true
          }}
          source.props = {{
              node.name        = "{AEC_NODE}"
              node.description = "kozyrec call recorder (internal)"
              priority.session = 1
              priority.driver  = 1
          }}
          sink.props = {{
              node.name = "kozyrec_aec_ref"
          }}
      }}
    }}
]
"""


def _branch(name, props, target, bitrate, out):
    # min-buffers=32 (PipeWire's audioconvert maximum): pipewiresrc waits on the
    # clock before every buffer, and those waits can run 70-150 ms when a call app
    # pulls the graph down to 256-sample cycles. With the default 2 buffers PipeWire
    # dropped ~3 of 4 cycles ("out of buffers"), leaving 21 ms fragments that
    # audiorate padded into a "complete" silent track. ponytail: 32 cycles is 170 ms
    # at quantum 256; longer waits drop again; the real fix is removing the clock wait.
    src = ["pipewiresrc", f"name={name}", "client-name=kozyrec",
           "do-timestamp=true", "provide-clock=false", "min-buffers=32"]
    if target:
        src.append(f"target-object={target}")
    src.append("stream-properties=props," + ",".join(f"{k}=(string){v}" for k, v in props))
    # Caps right after the source: PipeWire's adapter downmixes and resamples.
    # audiorate fills any hole (sink switch, AEC restart) with silence from the
    # pipeline start on, so both files share one timeline.
    return src + [
        "!", "audio/x-raw,rate=16000,channels=1",
        "!", "audiorate", "tolerance=100000000", "skip-to-first=false",
        "!", "audioconvert", "!", "audio/x-raw,format=S16LE",
        "!", "queue", "max-size-time=3000000000", "max-size-buffers=0", "max-size-bytes=0",
        "!", "opusenc", f"bitrate={bitrate}", "audio-type=voice", "frame-size=20",
        "complexity=5",                       # −0.8 % CPU per branch, inaudible for voice
        "!", "oggmux", "max-page-delay=500000000",
        "!", "filesink", f"location={out}", "buffer-mode=unbuffered",
    ]


def pipeline(session, mic, aec):
    """gst-launch argv. aec=False records the raw internal mic instead."""
    lat = ("node.latency", "1024/48000")
    mic_props = [("node.name", "kozyrec_rec_mic"), ("node.description", "kozyrec-rec-mic"), lat]
    if not aec:
        # A target equal to the default source would count as "follow default"
        # and could move us onto a BT mic later.
        mic_props.append(("node.dont-reconnect", "true"))
    # With AEC, no dont-fallback: if the canceller dies, WirePlumber errors a
    # dont-fallback stream out ("defined target not found") and gst exits,
    # ending the recording. Without it the branch just sits unlinked until the
    # restarted canceller's ExecStartPost relinks it (see start_aec).
    desk_props = [("node.name", "kozyrec_rec_desktop"), ("node.description", "kozyrec-rec-desktop"),
                  ("stream.capture.sink", "true"), lat]
    return (["gst-launch-1.0", "-e", "-q"]
            + _branch("mic", mic_props, AEC_NODE if aec else mic, 32000,
                      os.path.join(session, "mic.opus"))
            + _branch("desk", desk_props, None, 24000, os.path.join(session, "desktop.opus")))


def _passthrough_env():
    return [a for k in ("KOZYREC_STATE_DIR", "KOZYREC_CALLS") if os.environ.get(k)
            for a in ("-E", f"{k}={os.environ[k]}")]


def start_aec(mic, wait_s=3.0):
    """Launch the echo canceller and wait for its source. -> True if usable."""
    os.makedirs(RUN_DIR, exist_ok=True)
    conf = os.path.join(RUN_DIR, "aec.conf")
    with open(conf, "w") as f:
        f.write(aec_conf(mic))
    # pipewiresrc always connects with PW_STREAM_FLAG_DONT_RECONNECT, so when
    # a crashed canceller comes back (Restart=on-failure) nothing relinks the
    # recorder's mic branch. ExecStartPost does it; on the first start the
    # recorder isn't running yet and this exits at once. audiorate fills the
    # outage with silence, so the tracks stay aligned.
    relink = ("systemctl --user is-active -q " + REC_UNIT + " || exit 0; "
              "for i in $$(seq 50); do pw-link " + AEC_NODE + ":capture_MONO "
              "kozyrec_rec_mic:input_MONO 2>/dev/null && exit 0; sleep 0.1; done; exit 0")
    rc, _ = run(["systemd-run", "--user", f"--unit={AEC_UNIT}", "--collect", "--quiet",
                 "--service-type=exec", "-p", "Description=kozyrek recorder: echo canceller",
                 "-p", "Restart=on-failure", "-p", "RestartSec=1",
                 "-p", "StartLimitIntervalSec=60", "-p", "StartLimitBurst=3",
                 "-p", "TimeoutStopSec=3", "-p", f"ExecStartPost=/bin/sh -c '{relink}'",
                 "/usr/bin/pipewire", "-c", conf], timeout=10)
    if rc != 0:
        return False
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if has_source(AEC_NODE):
            return True
        time.sleep(0.05)
    stop_unit(AEC_UNIT)
    return False


def start_recorder(session, mic, aec, script):
    rc, out = run(["systemd-run", "--user", f"--unit={REC_UNIT}", "--collect", "--quiet",
                   "--service-type=exec", "-p", "Description=kozyrek call recorder",
                   "-p", "KillSignal=SIGINT", "-p", "TimeoutStopSec=8",
                   "-p", f"RuntimeMaxSec={MAX_MIN * 60}",
                   "-p", f"ExecStopPost=/usr/bin/python3 {script} _finalize",
                   "-E", f"KOZYREC_SESSION={session}", *_passthrough_env(),
                   *pipeline(session, mic, aec)], timeout=10)
    if rc != 0:
        raise CaptureError("could not launch the recorder (is one already running?)")


def ogg_pages(path):
    try:
        with open(path, "rb") as f:
            return f.read(65536).count(b"OggS")
    except OSError:
        return 0


def wait_audio(session, timeout=4.0):
    """True once both files hold an audio page past the two Opus header pages."""
    deadline = time.monotonic() + timeout
    files = [os.path.join(session, n) for n in ("mic.opus", "desktop.opus")]
    while time.monotonic() < deadline:
        if all(ogg_pages(p) >= 3 for p in files):
            return True
        if unit_live(REC_UNIT) is False:
            return False
        time.sleep(0.1)
    return False


# ── finalize ───────────────────────────────────────────────────────────────
def ogg_opus_info(path):
    """-> (duration_s, ended_cleanly). Reads the last granule position, so it
    works on a file whose writer was killed (no EOS page, no index)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(512)
            f.seek(max(0, size - 65536))
            tail = f.read()
    except OSError:
        return 0.0, False
    i = head.find(b"OpusHead")
    pre_skip = struct.unpack_from("<H", head, i + 10)[0] if i >= 0 and len(head) >= i + 12 else 0
    pos = len(tail)
    while True:
        pos = tail.rfind(b"OggS", 0, pos)
        if pos < 0 or pos + 14 > len(tail):
            if pos < 0:
                return 0.0, False
            continue
        flags = tail[pos + 5]
        granule = struct.unpack_from("<q", tail, pos + 6)[0]
        if granule >= 0:
            return max(0.0, (granule - pre_skip) / 48000.0), bool(flags & 4)


def stop_reason(marker, service_result):
    """Why the recorder ended. `stop` always leaves a marker; without one, a
    systemd timeout is RuntimeMaxSec and anything else is a crash."""
    if marker:
        return marker
    if service_result == "timeout":
        return "max_duration"
    return "recorder_died"


def read_marker(session):
    try:
        with open(os.path.join(session, ".stop_reason")) as f:
            return f.read().strip() or None
    except OSError:
        return None


def write_marker(session, reason):
    """First reason wins (O_EXCL), so concurrent auto-stops can't relabel."""
    try:
        fd = os.open(os.path.join(session, ".stop_reason"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        return False
    except OSError:
        return False
    with os.fdopen(fd, "w") as f:
        f.write(reason)
    return True


def clear_marker(session):
    try:
        os.unlink(os.path.join(session, ".stop_reason"))
    except OSError:
        pass


def settle(rec, service_result, expect=("recording", "stopping")):
    """Recording is over: write session.json and move to ask — or, with no
    audio at all, to error and remove the empty session. Idempotent through
    the CAS on `expect`."""
    session = rec["session"]
    mic_s, mic_eos = ogg_opus_info(os.path.join(session, "mic.opus"))
    desk_s, desk_eos = ogg_opus_info(os.path.join(session, "desktop.opus"))
    duration = max(mic_s, desk_s)
    reason = stop_reason(read_marker(session), service_result)
    if duration < 1.0:
        new = state.set("error", expect=expect, session=session, msg="The recorder captured no audio",
                        retry=False, during="record")
        if new is not None:
            state.rmtree(session, ignore_errors=True)
        return new
    since = float(rec.get("since") or time.time())
    gaps = rec.get("_gaps") or []
    meta = {
        "v": 1, "t0_unix": since, "end_unix": time.time(), "duration_s": round(duration, 3),
        "aec": bool(rec.get("aec")), "aec_ref": "desktop",
        "tracks": {
            "mic": {"file": "mic.opus", "start_offset_s": 0.0, "device": rec.get("device"),
                    "aec": bool(rec.get("aec")), "duration_s": round(mic_s, 3)},
            "desktop": {"file": "desktop.opus", "start_offset_s": 0.0,
                        "sinks": rec.get("_sinks") or [], "duration_s": round(desk_s, 3)},
        },
        "gaps": gaps, "stop_reason": reason, "complete": bool(mic_eos and desk_eos),
        "warn": "recorder_died" if reason == "recorder_died" else None,
    }
    state.write_json(os.path.join(session, "session.json"), meta)
    new = state.set("ask", session, expect=expect, **state.ask_fields(session))
    if new is not None:
        clear_marker(session)
    return new


# ── clocks: suspend shows up as BOOTTIME running ahead of MONOTONIC ────────
def boot_offset():
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.clock_gettime(time.CLOCK_MONOTONIC)


def add_gap(rec, bt_now, now):
    """A sleep not yet in rec["_gaps"] -> {"_gaps": [...]} with it appended,
    else None. The audio stays contiguous (MONOTONIC, which drives the
    pipeline clock, stops during suspend), so a gap is a *point* in audio
    time: at_s = wall time since start minus every sleep."""
    bt0 = rec.get("_bt0")
    if bt0 is None:
        return None
    gaps = rec.get("_gaps") or []
    slept = sum(g.get("dur_s", 0) for g in gaps)
    d = bt_now - float(bt0) - slept
    if d < GAP_MIN_S:
        return None
    at_s = now - float(rec.get("since") or now) - slept - d
    return {"_gaps": gaps + [{"at_s": round(max(0.0, at_s), 1), "dur_s": round(d, 1),
                              "why": "suspend"}]}


def pick_warn(mic, aec_wanted, aec_alive, calls, elapsed, free):
    if free < LOW_DISK_WARN:
        return "low_disk"
    if mic in ("muted", "missing"):
        return "mic_muted"
    if aec_wanted is False or (aec_wanted and not aec_alive):
        return "aec_failed"
    if not calls and elapsed > NO_CALL_AFTER_S:
        return "no_call_audio"
    return None


def live_fields(rec):
    """The recording line's live part (~35 ms of pactl)."""
    free = disk_free(rec.get("session") or state.CALLS)
    mic = mic_state(rec.get("device") or "")
    calls = live_calls()
    # Unknown (systemd didn't answer) is not evidence of a dead canceller.
    aec_alive = unit_state(AEC_UNIT) in ("active", None) if rec.get("aec") else False
    return {"mic": mic, "call": calls, "disk_left_min": int(free / BYTES_PER_S / 60),
            "max_min": MAX_MIN,
            "warn": pick_warn(mic, rec.get("aec"), aec_alive, calls,
                              time.time() - float(rec.get("since") or time.time()), free),
            "_free": free}
