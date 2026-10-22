// node --test test_parse.mjs — the only automated check for the pure logic.
// lib/parse.js is a plain QML .js library (no pragma, no imports), so load it
// the way QML does: as a script that defines functions.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, mkdtempSync, writeFileSync, chmodSync, rmSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { tmpdir } from "node:os";
import { join } from "node:path";
const src = readFileSync(new URL("./lib/parse.js", import.meta.url), "utf8");
const P = new Function(src + "; return { ipMonitor, pingLine, pingAdd, pingBins, pingStats, pingScale, stepVolume, brightnessArg, brightnessctl, wifiGlyph, batteryGlyph, wsSort, " +
    "recInit, recParse, recReduce, recLive, recClick, recToggle, fmtClock, fmtEta, recEta, recPillText, recGlyph, " +
    "recErrorTitle, recAskLine, recDoneBody, recDoneArgv, dictLang, screenrecStatus, screenrecCtlPos, parseTimer };")();

test("ipMonitor parses add/delete lines", () => {
    assert.deepEqual(P.ipMonitor("5: wg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1420 qdisc noqueue state UNKNOWN group default"), { name: "wg0", deleted: false });
    assert.deepEqual(P.ipMonitor("Deleted 5: wg0: <POINTOPOINT,NOARP> mtu 1420 qdisc noqueue state DOWN"), { name: "wg0", deleted: true });
    assert.deepEqual(P.ipMonitor("7: veth1a2b@if6: <BROADCAST> mtu 1500"), { name: "veth1a2b", deleted: false });
    assert.equal(P.ipMonitor("    link/ether aa:bb:cc:dd:ee:ff brd ff:ff:ff:ff:ff:ff"), null);
});
test("pingLine: reply → ms, -O timeout → -1, anything else → null", () => {
    assert.equal(P.pingLine("64 bytes from 1.1.1.1: icmp_seq=1 ttl=59 time=9.17 ms"), 9.17);
    assert.equal(P.pingLine("64 bytes from 1.1.1.1: icmp_seq=4 ttl=59 time=589 ms"), 589);
    assert.equal(P.pingLine("no answer yet for icmp_seq=5"), -1);
    assert.equal(P.pingLine("PING 1.1.1.1 (1.1.1.1) 56(84) bytes of data."), null);
    assert.equal(P.pingLine("From 192.0.2.1 icmp_seq=3 Destination Host Unreachable"), null);
});
test("ping history: minute buckets, pruning, bins, stats", () => {
    const h = { raw: [], min: [] }, t0 = 1_000_000 * 60000;
    assert.equal(P.pingAdd(h, t0, 10), true);              // opens a minute
    assert.equal(P.pingAdd(h, t0 + 5000, 30), false);
    assert.equal(P.pingAdd(h, t0 + 10000, -1), false);     // lost
    assert.deepEqual(h.min, [[t0, 3, 1, 40, 30]]);
    assert.equal(P.pingAdd(h, t0 + 60000, 20), true);
    // 10 min range, 10 bins of 1 min: raw samples, the lost one counts in loss only
    const bins = P.pingBins(h, t0 + 10 * 60000, 10 * 60000, 10);
    assert.deepEqual(bins[0], { n: 3, lost: 1, avg: 20, max: 30 });
    assert.deepEqual(bins[1], { n: 1, lost: 0, avg: 20, max: 20 });
    assert.equal(bins[2], null);                            // nothing measured: a gap, not zero
    assert.deepEqual(P.pingStats(bins), { avg: 20, max: 30, loss: 0.25 });
    assert.equal(P.pingScale(bins), 50);
    // all lost → avg -1, not NaN
    assert.equal(P.pingBins({ raw: [[t0, 1, 1, 0, 0]], min: [] }, t0 + 1, 60000, 1)[0].avg, -1);
    // > 1 h range reads minute buckets
    assert.deepEqual(P.pingBins(h, t0 + 3600e3 * 3, 3600e3 * 3, 1)[0], { n: 4, lost: 1, avg: 20, max: 30 });
    // the last hour of raw and 3 days of minutes are kept
    P.pingAdd(h, t0 + 3 * 86400e3 + 120000, 5);
    assert.deepEqual(h.raw.map(b => b[0]), [t0 + 3 * 86400e3 + 120000]);
    assert.deepEqual(h.min.map(b => b[0]), [t0 + 3 * 86400e3 + 120000]);
});
test("stepVolume clamps to [0,max]", () => {
    assert.equal(P.stepVolume(0.5, "+5", 1.5), 0.55);
    assert.equal(P.stepVolume(1.48, "+5", 1.5), 1.5);
    assert.equal(P.stepVolume(0.03, "-5", 1.5), 0);
});
test("brightnessArg maps ±N to brightnessctl syntax", () => {
    assert.equal(P.brightnessArg("+5"), "5%+");
    assert.equal(P.brightnessArg("-5"), "5%-");
});
test("brightnessctl -m output → fraction", () => {
    assert.equal(P.brightnessctl("intel_backlight,backlight,2499,90%,2777\n"), 0.9);
    assert.ok(Number.isNaN(P.brightnessctl("garbage")));
});
test("glyphs are non-empty single codepoints", () => {
    for (const g of [P.wifiGlyph(0.9, true), P.wifiGlyph(null, false), P.batteryGlyph(50, false), P.batteryGlyph(50, true)])
        assert.equal([...g].length, 1, g);
    assert.notEqual(P.wifiGlyph(0.1, true), P.wifiGlyph(0.9, true));
});
test("wsSort drops special workspaces and sorts", () => {
    assert.deepEqual(P.wsSort([{ id: 3 }, { id: -99 }, { id: 1 }]).map(w => w.id), [1, 3]);
});

