pragma Singleton
import QtQuick
import Quickshell
import Quickshell.Io
import Quickshell.Hyprland

QtObject {
    id: root
    readonly property bool devMode: Quickshell.env("KOZYREK_DEV") === "1"

    // Fullscreen on the focused workspace. Read from the workspace object, not
    // from the `fullscreen 0/1` event: that event desyncs when you leave
    // fullscreen by switching workspace or closing the window. The workspace
    // list is refreshed (socket request, no fork) on every event that can
    // change it.
    readonly property bool fsActive: Hyprland.focusedWorkspace?.hasFullscreen ?? false
    property Connections _hypr: Connections {
        target: Hyprland
        function onRawEvent(event) {
            switch (event.name) {
            case "fullscreen": case "workspace": case "workspacev2":
            case "focusedmon": case "activewindow": case "activewindowv2":
            case "closewindow": case "openwindow": case "movewindowv2":
                Hyprland.refreshWorkspaces()
            }
        }
    }

    // At most one popup open at a time; Popup.qml registers itself here.
    property var activePopup: null

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
