// node --test test_parse.mjs — the only automated check for the pure logic.
// lib/parse.js is a plain QML .js library (no pragma, no imports), so load it
// the way QML does: as a script that defines functions.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const src = readFileSync(new URL("./lib/parse.js", import.meta.url), "utf8");
const P = new Function(src + "; return { ipMonitor, stepVolume, brightnessArg, brightnessctl, wifiGlyph, batteryGlyph, wsSort, " +
    "recInit, recParse, recReduce, recLive, recClick, recToggle, fmtClock, fmtEta, recEta, recPillText, recGlyph, " +
    "recErrorTitle, recAskLine, recDoneBody, recDoneArgv };")();

test("ipMonitor parses add/delete lines", () => {
    assert.deepEqual(P.ipMonitor("5: wg0: <POINTOPOINT,NOARP,UP,LOWER_UP> mtu 1420 qdisc noqueue state UNKNOWN group default"), { name: "wg0", deleted: false });
    assert.deepEqual(P.ipMonitor("Deleted 5: wg0: <POINTOPOINT,NOARP> mtu 1420 qdisc noqueue state DOWN"), { name: "wg0", deleted: true });
    assert.deepEqual(P.ipMonitor("7: veth1a2b@if6: <BROADCAST> mtu 1500"), { name: "veth1a2b", deleted: false });
    assert.equal(P.ipMonitor("    link/ether aa:bb:cc:dd:ee:ff brd ff:ff:ff:ff:ff:ff"), null);
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
    const a = P.recDoneArgv(evil, evil + "/transcript.md", "x");
    assert.deepEqual(a.slice(0, 2), ["sh", "-c"]);
    assert.ok(!a[2].includes(evil)); assert.equal(a[4], evil); assert.equal(a[5], evil + "/transcript.md");
});
test("rec glyphs are single codepoints; recording/stopping draw the dot instead", () => {
    for (const s of ["idle", "ask", "transcribing", "done", "error"]) assert.equal([...P.recGlyph(s)].length, 1, s);
    assert.equal(P.recGlyph("recording"), ""); assert.equal(P.recGlyph("stopping"), "");
});
