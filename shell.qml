//@ pragma UseQApplication
// ^ needed for tray menus (QsMenuAnchor); takes effect on restart, not reload.
import QtQuick
import Quickshell
import Quickshell.Io
import "modules" as M
import "zhor" as Z

ShellRoot {
    Bar {
        left:   [ M.Power {}, M.Workspaces {}, M.WindowTitle {} ]
        center: [ M.Clock {} ]
        right:  [ Z.Zhor { id: zhor; fsActive: Kozy.fsActive; cPill: "#1effffff"; pillRadius: Theme.radius; anchors.verticalCenter: parent.verticalCenter }, M.Vpn {}, M.Layout {}, M.Dnd {}, M.Volume {}, M.Network {}, M.Bluetooth {}, M.Battery {}, M.Tray {} ]
    }
    Osd {}
    // Two notification daemons cannot share the D-Bus name; in dev mode the
    // live HyprPanel owns it, so this stays unloaded there.
    Loader { active: !Kozy.devMode; sourceComponent: Notifications {} }

    IpcHandler {
        target: "zhor"
        function toggle(): void { zhor.toggle() }
    }
    IpcHandler {
        target: "dnd"
        function toggle(): void { Kozy.dnd = !Kozy.dnd }
    }
}
