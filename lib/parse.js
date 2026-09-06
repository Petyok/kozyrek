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
