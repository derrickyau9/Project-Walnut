import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: page
    objectName: "persistentTasksPage"
    property var taskService: null
    color: "#0b0b0b"
    signal closeRequested()
    function closeOverlays() { if (detailPopup.visible) { detailPopup.close(); return true } return false }
    onVisibleChanged: if (!visible) closeOverlays()
    function statusName(value) { var label=({queued:"已排队",running:"运行中",pausing:"正在停止",paused:"已暂停",complete:"已完成",failed:"失败",cancelled:"已取消",unknown:"待核实原请求",awaiting_commit:"待接收端校验"})[value];return label ? qsTr(label) : value }
    function kindName(value) { var label=({workflow:"拍摄流程",transfer:"USB / 网络传输",export_original:"原件导出",export_dng:"DNG 导出",export_jpeg:"JPEG 显影",export_tiff:"16 位 TIFF",metadata:"批量选片",import:"复制导入",purge:"永久清空",develop_jpeg:"JPEG 显影",develop_tiff16:"16 位 TIFF",analyze:"图像分析",hdr:"HDR 合成",exposure_fusion:"曝光融合",focus_stack:"焦点堆栈",denoise_stack:"多帧降噪",panorama:"全景拼接",astro_stack:"星空堆栈",darkflat:"暗场/平场",negative:"负片反转"})[value];return label ? qsTr(label) : value }
    function stageText(value) { return typeof i18n !== "undefined" ? i18n.t(value || "",i18n.revision) : (value || "") }
    ColumnLayout {
        anchors.fill: parent; anchors.margins: 8; spacing: 8
        RowLayout {
            Layout.fillWidth: true
            MediaButton { text: qsTr("返回"); onClicked: page.closeRequested() }
            Text { Layout.fillWidth: true; text: qsTr("后台任务"); color: "#ffffff"; font.pixelSize: 24 }
            Text { text: page.taskService ? page.taskService.pendingCount + qsTr(" 项未完成") : qsTr("不可用"); color: "#bbbbbb"; font.pixelSize: 14 }
            MediaButton { text: qsTr("刷新"); onClicked: if (page.taskService) page.taskService.refresh() }
        }
        Text { Layout.fillWidth: true; text: qsTr("任务和检查点保存在资料分区。重启后由你继续；暂停和取消不会撤销已经完成的项目。"); color: "#aaaaaa"; font.pixelSize: 13; wrapMode: Text.WordWrap }
        ListView {
            id: tasksList; objectName: "persistentTaskList"
            Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 8
            model: page.taskService ? page.taskService.jobs : []
            delegate: Rectangle {
                id: card
                width: tasksList.width; height: 128; color: "#191919"; radius: 6; border.color: "#363636"
                ColumnLayout {
                    anchors.fill: parent; anchors.margins: 10; spacing: 4
                    RowLayout {
                        Layout.fillWidth: true
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 3
                            Text { text: page.kindName(modelData.kind) + " · " + page.statusName(modelData.status); color: "#eeeeee"; font.pixelSize: 17 }
                            Text { Layout.fillWidth: true; text: modelData.error || page.stageText(modelData.stage) || (modelData.done + " / " + modelData.total); color: "#bcbcbc"; font.pixelSize: 13; elide: Text.ElideRight }
                        }
                        MediaButton { implicitWidth: 70; text: qsTr("详情"); onClicked: { page.taskService.inspectJob(modelData.job_id); detailPopup.open() } }
                        MediaButton { implicitWidth: 70; text: modelData.status === "running" || modelData.status === "queued" ? qsTr("暂停") : qsTr("继续"); enabled: ["running","queued","paused","unknown","failed","cancelled"].indexOf(modelData.status) >= 0; onClicked: { if (modelData.status === "running" || modelData.status === "queued") page.taskService.pause(modelData.job_id); else page.taskService.resume(modelData.job_id) } }
                        MediaButton { implicitWidth: 70; text: qsTr("取消"); enabled: ["running","queued","pausing","paused","unknown","awaiting_commit"].indexOf(modelData.status) >= 0; onClicked: page.taskService.cancel(modelData.job_id) }
                    }
                    ProgressBar { Layout.fillWidth: true; Layout.preferredHeight: 6; from: 0; to: 1; value: modelData.progress; background: Rectangle { color: "#333333"; radius: 3 } contentItem: Item { Rectangle { width: parent.width * modelData.progress; height: parent.height; color: "#cccccc"; radius: 3 } } }
                    Text { text: modelData.done + " / " + modelData.total + qsTr(" 项 · ") + Math.round(modelData.progress * 100) + "%"; color: "#999999"; font.pixelSize: 12 }
                }
            }
            Text { anchors.centerIn: parent; visible: tasksList.count === 0; text: qsTr("暂无后台任务"); color: "#aaaaaa"; font.pixelSize: 20 }
        }
        Text { Layout.fillWidth: true; text: page.taskService ? qsTr(page.taskService.message) : ""; visible: text.length > 0; color: "#bcbcbc"; font.pixelSize: 13; wrapMode: Text.WordWrap }
    }
    Popup {
        id: detailPopup
        parent: page; x: 8; y: 8; width: page.width - 16; height: page.height - 16
        modal: true; focus: true; padding: 12
        background: Rectangle { color: "#141414"; border.color: "#666666"; radius: 6 }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; Text { text: qsTr("任务档案 · 前 100 项"); color: "white"; font.pixelSize: 20; Layout.fillWidth: true } MediaButton { text: qsTr("关闭"); onClicked: detailPopup.close() } }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; TextArea { text: page.taskService ? page.taskService.detailText : ""; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; textFormat: TextEdit.PlainText; color: "#cccccc"; font.pixelSize: 13; background: null } }
        }
    }
}
