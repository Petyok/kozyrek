pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland

QtObject {
    id: root
    readonly property bool devMode: Quickshell.env("KOZYREK_DEV") === "1"

    // Real fullscreen on the focused workspace. The workspace's `hasfullscreen`
    // is also true for a maximized window (`fullscreen 1`), which would hide
    // every popup over a maximized kitty, so the window's mode bit 2 decides.
    // Read from the objects, not from the `fullscreen 0/1` event: that event
    // desyncs when you leave fullscreen by switching workspace or closing the
    // window. Workspaces and toplevels are refreshed (socket request, no fork)
    // on every event that can change them.
    readonly property bool fsActive: {
        const ws = Hyprland.focusedWorkspace
        if (!ws?.hasFullscreen) return false
        return ws.toplevels.values.some(t => ((t.lastIpcObject?.fullscreen ?? 0) & 2) !== 0)
    }
    property Connections _hypr: Connections {
        target: Hyprland
        function onRawEvent(event) {
            switch (event.name) {
            case "fullscreen": case "workspace": case "workspacev2":
            case "focusedmon": case "activewindow": case "activewindowv2":
            case "closewindow": case "openwindow": case "movewindowv2":
                Hyprland.refreshWorkspaces()
                Hyprland.refreshToplevels()
            }
        }
    }

    // The bar window, registered by Bar.qml. Popups must anchor to the real
    // window object: the `QsWindow.window` attached property hands out the
    // interface proxy, and with that PopupAnchor silently positions at (0,0).
    property var bar: null
    // At most one popup open at a time; Popup.qml registers itself here.
    property var activePopup: null
    // The "show hidden icons" pill (modules/Overflow.qml), registered by itself.
    property var overflow: null

    // Do-not-disturb survives process restarts (PersistentProperties only
    // survives reloads), so it lives in a tiny JSON under the state dir.
    property bool dnd: false
    property FileView _store: FileView {
        path: Quickshell.statePath("dnd.json")
        watchChanges: true
        printErrors: false           // first run: no file yet, the adapter default is the answer
        adapter: JsonAdapter { id: adapter; property bool dnd: false }
        onLoaded: root.dnd = adapter.dnd
    }
    onDndChanged: { if (adapter.dnd !== dnd) { adapter.dnd = dnd; _store.writeAdapter() } }
}
