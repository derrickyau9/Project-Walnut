import QtQuick 2.15
import QtQuick.Controls 2.15

FocusScope {
    id: taskbar
    objectName: "cameraTaskbar"
    property var shell
    property var theme
    Typography { id:typography }
    property bool expanded: true
    property real swipeThreshold: 32
    property real tapSlop: 8
    property var _activeGesture: null
    property real gestureStartX: 0
    property real gestureStartY: 0
    property bool dragActive: false
    property bool keyboardNavigation:false
    readonly property bool gestureActive: _activeGesture !== null
    readonly property bool keyboardFocusActive: activeFocus && expanded
    readonly property var colors: theme && theme.surface ? theme : ({surface:"#111111",raised:"#222222",layer:"#333333",text:"#ffffff",muted:"#cccccc",helper:"#aaaaaa",line:"#444444",accent:"#666666"})
    signal collapseRequested()
    signal expandRequested()
    signal navigateRequested(string appId)
    signal dragProgressRequested(real progress)
    signal dragFinished(bool expanded)

    implicitWidth: 72
    implicitHeight: 480
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
        keyboardNavigation=false
        source.preventStealing=false
        source.pressX = x
        source.pressY = y
        source.moved = false
        var point=source.mapToItem(taskbar.parent,x,y)
        gestureStartX=point.x
        gestureStartY=point.y
        dragActive=false
        _activeGesture = source
    }
    function updateGesture(source, x, y) {
        var point=source.mapToItem(taskbar.parent,x,y)
        var dx=point.x-gestureStartX,dy=point.y-gestureStartY
        if (Math.abs(dx) > tapSlop || Math.abs(dy) > tapSlop)
            source.moved = true
        if(dx < -tapSlop && Math.abs(dx) > Math.abs(dy)*1.5) {
            source.preventStealing=true
            dragActive=true
            dragProgressRequested(Math.max(0,Math.min(1,1+dx/taskbar.width)))
        }
    }
    function cancelGesture(source) {
        if (_activeGesture === source) {
            _activeGesture = null
            if(dragActive) { dragActive=false; dragFinished(true) }
        }
    }
    function finishGesture(source, x, y) {
        updateGesture(source, x, y)
        var point=source.mapToItem(taskbar.parent,x,y)
        var dx = point.x-gestureStartX
        var dy = point.y-gestureStartY
        var horizontal = Math.abs(dx) >= swipeThreshold && Math.abs(dx) >= 1.5*Math.abs(dy)
        var tap = !source.moved
        var dragged=dragActive
        dragActive=false
        cancelGesture(source)
        // Resolve only after release: the parent may animate x immediately.
        if (horizontal) {
            dragFinished(dx >= 0)
            return false
        }
        if(dragged) dragFinished(true)
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

    Flickable {
        id:navigationViewport
        anchors.left:parent.left;anchors.right:parent.right;anchors.top:parent.top;anchors.bottom:collapseButton.top
        anchors.topMargin:8;anchors.bottomMargin:8
        clip:true
        contentWidth:width;contentHeight:entries.height+8
        boundsBehavior:Flickable.StopAtBounds
        ScrollIndicator.vertical:ScrollIndicator { }
        function reveal(entry) {
            if(entry.y<contentY) contentY=entry.y
            else if(entry.y+entry.height>contentY+height) contentY=entry.y+entry.height-height
        }
    Column {
        id: entries
        anchors.top: parent.top
        anchors.topMargin: 0
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 4
        Repeater {
            model: taskbar.shell ? taskbar.shell.apps : []
            Button {
                id: entry
                objectName: "taskbar-" + modelData.id
                width: 64
                height: 64
                padding: 0
                autoRepeat: false
                enabled: modelData.enabled
                property bool selected: taskbar.shell && taskbar.shell.currentApp === modelData.id
                onSelectedChanged:if(selected) selectionReveal.restart()
                Timer {id:selectionReveal;interval:0;onTriggered:navigationViewport.reveal(entry)}
                Accessible.name: qsTr(modelData.label) || qsTr(modelData.title)
                Accessible.description: qsTr(modelData.reason) || qsTr("Open ") + qsTr(modelData.title)
                onClicked: taskbar.navigateRequested(modelData.id)
                Keys.priority: Keys.BeforeItem
                Keys.onPressed: {
                    taskbar.keyboardNavigation=true
                    if (taskbar.activationKey(event.key)) {
                        event.accepted = true
                        if (!event.isAutoRepeat) taskbar.navigateRequested(modelData.id)
                    }
                }
                Keys.onReleased: if (taskbar.activationKey(event.key)) event.accepted = true
                background: Rectangle {
                    radius: 4
                    color: iconPointer.pressed ? taskbar.colors.layer : entry.selected || iconPointer.containsMouse ? taskbar.colors.raised : taskbar.colors.surface
                    border.width: entry.activeFocus ? 2 : entry.selected ? 1 : 0
                    border.color: entry.activeFocus ? taskbar.colors.text : taskbar.colors.line
                    Rectangle { anchors.left:parent.left; anchors.verticalCenter:parent.verticalCenter; width:2; height:18; color:taskbar.colors.text; visible:entry.selected }
                }
                contentItem: Item {
                    DaOSIcon {
                        anchors.centerIn: parent
                        width: 24; height: 24
                        kind: modelData.id
                        ink: !entry.enabled ? taskbar.colors.helper : taskbar.colors.text
                    }
                }
                ToolTip {
                    visible:iconPointer.containsMouse && !iconPointer.pressed
                    delay:600
                    text:modelData.label || modelData.title
                    font.family:typography.uiFamily
                    font.pixelSize:typography.captionSize
                }
                MouseArea {
                    id: iconPointer
                    anchors.fill: parent
                    acceptedButtons: Qt.LeftButton
                    hoverEnabled: true
                    preventStealing: false
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
    }

    Button {
        id: collapseButton
        objectName: "taskbarCollapseButton"
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 8
        anchors.horizontalCenter: parent.horizontalCenter
        width: 64; height: 64
        padding: 0
        autoRepeat: false
        Accessible.name: qsTr("Hide Taskbar")
        Accessible.description: qsTr("Hide the left icon Taskbar. Swipe right from the left screen edge to reveal it.")
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
        ToolTip.text: qsTr("Hide Taskbar")
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
