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
        // Screen recording sits next to the clock: the right zone already reaches it,
        // and a centred pair keeps clear of both sides while it records.
        center: [ M.Clock {}, M.ScreenRec { id: screenrec } ]
        // Rec first: the right Row is anchored right, so the one pill whose width
        // changes while it works shifts nothing when it is leftmost.
        right:  [ M.Rec { id: rec }, Z.Zhor { id: zhor; fsActive: Kozy.fsActive; cPill: "#1effffff"; pillRadius: Theme.radius; anchors.verticalCenter: parent.verticalCenter }, M.Collab { svc: collab }, M.Vpn {}, M.Layout {}, M.Tv { on: tv.open; onClicked: tv.toggle() }, M.Dnd {}, M.Volume {}, M.Ping { id: pingPill }, M.Network { ping: pingPill }, M.Bluetooth {}, M.Battery {}, M.Tray {} ]
    }
    Osd {}
    Dictation { id: dict }
    Tv { id: tv }
    Collab { id: collab }
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
    IpcHandler {
        target: "rec"
        function toggle(): void { rec.toggle() }
        function refresh(): void { rec.refresh() }
    }
    IpcHandler {
        target: "screenrec"
        function refresh(): void { screenrec.refresh() }
    }
    IpcHandler {
        target: "dict"
        function toggle(): void { dict.toggle() }
    }
    IpcHandler {
        target: "tv"
        function toggle(): void { tv.toggle() }
        function key(names: string): void { tv.key(names) }
    }
    IpcHandler {
        target: "collab"
        function toggle(): void { collab.toggle() }
    }
}
