import QtQuick
import Quickshell
import "modules" as M

ShellRoot {
    Bar {
        left:   [ M.Power {}, M.Workspaces {}, M.WindowTitle {} ]
        center: [ M.Clock {} ]
        right:  []
    }
}