// ── call recorder ───────────────────────────────────────────────────────────
const STATES = ["idle", "starting", "recording", "stopping", "ask", "transcribing", "done", "error"];
test("fmtClock boundaries", () => {
    for (const [s, t] of [[0, "00:00"], [7.9, "00:07"], [754, "12:34"], [3599, "59:59"], [3600, "1:00:00"], [3723, "1:02:03"], [-5, "00:00"], [NaN, "00:00"]])
        assert.equal(P.fmtClock(s), t, String(s));
});
test("fmtEta buckets", () => {
    assert.equal(P.fmtEta(null), "estimating…");
    assert.equal(P.fmtEta(59), "< 1 min");
    assert.equal(P.fmtEta(61), "~2 min");
    assert.equal(P.fmtEta(3900), "~1 h 05 min");
});
test("recEta counts down from the last progress line, never below zero", () => {
    const m = { ...P.recInit(), eta: 600, etaAt: 1000 };
    assert.equal(P.recEta(m, 1300), "~5 min");
    assert.equal(P.recEta(m, 5000), "< 1 min");
});
test("recParse accepts only {ev:string} objects", () => {
    assert.deepEqual(P.recParse('{"ev":"state","state":"idle"}'), { ev: "state", state: "idle" });
    for (const l of ["garbage", "[1]", "null", '{"state":"idle"}', '{"ev":3}', ""]) assert.equal(P.recParse(l), null, l);
});
test("recReduce: record → ask → transcribe → done", () => {
    let m = P.recInit();
    m = P.recReduce(m, { ev: "state", state: "recording", at: 1, session: "/c/s", since: 100 }, 100);
    assert.equal(P.recPillText(m, 175), "01:15");
    m = P.recReduce(m, { ev: "state", state: "ask", at: 2, session: "/c/s", duration_s: 2052, stop_reason: "user" }, 200);
    assert.equal(m.dur, 2052);
    m = P.recReduce(m, { ev: "state", state: "transcribing", at: 3, session: "/c/s", since: 210 }, 210);
    assert.equal(m.frac, 0); assert.equal(m.dur, 2052);          // audio length carried over from ask
    m = P.recReduce(m, { ev: "progress", session: "/c/s", stage: "asr", frac: 0.423, eta_s: 300 }, 300);
    assert.equal(P.recPillText(m, 300), "42%");
    m = P.recReduce(m, { ev: "state", state: "done", at: 4, session: "/c/s", transcript: "/c/s/transcript.md", duration_s: 2052, speakers: 3 }, 900);
    assert.equal(m.frac, -1); assert.equal(m.transcript, "/c/s/transcript.md");
});
test("recReduce drops stale lines (state and error) and returns the same object", () => {
    const m = P.recReduce(P.recInit(), { ev: "state", state: "ask", at: 5, session: "/c/s" }, 0);
    assert.equal(P.recReduce(m, { ev: "state", state: "recording", at: 4, session: "/c/s", since: 1 }, 0), m);
    assert.equal(P.recReduce(m, { ev: "error", state: "error", at: 4, msg: "old" }, 0), m);
    assert.equal(P.recReduce(m, { ev: "state", state: "bogus", at: 9 }, 0), m);
    assert.equal(P.recReduce(m, { ev: "hello" }, 0), m);
});
test("recording re-emits with the same `at` update live fields", () => {
    let m = P.recReduce(P.recInit(), { ev: "state", state: "recording", at: 7, session: "/c/s", since: 1, mic: "ok", warn: null }, 0);
    m = P.recReduce(m, { ev: "state", state: "recording", at: 7, session: "/c/s", since: 1, mic: "muted", warn: "mic_muted" }, 0);
    assert.equal(m.mic, "muted"); assert.equal(m.warn, "mic_muted"); assert.equal(m.since, 1);
});
test("a transient error (no `at`) does not block the replayed persisted state", () => {
    let m = P.recReduce(P.recInit(), { ev: "state", state: "recording", at: 50, session: "/c/s", since: 1 }, 0);
    m = P.recReduce(m, { ev: "error", msg: "already recording", retry: false }, 0);
    assert.equal(m.state, "error"); assert.equal(m.at, 50);
    m = P.recReduce(m, { ev: "state", state: "recording", at: 50, session: "/c/s", since: 1 }, 0);
    assert.equal(m.state, "recording");
    // …while a persisted error does advance it
    m = P.recReduce(m, { ev: "error", state: "error", at: 60, msg: "recorder failed", session: "/c/s" }, 0);
    assert.equal(P.recReduce(m, { ev: "state", state: "recording", at: 50, session: "/c/s", since: 1 }, 0), m);
});
test("local optimistic states never advance the ordering stamp", () => {
    let m = P.recReduce(P.recInit(), { ev: "state", state: "idle", at: 10 }, 0);
    m = P.recReduce(m, { ev: "local", state: "starting" }, 0);
    assert.equal(m.state, "starting"); assert.equal(m.at, 10);
    m = P.recReduce(m, { ev: "state", state: "recording", at: 11, session: "/c/s", since: 1 }, 0);
    assert.equal(m.state, "recording");
});
test("progress: only while transcribing, same session, monotonic, clamped", () => {
    let m = P.recInit();
    assert.equal(P.recReduce(m, { ev: "progress", frac: 0.5 }, 0), m);
    m = P.recReduce(m, { ev: "state", state: "transcribing", at: 1, session: "/c/a" }, 0);
    m = P.recReduce(m, { ev: "progress", session: "/c/a", frac: 0.5 }, 0);
    m = P.recReduce(m, { ev: "progress", session: "/c/a", frac: 0.4 }, 0); assert.equal(m.frac, 0.5);
    m = P.recReduce(m, { ev: "progress", session: "/c/a", frac: 7 }, 0);   assert.equal(m.frac, 1);
    assert.equal(P.recReduce(m, { ev: "progress", session: "/c/b", frac: 0.9 }, 0), m);
});
test("error remembers what failed and whether it can be retried", () => {
    let m = P.recReduce(P.recInit(), { ev: "state", state: "transcribing", at: 1, session: "/c/s" }, 0);
    m = P.recReduce(m, { ev: "error", msg: "transcriber died", retry: true }, 0);
    assert.equal(m.state, "error"); assert.equal(m.during, "transcribe"); assert.equal(m.retry, true); assert.equal(m.session, "/c/s");
    assert.equal(P.recErrorTitle(m), "Transcription failed");
    m = P.recReduce(m, { ev: "state", state: "idle", at: 2 }, 0);
    assert.equal(m.msg, ""); assert.equal(m.session, "");
});
test("click table", () => {
    const T = { idle: ["start", "folder", "none"], starting: ["none", "none", "none"], recording: ["stop", "popup", "none"],
                stopping: ["none", "none", "none"], ask: ["popup", "popup", "none"], transcribing: ["popup", "popup", "none"],
                done: ["open", "folder", "none"], error: ["popup", "popup", "none"] };
    for (const [s, [l, r, mid]] of Object.entries(T)) {
        assert.equal(P.recClick(s, "left"), l, s); assert.equal(P.recClick(s, "right"), r, s); assert.equal(P.recClick(s, "middle"), mid, s);
    }
});
test("hotkey toggle never opens files and never double-starts", () => {
    assert.deepEqual(STATES.map(P.recToggle), ["start", "none", "stop", "none", "popup", "popup", "start", "start"]);
});
test("recLive = the states that need the follower attached", () => {
    assert.deepEqual(STATES.filter(P.recLive), ["starting", "recording", "stopping", "transcribing"]);
});
test("recDoneBody / recAskLine", () => {
    const m = { ...P.recInit(), dur: 2532, speakers: 3, session: "/home/u/Calls/2026-09-25_14-03-11" };
    assert.equal(P.recDoneBody(m), "42 min · 3 speakers · 2026-09-25 14:03");
    assert.equal(P.recDoneBody({ ...m, speakers: 1, dur: 20 }), "< 1 min · 1 speaker · 2026-09-25 14:03");
    assert.equal(P.recAskLine({ ...m, stopReason: "max_duration" }), "42:12 recorded · 2026-09-25 14:03 · Reached the 1.5 h limit");
});
test("recDoneArgv keeps paths out of the shell script", () => {
    const evil = "/home/u/Calls/$(touch pwned); `id`";
    const a = P.recDoneArgv(evil, evil + "/transcript.md", "x", evil + "/kozyrec.py");
    assert.deepEqual(a.slice(0, 2), ["sh", "-c"]);
    assert.ok(!a[2].includes(evil));
    assert.equal(a[4], evil); assert.equal(a[5], evil + "/transcript.md"); assert.equal(a[7], evil + "/kozyrec.py");
});
// The notification's buttons, run for real against stub notify-send / python3 / qs / xdg-open.
function clickNotification(action) {
    const bin = mkdtempSync(join(tmpdir(), "kz-notify-")), log = join(bin, "log");
    const stub = (name, body) => { writeFileSync(join(bin, name), "#!/bin/sh\n" + body + "\n"); chmodSync(join(bin, name), 0o755); };
    stub("notify-send", 'printf "%s" "$ACTION"; [ -n "$ACTION" ]');                 // no click → notify-send fails
    for (const n of ["python3", "qs", "xdg-open"]) stub(n, `echo "${n} $*" >> "${log}"`);
    const session = "/home/u/Calls/a b", a = P.recDoneArgv(session, session + "/transcript.md", "body", "/k/kozyrec.py");
    try {
        execFileSync(a[0], a.slice(1), { env: { PATH: bin + ":/usr/bin:/bin", ACTION: action } });
        try { return readFileSync(log, "utf8").trim().split("\n"); } catch (e) { return []; }
    } finally { rmSync(bin, { recursive: true, force: true }); }
}
test("Open transcript from the notification dismisses that session and refreshes the pill", () => {
    assert.deepEqual(clickNotification("open"), [
        "python3 /k/kozyrec.py dismiss /home/u/Calls/a b",
        "qs -c kozyrek ipc call rec refresh",
        "xdg-open /home/u/Calls/a b/transcript.md",
    ]);
});
test("Open folder from the notification and a closed notification leave the state alone", () => {
    assert.deepEqual(clickNotification("folder"), ["xdg-open /home/u/Calls/a b"]);
    assert.deepEqual(clickNotification(""), []);
});
test("rec glyphs are single codepoints; recording/stopping draw the dot instead", () => {
    for (const s of ["idle", "ask", "transcribing", "done", "error"]) assert.equal([...P.recGlyph(s)].length, 1, s);
    assert.equal(P.recGlyph("recording"), ""); assert.equal(P.recGlyph("stopping"), "");
});
test("dictLang follows the last letter, past punctuation and digits", () => {
    assert.equal(P.dictLang("Привет, world!"), "EN");
    assert.equal(P.dictLang("Open the PR, пожалуйста."), "RU");
    assert.equal(P.dictLang("ёж 42…"), "RU");
    assert.equal(P.dictLang("12:30 — "), "");
});
test("screenrecStatus: idle, full screen, region, broken geometry", () => {
    assert.equal(P.screenrecStatus(""), null);
    assert.equal(P.screenrecStatus("\n"), null);
    assert.deepEqual(P.screenrecStatus("4242 75 on\n"), { pid: 4242, secs: 75, mic: true, area: null });
    assert.deepEqual(P.screenrecStatus("4242 3 off 120 64 640 480"), { pid: 4242, secs: 3, mic: false, area: { x: 120, y: 64, w: 640, h: 480 } });
    assert.equal(P.screenrecStatus("4242 3 off 120 64").area, null);
    assert.equal(P.screenrecStatus("4242 3 off 120 64 0 480").area, null);
});
test("screenrecCtlPos keeps the controls outside the region: above, below, right, left, then inside", () => {
    const W = 1440, H = 900, w = 200, h = 40;
    const outside = (p, r) => p.x + w <= r.x || p.x >= r.x + r.w || p.y + h <= r.y || p.y >= r.y + r.h;
    const cases = [
        [{ x: 400, y: 300, w: 400, h: 300 }, { x: 500, y: 252 }],    // above, centred
        [{ x: 100, y: 10,  w: 500, h: 300 }, { x: 250, y: 318 }],    // no room above → below
        [{ x: 0,   y: 0,   w: 1000, h: 900 }, { x: 1008, y: 430 }],  // full height → right
        [{ x: 500, y: 0,   w: 940, h: 900 }, { x: 292, y: 430 }],    // full height, flush right → left
    ];
    for (const [r, want] of cases) {
        const p = P.screenrecCtlPos(r, w, h, W, H);
        assert.deepEqual(p, want, JSON.stringify(r));
        assert.ok(outside(p, r), JSON.stringify(r));
    }
    assert.deepEqual(P.screenrecCtlPos({ x: 0, y: 200, w: 100, h: 300 }, w, h, W, H), { x: 4, y: 152 });       // clamped to the screen edge
    assert.deepEqual(P.screenrecCtlPos({ x: 0, y: 0, w: W, h: H }, w, h, W, H), { x: 620, y: 8 });              // whole screen: inside the top edge
});

test("parseTimer: durations, bare minutes, alarms, labels, junk", () => {
    const now = new Date(2026, 9, 18, 17, 37, 20).getTime();
    assert.deepEqual(P.parseTimer("1h30m tea", now), { kind: "timer", at: now + 5400e3, label: "tea" });
    assert.equal(P.parseTimer("90s", now).at, now + 90e3);
    assert.equal(P.parseTimer("10", now).at, now + 600e3);
    assert.equal(P.parseTimer("18:05 стендап", now).at, new Date(2026, 9, 18, 18, 5).getTime());
    assert.equal(P.parseTimer("7:30", now).at, new Date(2026, 9, 19, 7, 30).getTime());   // already past today → tomorrow
    assert.equal(P.parseTimer("17:37", now).at, new Date(2026, 9, 19, 17, 37).getTime()); // equal to now counts as past
    for (const bad of ["", "abc", "0", "0m", "25:00", "12:60", "m5"]) assert.equal(P.parseTimer(bad, now), null, bad);
});
