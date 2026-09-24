// Pure helpers shared by the QML modules. No QML, no pragma, no imports: this
// file is also loaded by test_parse.mjs under node.

// `ip monitor link` prints "N: name: <FLAGS> ..." and "Deleted N: name: ...".
// Names may carry "@ifN" for veth pairs.
function ipMonitor(line) {
    var m = /^(Deleted )?\d+: ([^:@\s]+)[@:]/.exec(line);
    return m ? { name: m[2], deleted: !!m[1] } : null;
}

function stepVolume(cur, arg, max) {
    var v = cur + parseInt(arg, 10) / 100;
    v = Math.min(max, Math.max(0, v));
    return Math.round(v * 100) / 100;
}

function brightnessArg(arg) {
    var n = Math.abs(parseInt(arg, 10));
    return n + "%" + (arg[0] === "-" ? "-" : "+");
}

// "intel_backlight,backlight,2499,90%,2777" → 0.9 (the rounded percent field,
// which is what brightnessctl itself shows the user)
function brightnessctl(text) {
    var f = text.trim().split(",");
    if (f.length < 5) return NaN;
    var pct = parseInt(f[3], 10);
    return isNaN(pct) ? NaN : pct / 100;
}

// Nerd Font (Material) glyphs; the bar's icon font is "Symbols Nerd Font".
function wifiGlyph(strength, connected) {
    if (!connected || strength === null) return "\u{F092D}";          // wifi-strength-off
    if (strength >= 0.75) return "\u{F0928}";                          // 4 bars
    if (strength >= 0.5)  return "\u{F0925}";                          // 3
    if (strength >= 0.25) return "\u{F0922}";                          // 2
    return "\u{F091F}";                                                // 1
}

function batteryGlyph(pct, charging) {
    if (charging) return "\u{F0084}";                                  // battery-charging
    var steps = ["\u{F008E}", "\u{F007A}", "\u{F007B}", "\u{F007C}", "\u{F007D}",
                 "\u{F007E}", "\u{F007F}", "\u{F0080}", "\u{F0081}", "\u{F0082}", "\u{F0079}"];
    return steps[Math.max(0, Math.min(10, Math.round(pct / 10)))];
}

function wsSort(list) {
    return list.filter(function (w) { return w.id > 0; })
               .sort(function (a, b) { return a.id - b.id; });
}

// ── call recorder (modules/Rec.qml) ─────────────────────────────────────────
// kozyrec.py prints one JSON object per line; recReduce folds them into the
// pill's model. "local" events are the pill's own optimistic states while a
// verb is in flight (starting/stopping/transcribing).
var REC_STATES = ["idle", "starting", "recording", "stopping", "ask", "transcribing", "done", "error"];

function recInit() {
    return { state: "idle", at: -1, session: "", since: 0, frac: -1, stage: "", eta: null, etaAt: 0,
             dur: 0, transcript: "", speakers: 0, warn: null, stopReason: "", msg: "", retry: false, during: "",
             mic: "", aec: null, call: [], diskMin: null, maxMin: null };
}

// One stdout line → event object, or null for anything that is not {"ev": "..."}.
function recParse(line) {
    var e;
    try { e = JSON.parse(line); } catch (x) { return null; }
    return (e && typeof e === "object" && !Array.isArray(e) && typeof e.ev === "string") ? e : null;
}

function _num(v, dflt) { return (typeof v === "number" && isFinite(v)) ? v : dflt; }

