import QtQuick
import Quickshell
import Quickshell.Io
import "modules" as M
import "zhor" as Z

ShellRoot {
    Bar {
        left:   [ M.Power {}, M.Workspaces {}, M.WindowTitle {} ]
        center: [ Z.Zhor { id: zhor; fsActive: Kozy.fsActive; cPill: "#1effffff"; pillRadius: Theme.radius; anchors.verticalCenter: parent.verticalCenter }, M.Clock {} ]
        right:  [ M.Vpn {}, M.Dnd {}, M.Network {}, M.Bluetooth {}, M.Battery {}, M.Tray {} ]
    }
    Osd {}
    // Two notification daemons cannot share the D-Bus name; in dev mode the
    // live HyprPanel owns it, so this stays unloaded there.
    Loader { active: !Kozy.devMode; sourceComponent: Notifications {} }

    IpcHandler {
        target: "zhor"
        function toggle(): void { zhor.toggle() }
    }
}
