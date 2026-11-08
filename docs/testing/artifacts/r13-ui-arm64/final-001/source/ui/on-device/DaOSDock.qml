import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: dock
    property var shell
    property var theme
    property bool expanded: false
    property bool expandAvailable: true
    signal commandRequested()
    signal launcherRequested()
    color: theme.rail || theme.surface
    border.color: theme.line
    radius: 22

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 7
        Button {
            objectName: "appLauncherButton"
            Layout.fillWidth: true
            Layout.preferredHeight: 46
            onClicked: dock.launcherRequested()
            Accessible.name: "DaOS applications"
            background: Rectangle { color: parent.down ? dock.theme.softAccent : (dock.theme.rail || dock.theme.surface); radius: 12 }
            contentItem: Row {
                spacing: 10
                Rectangle {
                    width: 36; height: 36; radius: 11; color: dock.theme.accent
                    Text {
            font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
 anchors.centerIn: parent; text: "D"; font.pixelSize: 23; font.weight: Font.DemiBold; color: "#ffffff" }
                }
                Text {
            font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
 anchors.verticalCenter: parent.verticalCenter; visible: dock.expanded; text: "DaOS"; color: dock.theme.text; font.pixelSize: 20; font.weight: Font.DemiBold }
            }
            ToolTip.visible: hovered
            ToolTip.text: "Applications"
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: dock.theme.line }
        Button {
            objectName: "commandPaletteButton"
            Layout.fillWidth: true
            Layout.preferredHeight: 40
            onClicked: dock.commandRequested()
            Accessible.name: "Search apps and settings"
            background: Rectangle { color: parent.hovered ? dock.theme.softAccent : (dock.theme.rail || dock.theme.surface); radius: 12 }
            contentItem: Row {
                spacing: 12
                DaOSIcon { kind: "search"; ink: dock.theme.muted; anchors.verticalCenter: parent.verticalCenter }
                Text {
            font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
 visible: dock.expanded; text: "Search  /  Ctrl K"; color: dock.theme.muted; font.pixelSize: 12; anchors.verticalCenter: parent.verticalCenter }
            }
            ToolTip.visible: hovered
            ToolTip.text: "Search  ·  Ctrl K"
        }
        Repeater {
            model: dock.shell.apps
            delegate: Button {
                id: entry
                objectName: "nav-" + modelData.id
                Layout.fillWidth: true
                Layout.preferredHeight: 44
                enabled: modelData.enabled
                onClicked: dock.shell.navigate(modelData.id)
                Accessible.name: modelData.title
                Accessible.description: modelData.reason || "Open " + modelData.title
                background: Rectangle {
                    radius: 12
                    color: dock.shell.currentApp === modelData.id ? dock.theme.softAccent : entry.down || entry.hovered ? dock.theme.raised : (dock.theme.rail || dock.theme.surface)
                    border.width: entry.activeFocus ? 2 : 0
                    border.color: dock.theme.accent
                }
                contentItem: Row {
                    spacing: 12
                    DaOSIcon { kind: modelData.id; ink: dock.shell.currentApp === modelData.id ? dock.theme.text : dock.theme.muted; anchors.verticalCenter: parent.verticalCenter }
                    Text {
            font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"

                        visible: dock.expanded
                        text: modelData.title
                        color: dock.theme.text
                        font.pixelSize: 13
                        font.weight: dock.shell.currentApp === modelData.id ? Font.DemiBold : Font.Normal
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                ToolTip.visible: hovered && !dock.expanded
                ToolTip.text: modelData.title
            }
        }
        Item { Layout.fillHeight: true }
        Button {
            objectName: "sidebarToggleButton"
            Layout.fillWidth: true
            Layout.preferredHeight: 36
            onClicked: {
                if (dock.expandAvailable) dock.shell.toggleMenu()
                else dock.launcherRequested()
            }
            Accessible.name: dock.expandAvailable ? "Expand or collapse sidebar" : "Show applications"
            background: Rectangle { color: parent.hovered ? dock.theme.raised : (dock.theme.rail || dock.theme.surface); radius: 10 }
            contentItem: Row {
                spacing: 12
                DaOSIcon { kind: "menu"; ink: dock.theme.muted }
                Text {
            font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
 visible: dock.expanded; text: "Camera workspace"; color: dock.theme.muted; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter }
            }
        }
        Text {
            font.family: Qt.platform.os === "windows" ? "Segoe UI" : "Sans Serif"
 Layout.alignment: Qt.AlignHCenter; text: "FF-1"; color: dock.theme.muted; font.pixelSize: 10; font.letterSpacing: 1 }
    }
}
