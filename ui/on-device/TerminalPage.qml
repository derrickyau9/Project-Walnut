import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15
import DaOS.Terminal 1.0

Rectangle {
    id: page
    color: "#000000"
    property bool completed:false
    signal closeRequested()
    onVisibleChanged: if(completed) { if (visible) surface.open(); else surface.close() }
    Component.onCompleted: { completed=true; if (visible) surface.open() }
    ColumnLayout {
        anchors.fill: parent
        anchors.bottomMargin: softKeyboard.isOpen ? 252 : 0
        spacing: 3
        RowLayout {
            Layout.fillWidth: true
            Layout.preferredHeight: 64
            Button { text: qsTr("返回设置"); focusPolicy: Qt.NoFocus; implicitHeight:64; onClicked:page.closeRequested() }
            Label { text: qsTr("终端"); color:"white"; Layout.fillWidth:true; font.family:typeof cameraTitleFont !== "undefined" ? cameraTitleFont : "Noto Sans CJK SC"; font.pixelSize:24; font.weight:Font.Medium; font.letterSpacing:1.2 }
            Button { text: qsTr("打开"); focusPolicy:Qt.NoFocus; implicitHeight:64; enabled:!surface.running; onClicked:surface.open() }
            Button { text: qsTr("关闭"); focusPolicy:Qt.NoFocus; implicitHeight:64; enabled:surface.running; onClicked:surface.close() }
        }
        RowLayout {
            Layout.fillWidth:true
            // Text-entry accessory keys share the compact keyboard exception.
            Layout.preferredHeight:44
            Button { text:qsTr("Ctrl+C"); focusPolicy:Qt.NoFocus; implicitHeight:44; onClicked:surface.sendKey("interrupt") }
            Button { text:qsTr("Tab"); focusPolicy:Qt.NoFocus; implicitHeight:44; onClicked:surface.sendKey("tab") }
            Button { text:qsTr("Esc"); focusPolicy:Qt.NoFocus; implicitHeight:44; onClicked:surface.sendKey("escape") }
            Button { text:"\u2191"; focusPolicy:Qt.NoFocus; implicitHeight:44; onClicked:surface.sendKey("up") }
            Button { text:"\u2193"; focusPolicy:Qt.NoFocus; implicitHeight:44; onClicked:surface.sendKey("down") }
            Button { text:qsTr("PgUp"); focusPolicy:Qt.NoFocus; implicitHeight:44; onClicked:surface.scroll(-1) }
            Button { text:qsTr("PgDn"); focusPolicy:Qt.NoFocus; implicitHeight:44; onClicked:surface.scroll(1) }
        }
        TerminalSurface {
            id:surface
            objectName:"localTerminalSurface"
            Layout.fillWidth:true
            Layout.fillHeight:true
        }
        Label { text:qsTr(surface.message); color:"#aaaaaa"; font.pixelSize:14; Layout.leftMargin:6; Layout.preferredHeight:18 }
    }
}