// Returns the SAME object when the event changes nothing (callers compare ===).
// State lines older than the last one seen (`at`) are dropped: the follower and
// a verb may both print, and a stale line must not undo a fresher one.
function recReduce(m, e, now) {
    var n = Object.assign({}, m);
    if (e.ev === "state" || e.ev === "local") {
        if (REC_STATES.indexOf(e.state) < 0) return m;
        if (e.ev === "state" && typeof e.at === "number") { if (e.at < m.at) return m; n.at = e.at; }
        var same = !e.session || e.session === m.session;
        n.state = e.state;
        n.session = e.session || (e.state === "idle" ? "" : m.session);
        n.since = _num(e.since, e.state === m.state ? m.since : now);
        n.dur = e.duration_s || (same && e.state !== "idle" ? m.dur : 0);
        if (e.state === "transcribing") {
            if (m.state !== "transcribing") { n.frac = 0; n.stage = "prepare"; n.eta = null; }
        } else { n.frac = -1; n.stage = ""; n.eta = null; }
        n.transcript = e.transcript || "";
        n.speakers = e.speakers || 0;
        n.stopReason = e.stop_reason || "";
        n.warn = e.warn || null;
        if (e.state === "recording") {
            n.mic = e.mic || m.mic;
            n.aec = typeof e.aec === "boolean" ? e.aec : m.aec;
            n.call = Array.isArray(e.call) ? e.call : m.call;
            n.diskMin = _num(e.disk_left_min, m.diskMin);
            n.maxMin = _num(e.max_min, m.maxMin);
        }
        if (e.state !== "error") { n.msg = ""; n.retry = false; n.during = ""; }
        return n;
    }
    if (e.ev === "progress") {
        if (m.state !== "transcribing" || (e.session && m.session && e.session !== m.session)) return m;
        var f = Number(e.frac);
        if (isFinite(f)) n.frac = Math.max(m.frac, Math.min(1, Math.max(0, f)));    // monotonic, clamped
        n.stage = e.stage || m.stage;
        n.eta = _num(e.eta_s, null);
        n.etaAt = now;
        if (e.audio_total_s) n.dur = e.audio_total_s;
        return n;
    }
    if (e.ev === "error") {
        // Only persisted errors carry `at`; a transient one (a verb that failed) must
        // not advance the stamp, or the replayed persisted truth would be dropped.
        if (typeof e.at === "number") { if (e.at < m.at) return m; n.at = e.at; }
        n.state = "error"; n.msg = e.msg || "unknown error"; n.retry = !!e.retry;
        n.session = e.session || m.session;
        n.during = e.during || (m.state === "transcribing" ? "transcribe" : (m.state === "idle" ? "" : "record"));
        n.frac = -1; n.eta = null;
        return n;
    }
    return m;
}

// States in which something is running and the follower must be attached.
function recLive(s) { return s === "starting" || s === "recording" || s === "stopping" || s === "transcribing"; }

// Mouse: button "left" | "right" | "middle" → "start" | "stop" | "popup" | "open" | "folder" | "none"
function recClick(s, button) {
    if (button === "middle" || s === "starting" || s === "stopping") return "none";
    if (button === "right") return (s === "idle" || s === "done") ? "folder" : "popup";
    return ({ idle: "start", recording: "stop", ask: "popup", transcribing: "popup", done: "open", error: "popup" })[s] || "none";
}
// The hotkey is the one-button meaning: never opens a file.
function recToggle(s) {
    return ({ idle: "start", done: "start", error: "start", recording: "stop", ask: "popup", transcribing: "popup" })[s] || "none";
}

// 7 → "00:07", 754 → "12:34", 3723 → "1:02:03"
function fmtClock(sec) {
    var s = Math.max(0, Math.floor(Number(sec) || 0)), h = Math.floor(s / 3600), mm = Math.floor(s / 60) % 60, ss = s % 60;
    function p(x) { return (x < 10 ? "0" : "") + x; }
    return h > 0 ? h + ":" + p(mm) + ":" + p(ss) : p(mm) + ":" + p(ss);
}
function fmtEta(sec) {
    if (sec === null || sec === undefined || !isFinite(sec)) return "estimating…";
    if (sec < 60) return "< 1 min";
    var min = Math.ceil(sec / 60);
    return min < 60 ? "~" + min + " min" : "~" + Math.floor(min / 60) + " h " + ("0" + (min % 60)).slice(-2) + " min";
}
// ETA counted down locally from the last progress line.
function recEta(m, now) { return m.eta === null ? fmtEta(null) : fmtEta(Math.max(0, m.eta - (now - m.etaAt))); }

