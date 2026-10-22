import QtQuick
import QtQuick.Controls
import Quickshell
import Quickshell.Io
import "../" as K
import "../lib/parse.js" as Parse

K.Pill {
    id: root
    active: menu.open
    onClicked: menu.open = !menu.open

    // ── config ──
    property real lat: 41.64
    property real lon: 41.64
    property string place: "Batumi"
    property int  refreshMs: 15 * 60 * 1000

    // ── time ──
    property var now: new Date()
    Timer { interval: 1000; repeat: true; running: true; onTriggered: { root.now = new Date(); root.check() } }
    function hhmm(d) { return ("0"+d.getHours()).slice(-2)+":"+("0"+d.getMinutes()).slice(-2) }
    function ddmm(d) { return ("0"+d.getDate()).slice(-2)+"."+("0"+(d.getMonth()+1)).slice(-2) }
    function isoHM(iso) { return root.hhmm(new Date(iso)) }
    readonly property var monthNames: ["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"]
    readonly property var monthGen:   ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    readonly property var dowShort:   ["пн","вт","ср","чт","пт","сб","вс"]
    function monthGrid(year, month) {
        var first = new Date(year, month, 1)
        var startDow = (first.getDay() + 6) % 7
        var daysIn = new Date(year, month + 1, 0).getDate()
        var cells = []
        for (var i = 0; i < startDow; i++) cells.push(0)
        for (var d = 1; d <= daysIn; d++) cells.push(d)
        while (cells.length % 7 !== 0) cells.push(0)
        return cells
    }

    // ── weather ──
    property bool loaded: false
    property real curTemp: 0
    property real curFeels: 0
    property int  curHum: 0
    property real curWind: 0
    property int  curCode: 0
    property string sunrise: ""
    property string sunset: ""
    property var  days: []
    function wmo(code) {
        if (code === 0)                  return ["☀️", "ясно"]
        if (code === 1 || code === 2)    return ["🌤️", "перем. облачно"]
        if (code === 3)                  return ["☁️", "облачно"]
        if (code === 45 || code === 48)  return ["🌫️", "туман"]
        if (code >= 51 && code <= 57)    return ["🌦️", "морось"]
        if (code >= 61 && code <= 67)    return ["🌧️", "дождь"]
        if (code >= 71 && code <= 77)    return ["🌨️", "снег"]
        if (code >= 80 && code <= 82)    return ["🌧️", "ливень"]
        if (code >= 85 && code <= 86)    return ["🌨️", "снегопад"]
        if (code >= 95)                  return ["⛈️", "гроза"]
        return ["❔", "?"]
    }
    function dayName(iso) { return ["вс","пн","вт","ср","чт","пт","сб"][new Date(iso).getDay()] }
    function url() {
        return "https://api.open-meteo.com/v1/forecast"
            + "?latitude=" + lat + "&longitude=" + lon
            + "&current=temperature_2m,weather_code,relative_humidity_2m,apparent_temperature,wind_speed_10m"
            + "&daily=weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max"
            + "&timezone=auto&forecast_days=3"
    }
    Process {
        id: fetch
        command: ["curl", "-s", root.url()]
        running: true
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    var j = JSON.parse(text), c = j.current, d = j.daily
                    root.curTemp = c.temperature_2m; root.curFeels = c.apparent_temperature
                    root.curHum = c.relative_humidity_2m; root.curWind = c.wind_speed_10m
                    root.curCode = c.weather_code
                    root.sunrise = d.sunrise[0]; root.sunset = d.sunset[0]
                    var out = []
                    for (var i = 0; i < d.time.length; i++)
                        out.push({ date: d.time[i], code: d.weather_code[i],
                                   tmax: d.temperature_2m_max[i], tmin: d.temperature_2m_min[i],
                                   pop: d.precipitation_probability_max[i] })
                    root.days = out; root.loaded = true
                } catch (e) { console.log("weather parse fail:", e) }
            }
        }
    }
    Timer { interval: root.refreshMs; repeat: true; running: true; onTriggered: fetch.running = true }

    // ── calendar view state ──
    property int viewYear:  now.getFullYear()
    property int viewMonth: now.getMonth()
    function shiftMonth(delta) {
        var m = viewMonth + delta, y = viewYear
        while (m < 0)  { m += 12; y-- }
        while (m > 11) { m -= 12; y++ }
        viewMonth = m; viewYear = y
    }
    function shiftYear(delta) { viewYear += delta }
    function isToday(d) { return d === now.getDate() && viewMonth === now.getMonth() && viewYear === now.getFullYear() }

    // ── timers & alarms ──
    // Items {kind: "timer"|"alarm", at: epoch ms, label}, sorted by `at`, kept in state/timers.json so they
    // survive a restart. One that came due while the bar was off rings on the first tick after it loads.
    property var items: []
    property bool badInput: false
    FileView {
        id: store
        path: Quickshell.statePath("timers.json")
        printErrors: false           // first run: no file yet
        adapter: JsonAdapter { id: ad; property var items: [] }
        onLoaded: root.items = JSON.parse(JSON.stringify(ad.items))
    }
    function save() { ad.items = root.items; store.writeAdapter() }
    function addItem(text) {
        var p = Parse.parseTimer(text, Date.now())
        if (!p) return false
        items = items.concat([p]).sort(function(a, b) { return a.at - b.at })
        save(); return true
    }
    function removeItem(i) { items = items.filter(function(_, k) { return k !== i }); save() }
    // alarm → "07:30", timer → time left
    function itemWhen(it, now) {
        return it.kind === "alarm" ? hhmm(new Date(it.at)) : Parse.fmtClock(Math.ceil((it.at - now.getTime()) / 1000))
    }
    function check() {
        var t = now.getTime(), due = items.filter(function(it) { return it.at <= t })
        if (!due.length) return
        items = items.filter(function(it) { return it.at > t }); save()
        due.forEach(function(it) { root.ring(it) })
    }
    // App name "kozyrek-alarm" lets Notifications.qml through do-not-disturb.
    function ring(it) {
        var alarm = it.kind === "alarm"
        Quickshell.execDetached(["notify-send", "-a", "kozyrek-alarm", "-u", "critical", "-t", "60000",
                                 alarm ? "⏰ Будильник " + hhmm(new Date(it.at)) : "⏳ Таймер", it.label])
        Quickshell.execDetached(["sh", "-c", "for i in 1 2 3; do paplay /usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga; done"])
    }

    // ── pill content ──
    Text { text: root.loaded ? root.wmo(root.curCode)[0] : "🕐"; font.pixelSize: 16; anchors.verticalCenter: parent.verticalCenter }
    Column {
        spacing: -1
        anchors.verticalCenter: parent.verticalCenter
        Text {
            text: root.items.length ? (root.items[0].kind === "alarm" ? "⏰ " : "⏳ ") + root.itemWhen(root.items[0], root.now) : root.ddmm(root.now)
            color: root.items.length ? K.Theme.accent : K.Theme.dim; font.pixelSize: 10; anchors.horizontalCenter: parent.horizontalCenter
        }
        Text { text: root.hhmm(root.now); color: K.Theme.text; font.pixelSize: K.Theme.fontPx; font.bold: true; anchors.horizontalCenter: parent.horizontalCenter }
    }

    // ── dropdown ──
    K.Popup {
        id: menu
        pill: root
        contentWidth: 320
        wantsKeyboard: true      // the add field
        onOpenChanged: if (open) { root.viewYear = root.now.getFullYear(); root.viewMonth = root.now.getMonth() }
        readonly property int colW: contentWidth - 32

        Column {
            spacing: 0
            Text { text: root.hhmm(root.now); color: K.Theme.text; font.pixelSize: 30; font.bold: true }
            Text { text: root.now.getDate() + " " + root.monthGen[root.now.getMonth()] + " " + root.now.getFullYear(); color: K.Theme.dim; font.pixelSize: 13 }
        }
        Rectangle { width: menu.colW; height: 1; color: K.Theme.faint }

        Column {
            width: menu.colW
            spacing: 6
            Row {
                width: parent.width; height: 26
                Repeater {
                    model: [ {t:"«", d:-12}, {t:"‹", d:-1} ]
                    Rectangle {
                        width: 26; height: 26; radius: 8
                        color: navMA.containsMouse ? "#33ffffff" : "transparent"
                        Text { anchors.centerIn: parent; text: modelData.t; color: K.Theme.text; font.pixelSize: 16 }
                        MouseArea { id: navMA; anchors.fill: parent; hoverEnabled: true
                            onClicked: modelData.d === -12 ? root.shiftYear(-1) : root.shiftMonth(-1) }
                    }
                }
                Text {
                    text: root.monthNames[root.viewMonth].charAt(0).toUpperCase() + root.monthNames[root.viewMonth].slice(1) + " " + root.viewYear
                    color: K.Theme.text; font.pixelSize: 14; font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                    anchors.verticalCenter: parent.verticalCenter
                    width: menu.colW - 26*4
                }
                Repeater {
                    model: [ {t:"›", d:1}, {t:"»", d:12} ]
                    Rectangle {
                        width: 26; height: 26; radius: 8
                        color: navMA2.containsMouse ? "#33ffffff" : "transparent"
                        Text { anchors.centerIn: parent; text: modelData.t; color: K.Theme.text; font.pixelSize: 16 }
                        MouseArea { id: navMA2; anchors.fill: parent; hoverEnabled: true
                            onClicked: modelData.d === 12 ? root.shiftYear(1) : root.shiftMonth(1) }
                    }
                }
            }
            Row {
                width: parent.width
                Repeater {
                    model: root.dowShort
                    Text { width: menu.colW / 7; horizontalAlignment: Text.AlignHCenter; text: modelData
                           color: (index >= 5) ? K.Theme.accent : K.Theme.dim; font.pixelSize: 11 }
                }
            }
            Grid {
                columns: 7; width: parent.width
                Repeater {
                    model: root.monthGrid(root.viewYear, root.viewMonth)
                    Item {
                        width: menu.colW / 7; height: width * 0.82
                        Rectangle { anchors.centerIn: parent; width: 26; height: 26; radius: 13; visible: root.isToday(modelData); color: K.Theme.today }
                        Text { anchors.centerIn: parent; text: modelData > 0 ? modelData : ""
                               color: root.isToday(modelData) ? "white" : K.Theme.text; font.pixelSize: 13; font.bold: root.isToday(modelData) }
                    }
                }
            }
        }
        Rectangle { width: menu.colW; height: 1; color: K.Theme.faint }

        Row {
            spacing: 12; visible: root.loaded
            Text { text: root.wmo(root.curCode)[0]; font.pixelSize: 38; anchors.verticalCenter: parent.verticalCenter }
            Column {
                spacing: -1
                Row {
                    spacing: 6
                    Text { text: Math.round(root.curTemp)+"°"; color: K.Theme.text; font.pixelSize: 26; font.bold: true }
                    Text { text: "ощущ. "+Math.round(root.curFeels)+"°"; color: K.Theme.dim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                }
                Text { text: root.place + " · " + root.wmo(root.curCode)[1]; color: K.Theme.dim; font.pixelSize: 12 }
            }
        }
        Row {
            width: menu.colW; visible: root.loaded
            Repeater {
                model: [ { i: "💧", v: root.curHum + "%" }, { i: "💨", v: Math.round(root.curWind) + " км/ч" },
                         { i: "🌅", v: root.isoHM(root.sunrise) }, { i: "🌇", v: root.isoHM(root.sunset) } ]
                Row {
                    width: menu.colW / 4; spacing: 3
                    Text { text: modelData.i; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: modelData.v; color: K.Theme.dim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                }
            }
        }
        Repeater {
            model: root.days
            Item {
                width: menu.colW; height: 22
                Text { id: dn2; text: root.dayName(modelData.date); color: K.Theme.text; font.pixelSize: 13; width: 26; anchors.verticalCenter: parent.verticalCenter }
                Text { id: ic2; text: root.wmo(modelData.code)[0]; font.pixelSize: 15; anchors { left: dn2.right; leftMargin: 4; verticalCenter: parent.verticalCenter } }
                Row {
                    spacing: 2; visible: modelData.pop > 5
                    anchors { left: ic2.right; leftMargin: 10; verticalCenter: parent.verticalCenter }
                    Text { text: "💧"; font.pixelSize: 10; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: modelData.pop + "%"; color: K.Theme.accent; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                }
                Text { text: Math.round(modelData.tmax)+"° / "+Math.round(modelData.tmin)+"°"; color: K.Theme.text; font.pixelSize: 12
                       anchors { right: parent.right; verticalCenter: parent.verticalCenter } }
            }
        }

        Rectangle { width: menu.colW; height: 1; color: K.Theme.faint }
        Text { text: "Таймеры и будильники"; color: K.Theme.dim; font.pixelSize: 12 }
        Repeater {
            model: root.items
            Item {
                width: menu.colW; height: 24
                Text { id: ti; text: modelData.kind === "alarm" ? "⏰" : "⏳"; font.pixelSize: 14; anchors.verticalCenter: parent.verticalCenter }
                Text {
                    text: root.itemWhen(modelData, root.now) + (modelData.label ? "  " + modelData.label : "")
                    color: K.Theme.text; font.pixelSize: 13; elide: Text.ElideRight
                    anchors { left: ti.right; leftMargin: 8; right: rm.left; verticalCenter: parent.verticalCenter }
                }
                Text {
                    id: rm; text: "✕"; font.pixelSize: 13; anchors { right: parent.right; verticalCenter: parent.verticalCenter }
                    color: rmMA.containsMouse ? K.Theme.bad : K.Theme.dim
                    MouseArea { id: rmMA; anchors.fill: parent; anchors.margins: -6; hoverEnabled: true; onClicked: root.removeItem(index) }
                }
            }
        }
        Text { visible: root.items.length === 0; text: "Пока пусто"; color: K.Theme.dim; font.pixelSize: 12 }
        Row {
            spacing: 6
            TextField {
                id: addField
                width: menu.colW - 3 * 34 - 3 * 6; height: 28; font.pixelSize: 12
                color: K.Theme.text; placeholderTextColor: K.Theme.dim; placeholderText: "10m · 07:30 название"
                background: Rectangle { radius: 8; color: "#1effffff"; border.color: root.badInput ? K.Theme.bad : K.Theme.faint }
                onTextEdited: root.badInput = false
                onAccepted: { if (root.addItem(text)) text = ""; else root.badInput = true }
            }
            Repeater {
                model: ["5m", "10m", "30m"]
                Rectangle {
                    width: 34; height: 28; radius: 8; color: chipMA.containsMouse ? "#33ffffff" : "#1effffff"
                    Text { anchors.centerIn: parent; text: modelData; color: K.Theme.text; font.pixelSize: 12 }
                    MouseArea { id: chipMA; anchors.fill: parent; hoverEnabled: true; onClicked: root.addItem(modelData) }
                }
            }
        }
    }
}
