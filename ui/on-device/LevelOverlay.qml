import QtQuick 2.15

Item {
    id: level
    objectName: "levelOverlay"
    property var controller: null
    readonly property bool ready: !!controller && controller.available
    readonly property color indicatorColor: ready && controller.aligned ? "#6de39a" : "#eeeeee"
    width: 244; height: 54
    visible: !!controller && controller.enabled
    function angle(value) { return (value>0 ? "+" : "")+Number(value).toFixed(1)+"°" }
    Text {
        objectName:"levelStateLabel";anchors.horizontalCenter:parent.horizontalCenter;y:0
        visible:!level.ready || controller.isDemo
        text:level.ready ? (controller.isDemo ? qsTr("DEMO · 示例姿态") : qsTr("外部姿态数据")) : controller && controller.state === "stale" ? qsTr("姿态数据已过期") : controller && controller.state === "invalid" ? qsTr("姿态数据无效") : qsTr("未连接 / 无可用姿态")
        color:level.indicatorColor;font.pixelSize:10
        style:Text.Outline;styleColor:"#99000000"
    }
    Item {
        anchors.horizontalCenter:parent.horizontalCenter;y:16;width:148;height:22;clip:true
        Rectangle {anchors.centerIn:parent;width:144;height:2;color:"#99000000"}
        Rectangle {anchors.centerIn:parent;width:144;height:1;color:"#99ffffff"}
        Rectangle {anchors.centerIn:parent;width:1;height:18;color:"#99ffffff"}
        Rectangle {
            objectName:"levelRollIndicator";x:24;width:100;height:3;radius:1
            visible:level.ready
            y:9+Math.max(-5,Math.min(5,level.ready ? controller.pitchDeg : 0))
            rotation:level.ready ? -controller.rollDeg : 0
            transformOrigin:Item.Center;color:level.indicatorColor;antialiasing:true
        }
    }
    Text {objectName:"levelRollValue";x:12;y:39;text:qsTr("横滚")+"  "+(level.ready ? level.angle(controller.rollDeg) : "—");color:level.indicatorColor;font.pixelSize:11;style:Text.Outline;styleColor:"#99000000"}
    Text {objectName:"levelPitchValue";anchors.right:parent.right;anchors.rightMargin:12;y:39;text:qsTr("俯仰")+"  "+(level.ready ? level.angle(controller.pitchDeg) : "—");color:level.indicatorColor;font.pixelSize:11;style:Text.Outline;styleColor:"#99000000"}
}
