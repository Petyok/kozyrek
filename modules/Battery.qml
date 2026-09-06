import QtQuick
import Quickshell
import Quickshell.Services.UPower
import "../" as K
import "../lib/parse.js" as Parse

K.Pill {
    id: root
    readonly property var dev: UPower.displayDevice
    // UPowerDevice.percentage is a 0..1 fraction in Quickshell.
    readonly property int pct: Math.round((dev?.percentage ?? 0) * 100)
    readonly property bool charging: dev?.state === UPowerDeviceState.Charging || dev?.state === UPowerDeviceState.FullyCharged
    readonly property color tone: charging ? K.Theme.text : pct <= 5 ? K.Theme.bad : pct <= 15 ? K.Theme.warn : K.Theme.text
    visible: dev?.isLaptopBattery ?? false

    Text { text: Parse.batteryGlyph(root.pct, root.charging); color: root.tone; font.pixelSize: 16; font.family: K.Theme.iconFont; anchors.verticalCenter: parent.verticalCenter }
    Text { text: root.pct + "%"; color: root.tone; font.pixelSize: K.Theme.fontPx; anchors.verticalCenter: parent.verticalCenter }

    // One warning per crossing below 15 % while discharging; re-armed by charging.
    property bool warned: false
    onPctChanged: {
        if (!charging && pct <= 15 && !warned) {
            warned = true
            Quickshell.execDetached(["notify-send", "-a", "kozyrek", "-u", "critical", "-i", "battery-caution",
                                     "Battery low", pct + "% left"])
        }
    }
    onChargingChanged: if (charging) warned = false
}
