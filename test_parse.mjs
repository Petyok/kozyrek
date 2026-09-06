// node --test test_parse.mjs — the only automated check for the pure logic.
// lib/parse.js is a plain QML .js library (no pragma, no imports), so load it
// the way QML does: as a script that defines functions.
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
const src = readFileSync(new URL("./lib/parse.js", import.meta.url), "utf8");
const P = new Function(src + "; return { ipMonitor, stepVolume, brightnessArg, brightnessctl, wifiGlyph, batteryGlyph, wsSort };")();

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
