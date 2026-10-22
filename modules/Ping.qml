import QtQuick
import Quickshell
import Quickshell.Io
import "../" as K
import "../lib/parse.js" as Parse

// Is the internet actually there, not just the Wi-Fi: one long-lived ping.
// -O prints a line for every probe left unanswered, so a dead link shows up
// within one interval instead of going quiet. Click: latency/loss graph.
// Hidden while all is well: shown once a probe is slow (≥ 150 ms), lost or
// offline, and for a minute after the last bad one so a blip doesn't flicker.
// The graph is also reachable from the Wi-Fi popup (openGraph).
K.Pill {
    id: root
    active: menu.open
    onClicked: menu.open = !menu.open
    visible: alert || menu.open

    property real ms: NaN   // last round-trip; -1 = probe lost; NaN = ping not running (no route)
    readonly property bool lost: ms === -1
    readonly property bool offline: isNaN(ms)
    readonly property bool bad: offline || lost || ms >= 150
    property bool alert: false
    Timer { id: hold; interval: 60000; onTriggered: root.alert = root.bad }

    // Show the pill first, open once the bar has laid it out: the popup anchors
    // to the pill's position at the moment it opens.
    function openGraph() { if (visible) menu.open = true; else { alert = true; hold.restart(); Qt.callLater(() => menu.open = true) } }

    // ── history: state/ping.json, compact JSON, saved every 5 min and on reload/exit ──
    property var hist: ({ raw: [], min: [] })
    property int rev: 0     // bumped per sample; the graph repaints on it
    property int minutes: 0
    function record(v) {
        if (Parse.pingAdd(hist, Date.now(), v) && ++minutes % 5 === 0) save()
        rev++
        if (bad) { alert = true; hold.restart() }
    }
    function save() { store.setText(JSON.stringify(hist)) }
    Component.onDestruction: save()
    FileView {
        id: store
        path: Quickshell.statePath("ping.json")
        blockLoading: true; blockWrites: true   // the destruction save must land before the process goes
        printErrors: false                       // first run: no file yet
        onLoaded: { try { var h = JSON.parse(text()); if (h.raw && h.min) root.hist = h } catch (e) {} ping.running = true }
        onLoadFailed: ping.running = true
    }

    Process {
        id: ping
        command: ["ping", "-n", "-O", "-i", "5", "-W", "2", "1.1.1.1"]
        stdout: SplitParser { onRead: line => { var v = Parse.pingLine(line); if (v !== null) { root.ms = v; root.record(v) } } }
        // Exits at once with "Network is unreachable" while there is no route:
        // each failed start is a lost probe, so outages show up in the graph.
        onExited: { root.ms = NaN; root.record(-1); restart.start() }
    }
    Timer { id: restart; interval: 5000; onTriggered: ping.running = true }

    // Fixed to the widest text, so "9 ms" → "589 ms" doesn't shift the pills to its left.
    TextMetrics { id: widest; text: "offline"; font.pixelSize: K.Theme.fontPx }
    Text {
        text: root.offline ? "offline" : root.lost ? "lost" : Math.round(root.ms) + " ms"
        color: root.offline ? K.Theme.dim : root.lost ? K.Theme.bad : root.ms >= 150 ? K.Theme.warn : K.Theme.text
        font.pixelSize: K.Theme.fontPx
        width: widest.advanceWidth; horizontalAlignment: Text.AlignHCenter
        anchors.verticalCenter: parent.verticalCenter
    }

    K.Popup {
        id: menu
        pill: root
        contentWidth: 340
        readonly property int colW: contentWidth - 32
        readonly property var ranges: [["10m", 600e3], ["30m", 1800e3], ["1h", 3600e3], ["3h", 3 * 3600e3],
                                       ["8h", 8 * 3600e3], ["24h", 86400e3], ["3d", 3 * 86400e3]]
        property int sel: 0
        readonly property real range: ranges[sel][1]
        // ≥ 10 s per column, so a 5 s probe lands in every one (no jitter gaps)
        readonly property int nbins: Math.min(120, range / 10000)
        property real now: Date.now()
        readonly property var bins: { root.rev; return open ? Parse.pingBins(root.hist, now, range, nbins) : [] }
        readonly property var stats: Parse.pingStats(bins)
        readonly property int scale: Parse.pingScale(bins)
        onBinsChanged: graph.requestPaint()
        onOpenChanged: now = Date.now()
        Connections { target: root; function onRevChanged() { menu.now = Date.now() } }
        function fmtMs(v) { return v < 0 ? "—" : Math.round(v) + " ms" }
        function binTime(i) {
            var d = new Date(now - range + (i + 0.5) * range / nbins)
            return Qt.formatDateTime(d, range > 86400e3 ? "ddd hh:mm" : range <= 1800e3 ? "hh:mm:ss" : "hh:mm")
        }

        Row {
            width: menu.colW
            Text { text: "Ping 1.1.1.1"; color: K.Theme.text; font.pixelSize: 15; font.bold: true; width: menu.colW - nowTxt.width }
            Text { id: nowTxt; text: root.offline ? "offline" : root.lost ? "lost" : menu.fmtMs(root.ms); color: K.Theme.dim; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
        }

        Row {
            spacing: 4
            Repeater {
                model: menu.ranges
                Rectangle {
                    width: (menu.colW - 6 * 4) / 7; height: 26; radius: 8
                    color: index === menu.sel ? K.Theme.accent : chipMA.containsMouse ? "#33ffffff" : "#1effffff"
                    Text { anchors.centerIn: parent; text: modelData[0]; color: K.Theme.text; font.pixelSize: 12 }
                    MouseArea { id: chipMA; anchors.fill: parent; hoverEnabled: true; onClicked: menu.sel = index }
                }
            }
        }

        Item {
            width: menu.colW; height: 120
            Canvas {
                id: graph
                anchors.fill: parent
                property int hover: -1
                onHoverChanged: requestPaint()
                onPaint: {
                    var c = getContext("2d"), b = menu.bins, n = menu.nbins, w = width / n, H = height, s = menu.scale
                    c.reset()
                    c.fillStyle = "#14ffffff"; c.fillRect(0, H / 2, width, 1)          // half-scale line
                    for (var i = 0; i < n; i++) {
                        var x = i * w
                        if (i === hover) { c.fillStyle = "#26ffffff"; c.fillRect(x, 0, w, H) }
                        if (!b[i]) continue
                        var bw = Math.max(1, w - 0.5)
                        if (b[i].avg >= 0) {
                            var ha = Math.max(1, Math.min(1, b[i].avg / s) * H)
                            c.fillStyle = b[i].avg >= 150 ? String(K.Theme.warn) : String(K.Theme.accent)
                            c.fillRect(x, H - ha, bw, ha)
                        }
                        if (b[i].lost) {      // red from the top, as tall as the loss share
                            c.fillStyle = String(K.Theme.bad)
                            c.fillRect(x, 0, bw, Math.max(2, b[i].lost / b[i].n * H))
                        }
                    }
                }
            }
            Text { text: menu.scale + " ms"; color: K.Theme.dim; font.pixelSize: 10; anchors { right: parent.right; top: parent.top } }
            Text { text: menu.scale / 2; color: K.Theme.dim; font.pixelSize: 10; anchors { right: parent.right; bottom: parent.verticalCenter } }
            MouseArea {
                anchors.fill: parent; hoverEnabled: true
                onPositionChanged: mouse => graph.hover = Math.floor(mouse.x / width * menu.nbins)
                onExited: graph.hover = -1
            }
        }
        Row {
            width: menu.colW
            Text { text: "−" + menu.ranges[menu.sel][0]; color: K.Theme.dim; font.pixelSize: 10; width: menu.colW - nowLbl.width }
            Text { id: nowLbl; text: "now"; color: K.Theme.dim; font.pixelSize: 10 }
        }

        // Hovered column, else the whole range.
        Text {
            readonly property var h: graph.hover >= 0 ? menu.bins[graph.hover] : undefined
            color: K.Theme.text; font.pixelSize: 12
            text: graph.hover >= 0
                ? menu.binTime(graph.hover) + "   " + (h ? "avg " + menu.fmtMs(h.avg) + " · max " + menu.fmtMs(h.max)
                                                       + " · loss " + Math.round(h.lost / h.n * 100) + "%" : "no data")
                : menu.stats ? "avg " + menu.fmtMs(menu.stats.avg) + " · max " + menu.fmtMs(menu.stats.max)
                               + " · loss " + (menu.stats.loss * 100).toFixed(menu.stats.loss && menu.stats.loss < 0.01 ? 2 : 0) + "%"
                             : "no data yet"
        }
    }
}