function recPillText(m, now) {
    switch (m.state) {
    case "starting":     return "…";
    case "recording":    return fmtClock(now - m.since);
    case "stopping":     return "saving…";
    case "ask":          return "?";
    case "transcribing": return Math.round(Math.max(0, m.frac) * 100) + "%";
    case "done":         return "ready";
    default:             return "";
    }
}
function recGlyph(s) {
    return ({ idle: "\u{F0EC3}", starting: "\u{F0EC3}", ask: "\u{F113D}", transcribing: "\u{F113D}",
              done: "\u{F05E0}", error: "\u{F0028}" })[s] || "";                 // recording/stopping: the dot
}
function recStageLabel(st) {
    return ({ prepare: "Preparing audio", diarize: "Finding speakers", asr: "Transcribing speech", merge: "Writing transcript" })[st] || "Working";
}
function recErrorTitle(m) {
    return m.during === "transcribe" ? "Transcription failed" : m.during === "record" ? "Recording failed" : "Call recorder error";
}
function recStopText(reason) {
    return ({ max_duration: "Reached the 1.5 h limit", low_disk: "Disk is almost full", suspend: "Laptop went to sleep",
              recorder_died: "The recorder crashed; the audio so far is saved" })[reason] || "Stopped";
}
function recWarnText(m) {
    return ({ mic_muted: "Microphone is muted",
              low_disk: "Disk almost full" + (m.diskMin !== null ? ", ~" + m.diskMin + " min left" : ""),
              aec_failed: "Echo cancellation is off",
              no_call_audio: "No call audio is being captured" })[m.warn] || String(m.warn);
}
// "/…/Calls/2026-09-25_14-03-11" → "2026-09-25 14:03"
function recSessionLabel(dir) {
    var b = /(\d{4}-\d\d-\d\d)_(\d\d)-(\d\d)(?:-\d\d)?\/?$/.exec(dir || "");
    return b ? b[1] + " " + b[2] + ":" + b[3] : ((dir || "").split("/").filter(Boolean).pop() || "");
}
function recMinutes(sec) { return sec < 60 ? "< 1 min" : Math.round(sec / 60) + " min"; }
function recAskLine(m) {
    var s = [fmtClock(m.dur) + " recorded", recSessionLabel(m.session)].filter(Boolean).join(" · ");
    return m.stopReason && m.stopReason !== "user" ? s + " · " + recStopText(m.stopReason) : s;
}
function recDoneBody(m) {
    var parts = [recMinutes(m.dur)];
    if (m.speakers > 0) parts.push(m.speakers + (m.speakers === 1 ? " speaker" : " speakers"));
    parts.push(recSessionLabel(m.session));
    return parts.filter(Boolean).join(" · ");
}
// Flat key/value cells for the recording popup's 2-column Grid; "⚠ " marks a problem.
function recDetails(m) {
    var out = ["mic", (m.mic === "ok" ? "" : "⚠ ") + (m.mic || "unknown"),
               "echo cancel", m.aec === null ? "unknown" : (m.aec ? "on" : "⚠ off"),
               "call audio", m.call.length ? m.call.join(", ") : "⚠ none yet"];
    if (m.diskMin !== null) out.push("disk", "~" + m.diskMin + " min left");
    if (m.maxMin) out.push("limit", m.maxMin % 60 ? m.maxMin + " min" : m.maxMin / 60 + " h");
    return out;
}

// Detached notification with actions: the notify-send client waits for the
// click, not QML, so the buttons survive a bar reload. Paths go in as $1/$2,
// never into the script text.
var REC_NOTIFY_SH = 'a=$(notify-send -a kozyrek -A open="Open transcript" -A folder="Open folder" "Transcript ready" "$3") || exit 0; ' +
                    'case $a in open) exec xdg-open "$2";; folder) exec xdg-open "$1";; esac';
function recDoneArgv(session, transcript, body) { return ["sh", "-c", REC_NOTIFY_SH, "kozyrec-notify", session, transcript, body]; }
