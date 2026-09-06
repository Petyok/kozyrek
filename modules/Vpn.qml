import QtQuick
import Quickshell
import Quickshell.Io
import "../" as K
import "../lib/parse.js" as Parse

// One long-lived `ip monitor link` instead of three 2-second pollers. Wireguard
// and tun devices are invisible to Quickshell.Networking (DeviceType knows only
// Wifi/Wired), so this stays on iproute2.
Row {
    id: root
    spacing: 6
    property var up: ({})
    readonly property var ifaces: [
        { name: "wg0",         label: "[WG-SM]", cmd: ["kitty", "-e", "sudo", "wg", "show"] },
        { name: "wg1",         label: "[WG-GE]", cmd: ["kitty", "-e", "sudo", "wg", "show"] },
        { name: "singbox_tun", label: "[RAY]",   cmd: ["kitty", "-e", "sudo", "systemctl", "status", "sing-box"] },
    ]
    function set(name, state) { var u = Object.assign({}, up); u[name] = state; up = u }

    Process {
        command: ["ip", "-j", "link"]
        running: true
        stdout: StdioCollector {
            onStreamFinished: {
                try {
                    var names = JSON.parse(text).map(l => l.ifname), u = {}
                    root.ifaces.forEach(i => u[i.name] = names.includes(i.name))
                    root.up = u
                } catch (e) { console.log("vpn: ip -j link:", e) }
            }
        }
    }
    Process {
        id: mon
        command: ["ip", "monitor", "link"]
        running: true
        stdout: SplitParser { onRead: line => { var r = Parse.ipMonitor(line); if (r) root.set(r.name, !r.deleted) } }
        onExited: restart.start()
    }
    Timer { id: restart; interval: 3000; onTriggered: mon.running = true }

    Repeater {
        model: root.ifaces
        K.Pill {
            visible: root.up[modelData.name] === true
            Text { text: modelData.label; color: K.Theme.accent; font.pixelSize: K.Theme.fontPx; font.bold: true; anchors.verticalCenter: parent.verticalCenter }
            onClicked: Quickshell.execDetached(modelData.cmd)
        }
    }
}
