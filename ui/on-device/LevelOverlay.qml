import QtQuick 2.15

Rectangle {
    id: level
    objectName: "levelOverlay"
    property var controller: null
    readonly property bool ready: !!controller && controller.available
    readonly property color indicatorColor: ready && controller.aligned ? "#6de39a" : "#eeeeee"
    width: 340; height: 118; radius: 8
    color: "#111111"; border.color: "#666666"
    visible: !!controller && controller.enabled
    function angle(value) { return (value>0 ? "+" : "")+Number(value).toFixed(1)+"°" }
    Text {
        anchors.horizontalCenter:parent.horizontalCenter;y:8
        text:qsTr("电子水平仪");color:"#eeeeee";font.pixelSize:14
    }
    Text {
        objectName:"levelStateLabel";anchors.horizontalCenter:parent.horizontalCenter;y:29
        text:level.ready ? (controller.isDemo ? qsTr("DEMO · 示例姿态") : qsTr("外部姿态数据")) : controller && controller.state === "stale" ? qsTr("姿态数据已过期") : controller && controller.state === "invalid" ? qsTr("姿态数据无效") : qsTr("未连接 / 无可用姿态")
        color:level.indicatorColor;font.pixelSize:12
    }
    Item {
        anchors.horizontalCenter:parent.horizontalCenter;y:48;width:148;height:36;clip:true
        Rectangle {anchors.centerIn:parent;width:144;height:1;color:"#555555"}
        Rectangle {anchors.centerIn:parent;width:1;height:30;color:"#555555"}
        Rectangle {
            objectName:"levelRollIndicator";x:24;width:100;height:3;radius:1
            visible:level.ready
            y:16+Math.max(-13,Math.min(13,level.ready ? controller.pitchDeg : 0))
            rotation:level.ready ? -controller.rollDeg : 0
            transformOrigin:Item.Center;color:level.indicatorColor;antialiasing:true
        }
    }
    Text {objectName:"levelRollValue";x:16;y:90;text:qsTr("横滚")+"  "+(level.ready ? level.angle(controller.rollDeg) : "—");color:level.indicatorColor;font.pixelSize:16}
    Text {objectName:"levelPitchValue";anchors.right:parent.right;anchors.rightMargin:16;y:90;text:qsTr("俯仰")+"  "+(level.ready ? level.angle(controller.pitchDeg) : "—");color:level.indicatorColor;font.pixelSize:16}
}
