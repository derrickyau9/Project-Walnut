import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: recipes
    property var apps
    property var cameraController
    property bool looksTab: false
    signal closeRequested()
    function closeOverlays() {
        var popups = [planEditor, historyPopup, evidencePopup]
        var closed = false
        for (var i = 0; i < popups.length; ++i) if (popups[i].visible) { popups[i].close(); closed = true }
        return closed
    }
    onVisibleChanged: if (!visible) closeOverlays()
    readonly property var workflow: apps ? apps.workflow : null
    readonly property var media: apps ? apps.media : null
    readonly property bool running: workflow && workflow.active
    function stateName(value) {
        var names = {ready:"就绪",pending:"等待执行",running:"执行中",paused:"已暂停",completed:"已完成",cancelled:"已取消",failed:"失败",unknown:"结果待核实",unavailable:"不可用",not_started:"未启动"}
        return names[value] ? qsTr(names[value]) : value
    }
    function kindName(value) { var label=({bracket:"曝光包围",interval:"间隔拍摄",dark:"合成暗场",flat:"合成平场"})[value];return label ? qsTr(label) : value }

    Rectangle { anchors.fill: parent; color: "#080808" }
    ColumnLayout {
        anchors.fill: parent; spacing: 8
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            MediaButton { implicitWidth: 64; text: qsTr("返回"); onClicked: recipes.closeRequested() }
            MediaButton { Layout.fillWidth: true; text: qsTr("拍摄计划"); emphasized: !recipes.looksTab; onClicked: recipes.looksTab = false }
            MediaButton { Layout.fillWidth: true; text: qsTr("照片风格"); emphasized: recipes.looksTab; onClicked: recipes.looksTab = true }
            MediaButton { objectName: "workflowNewPlanButton"; text: qsTr("新建"); visible: !recipes.looksTab; enabled: !recipes.running; onClicked: planEditor.open() }
            MediaButton { objectName: "workflowHistoryButton"; text: qsTr("历史"); visible: !recipes.looksTab; onClicked: historyPopup.open() }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0; visible: !recipes.looksTab; spacing: 12
            ColumnLayout {
                Layout.preferredWidth: Math.max(200, Math.min(280, recipes.width * .36)); Layout.fillHeight: true
                Text { text: qsTr("已保存计划"); color: "#cccccc"; font.pixelSize: 15 }
                ListView {
                    id: recipeList; objectName: "appRecipeList"
                    Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 6
                    model: recipes.apps ? recipes.apps.recipes : []
                    delegate: MediaButton {
                        width: recipeList.width; height: 76
                        objectName: "appRecipeItem-" + modelData.id
                        emphasized: recipes.apps.selectedRecipeId === modelData.id
                        text: modelData.name + "\n" + modelData.exposureCount + qsTr(" 张 · ") + recipes.kindName(modelData.kind)
                        enabled: !recipes.running
                        onClicked: recipes.apps.selectRecipe(modelData.id)
                    }
                    Text { anchors.centerIn: parent; width: parent.width - 16; visible: recipeList.count === 0; text: qsTr("新建包围、间隔或合成校准计划。"); color: "#aaaaaa"; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter; font.pixelSize: 14 }
                }
                MediaButton { Layout.fillWidth: true; text: qsTr("导出选中计划"); enabled: recipes.apps && recipes.apps.selectedRecipeId.length > 0; onClicked: recipes.apps.exportRecipe(recipes.apps.exportDirectory + "/recipe-" + Date.now() + ".json") }
            }
            Rectangle { Layout.fillHeight: true; width: 1; color: "#333333" }
            ColumnLayout {
                Layout.fillWidth: true; Layout.fillHeight: true; spacing: 8
                RowLayout {
                    Layout.fillWidth: true
                    Text { text: recipes.workflow ? recipes.stateName(recipes.workflow.status) : qsTr("不可用"); color: "#ffffff"; font.pixelSize: 22; font.weight: Font.DemiBold; Layout.fillWidth: true }
                    Text { text: recipes.workflow ? recipes.workflow.completed + " / " + recipes.workflow.total : ""; color: "#dddddd"; font.pixelSize: 18 }
                    MediaButton { implicitWidth: 76; text: qsTr("档案"); enabled: recipes.workflow && recipes.workflow.jobId.length > 0; onClicked: evidencePopup.open() }
                }
                ProgressBar { objectName: "workflowProgress"; Layout.fillWidth: true; from: 0; to: 1; value: recipes.workflow ? recipes.workflow.progress : 0; implicitHeight: 8; background: Rectangle { color: "#333333"; radius: 3 } contentItem: Item { Rectangle { width: parent.width * (recipes.workflow ? recipes.workflow.progress : 0); height: parent.height; color: "#dddddd"; radius: 3 } } }
                Text { text: recipes.workflow && recipes.workflow.active ? (recipes.workflow.desired === "pause" ? qsTr("已请求暂停 · 等待当前拍摄完成") : recipes.workflow.desired === "cancel" ? qsTr("已请求取消 · 正在核实原始请求") : qsTr("相机已由此计划独占")) : qsTr("HF-Sim · 合成拍摄源"); color: "#aaaaaa"; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                RowLayout {
                    Layout.fillWidth: true; spacing: 4
                    MediaButton { objectName: "workflowRunButton"; Layout.fillWidth: true; implicitWidth: 64; text: qsTr("执行"); emphasized: true; enabled: recipes.workflow && recipes.workflow.available && !recipes.running && recipes.apps.selectedRecipeId.length > 0; onClicked: recipes.apps.runSelectedRecipe() }
                    MediaButton { objectName: "workflowPauseButton"; Layout.fillWidth: true; implicitWidth: 64; text: qsTr("暂停"); enabled: recipes.running; onClicked: recipes.workflow.pause() }
                    MediaButton { objectName: "workflowResumeButton"; Layout.fillWidth: true; implicitWidth: 70; text: qsTr("继续"); enabled: recipes.workflow && !recipes.running && (recipes.workflow.status === "paused" || recipes.workflow.status === "unknown"); onClicked: recipes.workflow.resume() }
                    MediaButton { objectName: "workflowCancelButton"; Layout.fillWidth: true; implicitWidth: 68; text: qsTr("取消"); enabled: recipes.workflow && (recipes.running || recipes.workflow.status === "paused" || recipes.workflow.status === "unknown"); onClicked: recipes.workflow.cancel() }
                }
                ScrollView {
                    Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0; Layout.preferredHeight: 100; clip: true
                    TextArea { objectName: "workflowResults"; text: recipes.workflow ? qsTr(recipes.workflow.summaryText) : qsTr("工作流控制暂不可用"); readOnly: true; selectByMouse: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.WrapAnywhere; color: "#cccccc"; font.pixelSize: 15; background: null }
                }
                Text { text: recipes.workflow ? qsTr(recipes.workflow.errorText) : ""; visible: text.length > 0; color: "#ffffff"; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13 }
                Text { text: qsTr("暂停在两次拍摄之间生效。继续会核实原始 UUID，不会自动重复不明结果的曝光。"); color: "#999999"; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12 }
            }
        }
        ColumnLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; spacing: 12; visible: recipes.looksTab
            Text { text: qsTr("非破坏照片风格"); color: "#ffffff"; font.pixelSize: 23 }
            Text { Layout.fillWidth: true; text: qsTr("对选中的已保存照片应用显示风格，影响回放和导出的 SDR 样张。原始 Bayer DNG 与传感器校准保持独立。"); color: "#bbbbbb"; wrapMode: Text.WordWrap; font.pixelSize: 15 }
            RowLayout {
                Layout.fillWidth: true
                Repeater { model: ["Natural", "Mono", "Punchy", "Soft"]; MediaButton { Layout.fillWidth: true; text: ({Natural:qsTr("自然"),Mono:qsTr("黑白"),Punchy:qsTr("鲜明"),Soft:qsTr("柔和")})[modelData]; enabled: recipes.media && recipes.media.ready && !recipes.media.busy; onClicked: recipes.media.applyLook(modelData) } }
            }
            Image { Layout.fillWidth: true; Layout.fillHeight: true; source: recipes.media && recipes.media.previewUrl ? recipes.media.previewUrl : recipes.cameraController ? recipes.cameraController.reviewUrl : ""; fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false }
            Text { Layout.fillWidth: true; text: recipes.media && recipes.media.selectedId ? (typeof i18n !== "undefined" ? i18n.t(recipes.media.message, i18n.revision) : recipes.media.message) : qsTr("请先在照片中选择一张已保存图像。"); color: "#aaaaaa"; wrapMode: Text.WordWrap; font.pixelSize: 13 }
        }
    }

    Popup {
        id: planEditor
        objectName: "workflowPlanEditor"
        parent: recipes; x: 8; y: 8; width: recipes.width - 16; height: recipes.height - 16
        property string kind: "bracket"
        modal: true; focus: true; padding: 12
        onOpened: { planName.text = ""; kind = "bracket" }
        background: Rectangle { color: "#111111"; border.color: "#777777"; radius: 8 }
        contentItem: ColumnLayout {
            spacing: 8
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: qsTr("新建拍摄计划"); color: "#ffffff"; font.family: typeof cameraTitleFont !== "undefined" ? cameraTitleFont : "Noto Sans CJK SC"; font.pixelSize: 24; font.weight: Font.Medium; font.letterSpacing: 1.2 } MediaButton { text: qsTr("关闭"); onClicked: planEditor.close() } }
            TextField { id: planName; objectName: "appRecipeNameInput"; Layout.fillWidth: true; Layout.preferredHeight: 64; placeholderText: qsTr("计划名称"); color: "#eeeeee"; font.pixelSize: 16; maximumLength: 80; selectByMouse: true; background: Rectangle { color: "#222222"; border.color: "#666666"; radius: 4 } }
            RowLayout { Layout.fillWidth: true; spacing: 4; Repeater { model: ["bracket","interval","dark","flat"]; MediaButton { Layout.fillWidth: true; text: recipes.kindName(modelData); emphasized: planEditor.kind === modelData; onClicked: planEditor.kind = modelData } } }
            RowLayout {
                Layout.fillWidth: true; visible: planEditor.kind !== "bracket"
                Text { text: qsTr("张数"); color: "#cccccc"; font.pixelSize: 15 }
                SpinBox { id: shotCount; Layout.preferredHeight: 64; Layout.fillWidth: true; from: 1; to: 100; value: 10; editable: false; font.pixelSize: 18 }
                Text { text: qsTr("间隔 / ms"); visible: planEditor.kind === "interval"; color: "#cccccc"; font.pixelSize: 15 }
                SpinBox { id: interval; visible: planEditor.kind === "interval"; Layout.preferredHeight: 64; Layout.fillWidth: true; from: 0; to: 86400000; stepSize: 1000; value: 1000; editable: true; font.pixelSize: 18 }
            }
            Text { Layout.fillWidth: true; Layout.fillHeight: true; color: "#bbbbbb"; wrapMode: Text.WordWrap; font.pixelSize: 14; text: planEditor.kind === "bracket" ? qsTr("三张包围：当前曝光的一半 / 当前 / 两倍（−1、0、+1 EV，受支持范围限制）。同时保存当前增益和模式。") : planEditor.kind === "interval" ? qsTr("重复当前曝光。间隔是上一张保存完成后的最短等待时间，不是硬件精确定时。") : qsTr("合成 ") + planEditor.kind + qsTr(" 序列，不能证明实体传感器已校准或满足光学条件。") }
            MediaButton { objectName: "appRecipeSaveButton"; Layout.fillWidth: true; text: qsTr("保存计划"); emphasized: true; enabled: planName.text.trim().length > 0 && !!recipes.apps; onClicked: { recipes.apps.saveCaptureRecipe(planName.text.trim(), planEditor.kind, shotCount.value, interval.value); if (!recipes.apps.errorText) planEditor.close() } }
        }
    }
    Popup {
        id: evidencePopup
        parent: recipes; x: 8; y: 8; width: recipes.width - 16; height: recipes.height - 16
        modal: true; focus: true; padding: 12
        background: Rectangle { color: "#111111"; border.color: "#777777"; radius: 8 }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: qsTr("工作流拍摄档案"); color: "#ffffff"; font.pixelSize: 20 } MediaButton { text: qsTr("关闭"); onClicked: evidencePopup.close() } }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; TextArea { text: recipes.workflow ? recipes.workflow.resultText : ""; readOnly: true; selectByMouse: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.WrapAnywhere; color: "#cccccc"; font.pixelSize: 13; background: null } }
        }
    }
    Popup {
        id: historyPopup
        parent: recipes; x: 8; y: 8; width: recipes.width - 16; height: recipes.height - 16
        modal: true; focus: true; padding: 12
        background: Rectangle { color: "#111111"; border.color: "#777777"; radius: 8 }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: qsTr("工作流历史"); color: "#ffffff"; font.pixelSize: 20 } MediaButton { text: qsTr("关闭"); onClicked: historyPopup.close() } }
            ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 6; model: recipes.workflow ? recipes.workflow.jobs : []; delegate: MediaButton { width: ListView.view.width; height: 72; enabled: !recipes.running; emphasized: modelData.job_id === recipes.workflow.jobId; text: recipes.stateName(modelData.status) + "\n" + modelData.job_id; onClicked: { recipes.workflow.selectJob(modelData.job_id); historyPopup.close() } } }
        }
    }
}
