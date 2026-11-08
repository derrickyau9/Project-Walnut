import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: page
    property var cameraController
    readonly property var capabilities: cameraController ? cameraController.capabilities : ({})
    readonly property var metrics: typeof uiPerformance !== "undefined" ? uiPerformance.metrics : ({})
    property bool performanceTab: false
    signal closeRequested()
    function number(value, suffix) { return value === undefined || value === null ? qsTr("尚无样本") : Number(value).toFixed(2) + suffix }
    Rectangle { anchors.fill: parent; color: "#111111" }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 10; spacing: 8
        RowLayout {
            Layout.fillWidth: true
            MediaButton { text: qsTr("返回"); onClicked: page.closeRequested() }
            MediaButton { Layout.fillWidth: true; text: qsTr("设备能力"); emphasized: !page.performanceTab; onClicked: page.performanceTab = false }
            MediaButton { Layout.fillWidth: true; text: qsTr("实测性能"); emphasized: page.performanceTab; onClicked: page.performanceTab = true }
        }
        ScrollView {
            id: capabilityScroll
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
            ColumnLayout {
                width: capabilityScroll.availableWidth; spacing: 4
                Text { Layout.fillWidth: true; text: page.performanceTab ? qsTr("当前进程的观测数据；不等于传感器延迟或显示器端到端延迟。没有样本时不填入目标值。") : (page.capabilities.device || qsTr("未确认设备")) + " · " + (page.capabilities.simulated ? qsTr("合成相机") : qsTr("等待能力确认")) + qsTr("\n不可用功能保留原因，不显示虚构参数。"); color: "#bcbcbc"; font.pixelSize: 14; wrapMode: Text.WordWrap }
                Repeater {
                    model: page.performanceTab ? [
                        {name:"渲染帧间隔 P95",value:page.number(page.metrics.frameP95Ms," ms")},
                        {name:"输入至下一次交换 P95",value:page.number(page.metrics.inputP95Ms," ms")},
                        {name:"输入至下一次交换 P99",value:page.number(page.metrics.inputP99Ms," ms")},
                        {name:"已呈现合成预览",value:page.number(page.metrics.previewFps," fps")},
                        {name:"预览 worker 至 UI P95",value:page.number(page.metrics.previewWorkerP95Ms," ms")},
                        {name:"帧 / 输入样本数",value:(page.metrics.frameSamples || 0) + " / " + (page.metrics.inputSamples || 0)}
                    ] : [
                        {key:"capture",name:"DNG 拍摄"},{key:"preview",name:"实时预览"},{key:"playback",name:"照片回放"},
                        {key:"workflow",name:"拍摄工作流"},{key:"iso",name:"ISO 标定"},{key:"aperture",name:"电子光圈"},
                        {key:"white_balance",name:"真实白平衡"},{key:"auto_exposure",name:"自动曝光"},{key:"autofocus",name:"自动对焦"},
                        {key:"tracking",name:"主体追踪"},{key:"burst",name:"连拍"},{key:"pre_capture",name:"预拍摄"},
                        {key:"video",name:"视频录制"},{key:"histogram",name:"真实直方图"},{key:"peaking",name:"峰值对焦"},
                        {key:"level",name:"电子水平仪"},{key:"stabilization",name:"防抖"},{key:"flash",name:"闪光同步"},{key:"battery",name:"电池遥测"}
                    ]
                    Rectangle {
                        Layout.fillWidth: true; implicitHeight: Math.max(64, detail.implicitHeight + 20); color: "#1b1b1b"; radius: 4
                        RowLayout {
                            anchors.fill: parent; anchors.margins: 10; spacing: 16
                            Text { Layout.preferredWidth: Math.min(240, page.width * .38); text: qsTr(modelData.name); color: "#eeeeee"; font.pixelSize: 15; wrapMode: Text.WordWrap }
                            Text {
                                id: detail; Layout.fillWidth: true
                                text: page.performanceTab ? modelData.value : page.capabilities[modelData.key] ? (page.capabilities[modelData.key].available ? (page.capabilities[modelData.key].simulated ? qsTr("已支持 · 合成输入") : qsTr("已支持")) : qsTr("不可用")) + (page.capabilities[modelData.key].reason ? "\n" + qsTr(page.capabilities[modelData.key].reason) : "") : qsTr("尚未确认")
                                color: "#bbbbbb"; font.pixelSize: 14; wrapMode: Text.WordWrap
                            }
                        }
                    }
                }
                MediaButton { Layout.fillWidth: true; text: qsTr("保存当前性能记录"); visible: page.performanceTab; onClicked: if (typeof uiPerformance !== "undefined") uiPerformance.save() }
            }
        }
    }
}
