import QtQuick 2.15
import QtQuick.Controls 2.15

FocusScope {
    id: taskbar
    objectName: "cameraTaskbar"
    property var shell
    property var theme
    property bool expanded: true
    property real swipeThreshold: 32
    property real tapSlop: 8
    property var _activeGesture: null
    readonly property bool gestureActive: _activeGesture !== null
    readonly property bool keyboardFocusActive: activeFocus && expanded
    readonly property var colors: theme && theme.surface ? theme : ({surface:"#111111",raised:"#222222",layer:"#333333",text:"#ffffff",muted:"#cccccc",helper:"#aaaaaa",line:"#444444",accent:"#666666"})
    signal collapseRequested()
    signal expandRequested()
    signal navigateRequested(string appId)

    implicitWidth: 72
    implicitHeight: 600
    width: implicitWidth
    height: implicitHeight
    clip: true
    enabled: expanded
    onExpandedChanged: {
        if (!expanded) {
            _activeGesture = null
            focus = false
        }
    }

    function beginGesture(source, x, y) {
        source.pressX = x
        source.pressY = y
        source.moved = false
        _activeGesture = source
    }
    function updateGesture(source, x, y) {
        if (Math.abs(x-source.pressX) > tapSlop || Math.abs(y-source.pressY) > tapSlop)
            source.moved = true
    }
    function cancelGesture(source) {
        if (_activeGesture === source)
            _activeGesture = null
    }
    function finishGesture(source, x, y) {
        updateGesture(source, x, y)
        var dx = x-source.pressX
        var dy = y-source.pressY
        var horizontal = Math.abs(dx) >= swipeThreshold && Math.abs(dx) >= 1.5*Math.abs(dy)
        var tap = !source.moved
        cancelGesture(source)
        // Resolve only after release: the parent may animate x immediately.
        if (horizontal) {
            if (dx < 0) collapseRequested()
            else expandRequested()
            return false
        }
        return tap
    }
    function activationKey(key) {
        return key === Qt.Key_Space || key === Qt.Key_Return || key === Qt.Key_Enter
    }

    Rectangle { anchors.fill: parent; color: taskbar.colors.surface }
    Rectangle { anchors.right: parent.right; height: parent.height; width: 1; color: taskbar.colors.line }
    MouseArea {
        id: railPointer
        objectName: "taskbarSwipeSurface"
        anchors.fill: parent
        acceptedButtons: Qt.LeftButton
        preventStealing: true
        property real pressX: 0
        property real pressY: 0
        property bool moved: false
        onPressed: taskbar.beginGesture(railPointer, mouse.x, mouse.y)
        onPositionChanged: if (pressed) taskbar.updateGesture(railPointer, mouse.x, mouse.y)
        onReleased: taskbar.finishGesture(railPointer, mouse.x, mouse.y)
        onCanceled: taskbar.cancelGesture(railPointer)
        Component.onDestruction: taskbar.cancelGesture(railPointer)
    }

    Column {
        id: entries
        anchors.top: parent.top
        anchors.topMargin: 16
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 4
        Repeater {
            model: taskbar.shell ? taskbar.shell.apps : []
            Button {
                id: entry
                objectName: "taskbar-" + modelData.id
                width: 56
                height: 48
                padding: 0
                autoRepeat: false
                enabled: modelData.enabled
                property bool selected: taskbar.shell && taskbar.shell.currentApp === modelData.id
                Accessible.name: modelData.title
                Accessible.description: modelData.reason || "Open " + modelData.title
                onClicked: taskbar.navigateRequested(modelData.id)
                Keys.priority: Keys.BeforeItem
                Keys.onPressed: {
                    if (taskbar.activationKey(event.key)) {
                        event.accepted = true
                        if (!event.isAutoRepeat) taskbar.navigateRequested(modelData.id)
                    }
                }
                Keys.onReleased: if (taskbar.activationKey(event.key)) event.accepted = true
                background: Rectangle {
                    radius: 8
                    color: entry.selected ? taskbar.colors.accent : iconPointer.pressed ? taskbar.colors.layer : iconPointer.containsMouse ? taskbar.colors.raised : taskbar.colors.surface
                    border.width: entry.activeFocus ? 2 : 0
                    border.color: taskbar.colors.text
                }
                contentItem: Item {
                    DaOSIcon {
                        anchors.centerIn: parent
                        width: 24; height: 24
                        kind: modelData.id
                        ink: !entry.enabled ? taskbar.colors.helper : entry.selected ? "#ffffff" : taskbar.colors.text
                    }
                }
                ToolTip.visible: iconPointer.containsMouse && !iconPointer.pressed
                ToolTip.delay: 600
                ToolTip.text: modelData.title
                MouseArea {
                    id: iconPointer
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    hoverEnabled: true
                    preventStealing: true
                    property real pressX: 0
                    property real pressY: 0
                    property bool moved: false
                    onPressed: {
                        entry.forceActiveFocus(Qt.MouseFocusReason)
                        taskbar.beginGesture(iconPointer, mouse.x, mouse.y)
                    }
                    onPositionChanged: if (pressed) taskbar.updateGesture(iconPointer, mouse.x, mouse.y)
                    onReleased: {
                        if (taskbar.finishGesture(iconPointer, mouse.x, mouse.y))
                            taskbar.navigateRequested(modelData.id)
                    }
                    onCanceled: taskbar.cancelGesture(iconPointer)
                    Component.onDestruction: taskbar.cancelGesture(iconPointer)
                }
            }
        }
    }

    Button {
        id: collapseButton
        objectName: "taskbarCollapseButton"
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 16
        anchors.horizontalCenter: parent.horizontalCenter
        width: 56; height: 48
        padding: 0
        autoRepeat: false
        Accessible.name: "Hide Taskbar"
        Accessible.description: "Hide the left icon Taskbar. Swipe right from the left screen edge to reveal it."
        onClicked: taskbar.collapseRequested()
        Keys.priority: Keys.BeforeItem
        Keys.onPressed: {
            if (taskbar.activationKey(event.key)) {
                event.accepted = true
                if (!event.isAutoRepeat) taskbar.collapseRequested()
            }
        }
        Keys.onReleased: if (taskbar.activationKey(event.key)) event.accepted = true
        background: Rectangle {
            radius: 8
            color: collapsePointer.pressed ? taskbar.colors.layer : collapsePointer.containsMouse ? taskbar.colors.raised : taskbar.colors.surface
            border.width: collapseButton.activeFocus ? 2 : 0
            border.color: taskbar.colors.text
        }
        contentItem: Item { DaOSIcon { anchors.centerIn: parent; width: 24; height: 24; kind: "back"; ink: taskbar.colors.text } }
        ToolTip.visible: collapsePointer.containsMouse && !collapsePointer.pressed
        ToolTip.delay: 600
        ToolTip.text: "Hide Taskbar"
        MouseArea {
            id: collapsePointer
            anchors.fill: parent
            acceptedButtons: Qt.LeftButton
            hoverEnabled: true
            preventStealing: true
            property real pressX: 0
            property real pressY: 0
            property bool moved: false
            onPressed: {
                collapseButton.forceActiveFocus(Qt.MouseFocusReason)
                taskbar.beginGesture(collapsePointer, mouse.x, mouse.y)
            }
            onPositionChanged: if (pressed) taskbar.updateGesture(collapsePointer, mouse.x, mouse.y)
            onReleased: {
                if (taskbar.finishGesture(collapsePointer, mouse.x, mouse.y))
                    taskbar.collapseRequested()
            }
            onCanceled: taskbar.cancelGesture(collapsePointer)
            Component.onDestruction: taskbar.cancelGesture(collapsePointer)
        }
    }
}
