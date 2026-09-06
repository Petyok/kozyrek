import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Wayland
import Quickshell.Hyprland

ShellRoot {
    id: root
    // ── config ──────────────────────────────────────────────
    property real lat: 41.64
    property real lon: 41.64
    property string place: "Batumi"
    property int  refreshMs: 15 * 60 * 1000

    // палитра — под тёмный полупрозрачный бар (#705492 фиол. акцент)
    property color cBg:     "#e6141826"
    property color cAccent: "#b69ad6"
    property color cText:   "#eef1fa"
    property color cDim:    "#a6b6c4d8"
    property color cFaint:  "#33ffffff"
    property color cToday:  "#705492"

    // ── time ────────────────────────────────────────────────
    property var now: new Date()
    Timer { interval: 1000; repeat: true; running: true; onTriggered: root.now = new Date() }
    function hhmm(d) { return ("0"+d.getHours()).slice(-2)+":"+("0"+d.getMinutes()).slice(-2) }
    function ddmm(d) { return ("0"+d.getDate()).slice(-2)+"."+("0"+(d.getMonth()+1)).slice(-2) }
    function isoHM(iso) { return root.hhmm(new Date(iso)) }

    readonly property var monthNames: ["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"]
    readonly property var monthGen:   ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    readonly property var dowShort:   ["пн","вт","ср","чт","пт","сб","вс"]

    function monthGrid(year, month) {
        var first = new Date(year, month, 1)
        var startDow = (first.getDay() + 6) % 7   // пн=0
        var daysIn = new Date(year, month + 1, 0).getDate()
        var cells = []
        for (var i = 0; i < startDow; i++) cells.push(0)
        for (var d = 1; d <= daysIn; d++) cells.push(d)
        while (cells.length % 7 !== 0) cells.push(0)
        return cells
    }

    // ── weather state ───────────────────────────────────────
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

    // ── fullscreen detect ──
    // часы на Overlay → торчат поверх fullscreen-окна (ютуб). Прячем при
    // fullscreen. Событие "fullscreen" 1/0 ненадёжно (десинк при смене
    // workspace/закрытии окна → залипает). Берём авторитетно из
    // `hyprctl activeworkspace -j .hasfullscreen`, перечитываем на любое
    // событие, способное менять состояние.
    property bool fsActive: false
    Process {
        id: fsProbe
        command: ["hyprctl", "activeworkspace", "-j"]
        running: true
        stdout: StdioCollector {
            onStreamFinished: {
                try { root.fsActive = (JSON.parse(text).hasfullscreen === true) }
                catch (e) { }
            }
        }
    }
    Connections {
        target: Hyprland
        function onRawEvent(event) {
            switch (event.name) {
            case "fullscreen": case "workspace": case "workspacev2":
            case "focusedmon": case "activewindow": case "activewindowv2":
            case "closewindow": case "openwindow": case "movewindowv2":
                fsProbe.running = false; fsProbe.running = true
            }
        }
    }

    // ── popup open state ────────────────────────────────────
    property bool menuOpen: false
    // просматриваемый месяц календаря (отвязан от сегодня — мотается стрелками)
    property int viewYear:  now.getFullYear()
    property int viewMonth: now.getMonth()
    onMenuOpenChanged: if (menuOpen) { viewYear = now.getFullYear(); viewMonth = now.getMonth() }
    function shiftMonth(delta) {
        var m = viewMonth + delta, y = viewYear
        while (m < 0)  { m += 12; y-- }
        while (m > 11) { m -= 12; y++ }
        viewMonth = m; viewYear = y
    }
    function shiftYear(delta) { viewYear += delta }
    function isToday(d) {
        return d === now.getDate() && viewMonth === now.getMonth() && viewYear === now.getFullYear()
    }

    // ── click-away catcher ──
    // слой Top (НИЖЕ меню/часов на Overlay) — иначе прозрачный catcher лёг бы
    // поверх меню и жрал клики по стрелкам. Overlay всегда выше Top.
    PanelWindow {
        visible: root.menuOpen
        anchors { top: true; bottom: true; left: true; right: true }
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Top
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        exclusionMode: ExclusionMode.Ignore
        MouseArea { anchors.fill: parent; onClicked: root.menuOpen = false }
    }

    // ── bar clock button ────────────────────────────────────
    PanelWindow {
        id: barClock
        visible: !root.fsActive
        anchors { top: true; right: true }
        margins { top: 8; right: 678 }
        implicitWidth: clockRow.implicitWidth + 26
        implicitHeight: 34
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        exclusionMode: ExclusionMode.Ignore

        Rectangle {
            anchors.fill: parent
            radius: 12
            // постоянная пилюля как кнопки бара (даже в idle)
            color: root.menuOpen ? root.cAccent
                 : (clockMA.containsMouse ? "#33ffffff" : "#1effffff")
            border.color: root.menuOpen ? "transparent" : root.cFaint
            border.width: 1
            Behavior on color { ColorAnimation { duration: 120 } }
            Row {
                id: clockRow
                anchors.centerIn: parent
                spacing: 7
                Text {
                    text: root.loaded ? root.wmo(root.curCode)[0] : "🕐"
                    font.pixelSize: 16
                    anchors.verticalCenter: parent.verticalCenter
                }
                Column {
                    spacing: -1
                    anchors.verticalCenter: parent.verticalCenter
                    Text {
                        text: root.ddmm(root.now)
                        color: root.cDim; font.pixelSize: 10
                        anchors.horizontalCenter: parent.horizontalCenter
                    }
                    Text {
                        text: root.hhmm(root.now)
                        color: root.cText; font.pixelSize: 13; font.bold: true
                        anchors.horizontalCenter: parent.horizontalCenter
                    }
                }
            }
            MouseArea {
                id: clockMA
                anchors.fill: parent
                hoverEnabled: true
                onClicked: root.menuOpen = !root.menuOpen
            }
        }
    }

    // ── dropdown menu ───────────────────────────────────────
    PanelWindow {
        id: menu
        visible: root.menuOpen && !root.fsActive
        anchors { top: true; right: true }
        margins { top: 50; right: 560 }
        implicitWidth: 320
        implicitHeight: menuCol.implicitHeight + 32
        color: "transparent"
        WlrLayershell.layer: WlrLayer.Overlay
        WlrLayershell.keyboardFocus: WlrKeyboardFocus.None
        exclusionMode: ExclusionMode.Ignore

        // клик мимо — закрыть (фоновая зона шире карточки сверху? оставим явную кнопку)
        Rectangle {
            id: menuCard
            anchors.fill: parent
            radius: 18
            color: root.cBg
            border.color: root.cFaint
            border.width: 1
            opacity: root.menuOpen ? 1 : 0
            transform: Translate { id: mslide; y: root.menuOpen ? 0 : -12 }
            Behavior on opacity { NumberAnimation { duration: 180; easing.type: Easing.OutCubic } }

            Column {
                id: menuCol
                anchors.centerIn: parent
                width: parent.width - 32
                spacing: 12

                // ── заголовок: дата ──
                Column {
                    spacing: 0
                    Text {
                        text: root.hhmm(root.now)
                        color: root.cText; font.pixelSize: 30; font.bold: true
                    }
                    Text {
                        text: root.now.getDate() + " " + root.monthGen[root.now.getMonth()] + " " + root.now.getFullYear()
                        color: root.cDim; font.pixelSize: 13
                    }
                }

                Rectangle { width: parent.width; height: 1; color: root.cFaint }

                // ── календарь ──
                Column {
                    width: parent.width
                    spacing: 6
                    // навигация: ‹‹ год ‹ месяц [Месяц Год] месяц › год ››
                    Row {
                        width: parent.width
                        height: 26
                        // компонент-кнопка стрелки
                        Repeater {
                            // левая группа: год-, месяц-
                            model: [ {t:"«", d:-12}, {t:"‹", d:-1} ]
                            Rectangle {
                                width: 26; height: 26; radius: 8
                                color: navMA.containsMouse ? "#33ffffff" : "transparent"
                                Text { anchors.centerIn: parent; text: modelData.t; color: root.cText; font.pixelSize: 16 }
                                MouseArea { id: navMA; anchors.fill: parent; hoverEnabled: true
                                    onClicked: modelData.d === -12 ? root.shiftYear(-1) : root.shiftMonth(-1) }
                            }
                        }
                        Text {
                            text: root.monthNames[root.viewMonth].charAt(0).toUpperCase() + root.monthNames[root.viewMonth].slice(1) + " " + root.viewYear
                            color: root.cText; font.pixelSize: 14; font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            anchors.verticalCenter: parent.verticalCenter
                            width: menuCol.width - 26*4
                        }
                        Repeater {
                            // правая группа: месяц+, год+
                            model: [ {t:"›", d:1}, {t:"»", d:12} ]
                            Rectangle {
                                width: 26; height: 26; radius: 8
                                color: navMA2.containsMouse ? "#33ffffff" : "transparent"
                                Text { anchors.centerIn: parent; text: modelData.t; color: root.cText; font.pixelSize: 16 }
                                MouseArea { id: navMA2; anchors.fill: parent; hoverEnabled: true
                                    onClicked: modelData.d === 12 ? root.shiftYear(1) : root.shiftMonth(1) }
                            }
                        }
                    }
                    // шапка дней недели
                    Row {
                        width: parent.width
                        Repeater {
                            model: root.dowShort
                            Text {
                                width: menuCol.width / 7
                                horizontalAlignment: Text.AlignHCenter
                                text: modelData
                                color: (index >= 5) ? root.cAccent : root.cDim
                                font.pixelSize: 11
                            }
                        }
                    }
                    // сетка
                    Grid {
                        columns: 7
                        width: parent.width
                        Repeater {
                            model: root.monthGrid(root.viewYear, root.viewMonth)
                            Item {
                                width: menuCol.width / 7
                                height: width * 0.82
                                Rectangle {
                                    anchors.centerIn: parent
                                    width: 26; height: 26; radius: 13
                                    visible: root.isToday(modelData)
                                    color: root.cToday
                                }
                                Text {
                                    anchors.centerIn: parent
                                    text: modelData > 0 ? modelData : ""
                                    color: root.isToday(modelData) ? "white" : root.cText
                                    font.pixelSize: 13
                                    font.bold: root.isToday(modelData)
                                }
                            }
                        }
                    }
                }

                Rectangle { width: parent.width; height: 1; color: root.cFaint }

                // ── погода ──
                Row {
                    spacing: 12
                    visible: root.loaded
                    Text { text: root.wmo(root.curCode)[0]; font.pixelSize: 38; anchors.verticalCenter: parent.verticalCenter }
                    Column {
                        spacing: -1
                        Row {
                            spacing: 6
                            Text { text: Math.round(root.curTemp)+"°"; color: root.cText; font.pixelSize: 26; font.bold: true }
                            Text { text: "ощущ. "+Math.round(root.curFeels)+"°"; color: root.cDim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                        }
                        Text { text: root.place + " · " + root.wmo(root.curCode)[1]; color: root.cDim; font.pixelSize: 12 }
                    }
                }
                Row {
                    width: parent.width
                    visible: root.loaded
                    Repeater {
                        model: [
                            { i: "💧", v: root.curHum + "%" },
                            { i: "💨", v: Math.round(root.curWind) + " км/ч" },
                            { i: "🌅", v: root.isoHM(root.sunrise) },
                            { i: "🌇", v: root.isoHM(root.sunset) },
                        ]
                        Row {
                            width: menuCol.width / 4; spacing: 3
                            Text { text: modelData.i; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
                            Text { text: modelData.v; color: root.cDim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                        }
                    }
                }
                // 3-дневный прогноз
                Repeater {
                    model: root.days
                    Item {
                        width: menuCol.width; height: 22
                        Text { id: dn2; text: root.dayName(modelData.date); color: root.cText; font.pixelSize: 13; width: 26; anchors.verticalCenter: parent.verticalCenter }
                        Text { id: ic2; text: root.wmo(modelData.code)[0]; font.pixelSize: 15; anchors { left: dn2.right; leftMargin: 4; verticalCenter: parent.verticalCenter } }
                        Row {
                            spacing: 2; visible: modelData.pop > 5
                            anchors { left: ic2.right; leftMargin: 10; verticalCenter: parent.verticalCenter }
                            Text { text: "💧"; font.pixelSize: 10; anchors.verticalCenter: parent.verticalCenter }
                            Text { text: modelData.pop + "%"; color: root.cAccent; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
                        }
                        Text { text: Math.round(modelData.tmax)+"° / "+Math.round(modelData.tmin)+"°"; color: root.cText; font.pixelSize: 12; anchors { right: parent.right; verticalCenter: parent.verticalCenter } }
                    }
                }
            }
        }
    }
}
