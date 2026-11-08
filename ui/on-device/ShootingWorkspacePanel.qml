import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

// Shared by the shooting Q sheet and the MENU customization pages.
Item {
    id: panel
    property var workspace
    property var theme
    property string page: "quick"
    property bool editing: false
    property int editingSlot: -1
    property bool showModeToolbar:true
    Typography { id:typography }
    readonly property var colors: theme && theme.background ? theme : ({background:"#111111",surface:"#111111",raised:"#222222",text:"#ffffff",muted:"#cccccc",line:"#555555",accent:"#666666"})
    signal closeRequested()

    function actionIndex(actionId) {
        var items = workspace ? workspace.inputActions : []
        for (var i = 0; i < items.length; i++) if (items[i].id === actionId) return i
        return 0
    }
    function pickSlot(index) { editingSlot=index; assignment.open() }
    function valueText(action,confirmed) {
        var view=workspace ? workspace.parameterView : {}, values=confirmed ? view.confirmed : view.desired
        if(!values) return "—"
        if(action === "gain") return values.requested_analogue_gain === undefined ? "—" : Number(values.requested_analogue_gain).toFixed(2)+"×"
        if(action === "exposure") {
            var us=values.requested_exposure_us
            if(!(us>0)) return "—"
            return us>=1000000 ? (us/1000000).toFixed(us%1000000 ? 1 : 0)+" s" : "1/"+Math.round(1000000/us)
        }
        return ""
    }
    function parameterPending(action) {return valueText(action,false) !== valueText(action,true)}
    function closeOverlays() {
        var wasOpen=assignment.visible || importDialog.visible
        assignment.exit.enabled=false
        assignment.close()
        assignment.exit.enabled=true
        importDialog.close()
        return wasOpen
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 8
        RowLayout {
            visible:panel.page !== "quick" || panel.showModeToolbar
            Layout.fillWidth: true
            spacing: 6
            Button {
                id:photoChoice
                text:qsTr("照片 Q"); implicitHeight:64; implicitWidth:100
                readonly property bool selected:panel.workspace && panel.workspace.mode === "photo"
                onClicked:panel.workspace.setMode("photo")
                background:Rectangle {color:photoChoice.selected ? panel.colors.text : panel.colors.raised; border.color:panel.colors.line}
                contentItem:Text {text:photoChoice.text; color:photoChoice.selected ? panel.colors.background : panel.colors.text; font.family:typography.uiFamily; font.pixelSize:typography.bodySize; font.weight:Font.Normal; font.letterSpacing:typography.bodyTracking; horizontalAlignment:Text.AlignHCenter; verticalAlignment:Text.AlignVCenter}
            }
            Button {
                id:videoChoice
                text:qsTr("视频 Q"); implicitHeight:64; implicitWidth:100
                readonly property bool selected:panel.workspace && panel.workspace.mode === "video"
                onClicked:panel.workspace.setMode("video")
                background:Rectangle {color:videoChoice.selected ? panel.colors.text : panel.colors.raised; border.color:panel.colors.line}
                contentItem:Text {text:videoChoice.text; color:videoChoice.selected ? panel.colors.background : panel.colors.text; font.family:typography.uiFamily; font.pixelSize:typography.bodySize; font.weight:Font.Normal; font.letterSpacing:typography.bodyTracking; horizontalAlignment:Text.AlignHCenter; verticalAlignment:Text.AlignVCenter}
            }
            Item { Layout.fillWidth: true }
            Button { visible: panel.page === "quick"; text: panel.editing ? qsTr("结束编辑") : qsTr("编辑 12 格"); implicitHeight: 64; onClicked: panel.editing=!panel.editing }
        }
        Label {
            Layout.fillWidth: true
            visible:panel.page !== "quick"
            text: panel.page === "customize" ? qsTr("C1–C3 记忆已确认的真实参数和界面；导入配置不会自动应用曝光。") : qsTr("个人收藏 · 使用下方编辑按钮增减工具。")
            color: panel.colors.muted
            font.family:typography.uiFamily
            font.pixelSize: 15
            font.weight:Font.Normal
            wrapMode: Text.WordWrap
        }

        GridLayout {
            visible: panel.page === "quick"
            Layout.fillWidth: true
            Layout.fillHeight: true
            columns: 4
            columnSpacing: 8
            rowSpacing: 8
            Repeater {
                model: panel.workspace ? panel.workspace.quickSlots : []
                Button {
                    id: tile
                    objectName: "q-slot-" + index
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.preferredWidth: 160
                    Layout.minimumWidth: 0
                    Layout.preferredHeight: 76
                    Layout.minimumHeight: 64
                    property bool usable: modelData.available
                    property bool parameter:modelData.id === "exposure" || modelData.id === "gain"
                    property bool selected:parameter && panel.workspace && panel.workspace.selectedParameter === modelData.id
                    padding:4
                    onClicked: panel.editing ? panel.pickSlot(index) : parameter ? panel.workspace.selectQuickParameter(modelData.id) : panel.workspace.triggerAction(modelData.id)
                    onPressAndHold: panel.pickSlot(index)
                    Accessible.name: qsTr(modelData.title) + (usable ? "" : qsTr(", unavailable: ") + qsTr(modelData.reason))
                    background: Rectangle {
                        radius: 3
                        color: tile.down ? "#444444" : panel.colors.raised
                        border.color: tile.activeFocus || panel.editing || tile.selected ? panel.colors.text : panel.colors.line
                        border.width: tile.activeFocus || tile.selected ? 2 : 1
                    }
                    contentItem: Column {
                        spacing: 4
                        scale:tile.down ? .97 : 1
                        Behavior on scale { NumberAnimation { duration:panel.workspace && panel.workspace.reducedMotion ? 0 : 220; easing.type:Easing.OutCubic } }
                        Text { width: parent.width; text: qsTr(modelData.title); horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; color: tile.down ? "#ffffff" : tile.usable ? panel.colors.text : panel.colors.muted; font.family:typography.uiFamily; font.pixelSize:typography.bodySize; font.weight:Font.Normal; font.letterSpacing:typography.bodyTracking }
                        NumericReadout { visible:tile.parameter && !panel.editing; width:parent.width;height:28;value:panel.valueText(modelData.id,false)+(panel.parameterPending(modelData.id) ? " ·" : "");pixelSize:22;ink:panel.colors.text;reduceMotion:panel.workspace && panel.workspace.reducedMotion }
                        Text { visible:!tile.parameter || panel.editing; width: parent.width; text: panel.editing ? qsTr("更换功能") : !tile.usable ? qsTr("暂不可用 · 查看原因") : (index+1 < 10 ? "0" : "") + (index+1); horizontalAlignment: Text.AlignHCenter; color: tile.down ? "#eeeeee" : panel.colors.muted; font.family:typography.numericFamily; font.pixelSize:typography.captionSize; font.weight:Font.Normal; font.letterSpacing:.4 }
                    }
                    DragHandler {
                        target:null
                        enabled:tile.parameter && !panel.editing
                        xAxis.enabled:true;yAxis.enabled:false
                        property int applied:0
                        onActiveChanged: {applied=0;if(active)panel.workspace.selectQuickParameter(modelData.id)}
                        onTranslationChanged:if(active) {
                            var detents=Math.trunc(translation.x/14),delta=detents-applied
                            if(delta) {panel.workspace.nudgeParameter(modelData.id,delta);applied=detents}
                        }
                    }
                }
            }
        }

        RowLayout {
            visible:panel.page === "quick"
            Layout.fillWidth:true
            Layout.preferredHeight:64
            Button { text:"−";implicitWidth:64;implicitHeight:64;font.pixelSize:28;autoRepeat:true;autoRepeatDelay:400;autoRepeatInterval:80;enabled:panel.workspace && panel.workspace.parameterView.canEdit;onClicked:panel.workspace.nudgeParameter(panel.workspace.selectedParameter,-1) }
            ColumnLayout {
                Layout.fillWidth:true;spacing:2
                Text { Layout.fillWidth:true;text:panel.workspace && panel.workspace.selectedParameter === "gain" ? qsTr("模拟增益 · ")+panel.valueText("gain",false) : qsTr("快门 · ")+panel.valueText("exposure",false);horizontalAlignment:Text.AlignHCenter;color:panel.colors.text;font.family:typography.numericFamily;font.pixelSize:20 }
                Text { Layout.fillWidth:true;text:panel.workspace && panel.workspace.message.length ? (typeof i18n !== "undefined" ? i18n.t(panel.workspace.message, i18n.revision) : panel.workspace.message) : panel.workspace && panel.workspace.parameterView.error ? panel.workspace.parameterView.error : panel.workspace && panel.workspace.parameterView.pending ? qsTr("待确认 · 当前 ")+panel.valueText(panel.workspace.selectedParameter,true) : panel.workspace && panel.workspace.mode === "video" ? qsTr("视频 Q 仅为布局；当前后端不支持录制") : qsTr("参数已确认 · 拨轮或左右滑动方格直接调节");color:panel.colors.muted;font.family:typography.uiFamily;font.pixelSize:14;horizontalAlignment:Text.AlignHCenter;elide:Text.ElideRight;maximumLineCount:1 }
            }
            Button { text:"+";implicitWidth:64;implicitHeight:64;font.pixelSize:28;autoRepeat:true;autoRepeatDelay:400;autoRepeatInterval:80;enabled:panel.workspace && panel.workspace.parameterView.canEdit;onClicked:panel.workspace.nudgeParameter(panel.workspace.selectedParameter,1) }
        }

        ScrollView {
            id:pinsScroll
            visible: panel.page === "my_menu"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth:availableWidth
            ScrollBar.horizontal.policy:ScrollBar.AlwaysOff
            ColumnLayout {
                width: pinsScroll.availableWidth
                spacing: 6
                Repeater {
                    model: panel.workspace ? panel.workspace.myMenu : []
                    RowLayout {
                        Layout.fillWidth: true
                        Button { Layout.fillWidth:true; implicitHeight:64; text:qsTr(modelData.title) + (modelData.available ? "" : qsTr(" · 暂不可用")); onClicked:panel.workspace.triggerAction(modelData.id) }
                        Button { text:qsTr("移除"); implicitHeight:64; onClicked:panel.workspace.toggleMyMenu(modelData.id) }
                    }
                }
                Label { visible: panel.workspace && panel.workspace.myMenu.length === 0; text:qsTr("尚无收藏，请添加常用工具。"); color:panel.colors.muted; wrapMode:Text.WordWrap; Layout.fillWidth:true }
                Button { text:qsTr("编辑收藏"); implicitHeight:64; onClicked:{panel.editingSlot=-1;assignment.open()} }
            }
        }

        ScrollView {
            id:controlsScroll
            visible: panel.page === "customize"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth:availableWidth
            ScrollBar.horizontal.policy:ScrollBar.AlwaysOff
            ColumnLayout {
                width: controlsScroll.availableWidth
                spacing: 10
                RowLayout {
                    Layout.fillWidth:true
                    LocalizedComboBox { id:presetChoice; Layout.fillWidth:true; implicitHeight:64; model:panel.workspace ? panel.workspace.presets : []; textRole:"title" }
                    Button { text:qsTr("应用界面模板"); implicitHeight:64; onClicked:panel.workspace.applyPreset(presetChoice.model[presetChoice.currentIndex].id) }
                }
                RowLayout {
                    Layout.fillWidth:true
                    Button { text:qsTr("导出分享配置"); Layout.fillWidth:true; implicitHeight:64; onClicked:panel.workspace.exportDefault() }
                    Button { text:qsTr("从文件导入…"); Layout.fillWidth:true; implicitHeight:64; onClicked:importDialog.open() }
                }
                Repeater {
                    model: panel.workspace ? panel.workspace.profiles : []
                    RowLayout {
                        Layout.fillWidth: true
                        Label { text:modelData.id + (!modelData.parametersSaved && modelData.saved ? qsTr(" · 仅界面") : modelData.active ? (panel.workspace.profileDirty ? qsTr(" · 已修改") : qsTr(" · 当前")) : ""); color:panel.colors.text; font.family:typography.numericFamily; font.pixelSize:20; font.weight:Font.Normal; font.letterSpacing:.4; Layout.fillWidth:true }
                        Button { text:qsTr("保存 ") + modelData.id; implicitHeight:64; onClicked:panel.workspace.saveProfile(modelData.id) }
                        Button { text:qsTr("载入"); implicitHeight:64; enabled:modelData.saved; onClicked:panel.workspace.loadProfile(modelData.id) }
                    }
                }
                CheckBox { text:qsTr("拍摄时自动收起任务栏"); checked:panel.workspace ? panel.workspace.taskbarAutoHide : true; onToggled:if(panel.workspace) panel.workspace.setTaskbarAutoHide(checked) }
                CheckBox { text:qsTr("减少动态效果"); checked:panel.workspace ? panel.workspace.reducedMotion : false; onToggled:if(panel.workspace) panel.workspace.setReducedMotion(checked) }
                Repeater {
                    model: ["fn1", "fn2"]
                    RowLayout {
                        Layout.fillWidth: true
                        property string inputKey: modelData
                        Label { text:parent.inputKey === "fn1" ? qsTr("Fn1 / F1") : qsTr("Fn2 / F2"); color:panel.colors.text; font.family:typography.numericFamily; font.pixelSize:typography.bodySize; Layout.preferredWidth:100 }
                        LocalizedComboBox { Layout.fillWidth:true; implicitHeight:64; model:panel.workspace ? panel.workspace.inputActions : []; textRole:"title"; currentIndex:panel.workspace ? panel.actionIndex(panel.workspace.inputBindings[parent.inputKey]) : 0; onActivated:panel.workspace.setInput(parent.inputKey, model[index].id) }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text:qsTr("拨轮 / ↑ ↓"); color:panel.colors.text; font.family:typography.uiFamily; font.pixelSize:typography.bodySize; Layout.preferredWidth:100 }
                    LocalizedComboBox { Layout.fillWidth:true; implicitHeight:64; model:[{id:"exposure",title:qsTr("快门速度")},{id:"gain",title:qsTr("模拟增益")}]; textRole:"title"; currentIndex:panel.workspace && panel.workspace.inputBindings.dial === "gain" ? 1 : 0; onActivated:panel.workspace.setInput("dial",model[index].id) }
                }
                Label { Layout.fillWidth:true; text:qsTr("按住 F1/F2 临时显示辅助线或面板，松开恢复。曝光修改仍由相机确认；松开不会逆改曝光。↑/↓ 调节拨轮参数。实体 GPIO 需接入适配器；输入文字时快捷键暂停。"); color:panel.colors.muted; font.pixelSize:14; wrapMode:Text.WordWrap }
                Button { text:qsTr("编辑我的菜单收藏"); implicitHeight:64; onClicked:{panel.editingSlot=-1;assignment.open()} }
            }
        }
        Label {
            Layout.fillWidth: true
            visible: panel.page !== "quick" && panel.workspace && panel.workspace.message.length > 0
            text: panel.workspace ? (typeof i18n !== "undefined" ? i18n.t(panel.workspace.message, i18n.revision) : qsTr(panel.workspace.message)) : ""
            font.family:typography.uiFamily
            font.pixelSize:typography.captionSize
            font.weight:Font.Normal
            color: panel.colors.text
            wrapMode: Text.WordWrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
    }

    Popup {
        id:importDialog
        objectName:"workspaceImportDialog"
        parent:Overlay.overlay
        modal:true
        focus:true
        width:Math.min(720,(parent ? parent.width : panel.width)-24)
        height:216
        x:parent ? (parent.width-width)/2 : 0
        y:8
        padding:10
        closePolicy:Popup.CloseOnEscape
        background:Rectangle { color:panel.colors.surface; border.color:panel.colors.line; radius:8 }
        ColumnLayout {
            anchors.fill:parent
            spacing:4
            Label { text:qsTr("导入 DaOS 工作台"); color:panel.colors.text; font.family:typography.titleFamily; font.pixelSize:typography.sectionSize; font.weight:Font.Medium; font.letterSpacing:typography.titleTracking }
            TextField { id:importPath; objectName:"workspaceImportPath"; Layout.fillWidth:true; Layout.preferredHeight:64; placeholderText:qsTr("输入 .json 文件的完整路径"); inputMethodHints:Qt.ImhNoAutoUppercase|Qt.ImhNoPredictiveText; selectByMouse:true; onAccepted:if(panel.workspace.importWorkspace(text)) {softKeyboard.done();importDialog.close()} }
            RowLayout {
                Layout.fillWidth:true
                Button { text:qsTr("取消"); implicitHeight:64; onClicked:{softKeyboard.done();importDialog.close()} }
                Button { text:qsTr("载入配置"); Layout.fillWidth:true; implicitHeight:64; enabled:importPath.text.length>0; onClicked:if(panel.workspace.importWorkspace(importPath.text)) {softKeyboard.done();importDialog.close()} }
            }
            Label { Layout.fillWidth:true; text:panel.workspace && panel.workspace.message.length ? (typeof i18n !== "undefined" ? i18n.t(panel.workspace.message, i18n.revision) : panel.workspace.message) : qsTr("配置存入工作台，不会自动应用拍摄参数。"); font.family:typography.uiFamily; font.pixelSize:typography.captionSize; color:panel.colors.muted; wrapMode:Text.WrapAnywhere; maximumLineCount:1; elide:Text.ElideRight }
        }
    }

    Popup {
        id: assignment
        objectName: "qAssignmentPopup"
        parent: Overlay.overlay
        modal: true
        focus: true
        width: Math.min(680, (parent ? parent.width : panel.width)-32)
        height: Math.min(440, (parent ? parent.height : panel.height)-24)
        x: parent ? (parent.width-width)/2 : 0
        y: parent ? (parent.height-height)/2 : 0
        padding: 14
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        background: Rectangle { color:panel.colors.surface; radius:8; border.color:panel.colors.line }
        enter: Transition { ParallelAnimation { NumberAnimation { property:"opacity"; from:0; to:1; duration:panel.workspace && panel.workspace.reducedMotion ? 0 : 220; easing.type:Easing.OutCubic } NumberAnimation { property:"scale"; from:panel.workspace && panel.workspace.reducedMotion ? 1 : .97; to:1; duration:panel.workspace && panel.workspace.reducedMotion ? 0 : 280; easing.type:Easing.OutCubic } } }
        exit: Transition { NumberAnimation { property:"opacity"; to:0; duration:panel.workspace && panel.workspace.reducedMotion ? 0 : 220; easing.type:Easing.OutCubic } }
        ColumnLayout {
            anchors.fill:parent
            RowLayout {
                Layout.fillWidth:true
                Label { text:panel.editingSlot < 0 ? qsTr("收藏工具") : qsTr("配置第 ") + (panel.editingSlot+1) + qsTr(" 格"); font.family:typography.titleFamily; font.pixelSize:typography.titleSize; font.weight:Font.Medium; font.letterSpacing:typography.titleTracking; color:panel.colors.text; Layout.fillWidth:true }
                Button { text:qsTr("完成"); implicitHeight:64; onClicked:assignment.close() }
            }
            ListView {
                Layout.fillWidth:true
                Layout.fillHeight:true
                clip:true
                spacing:6
                model:panel.workspace ? panel.workspace.catalog : []
                delegate: RowLayout {
                    width:ListView.view.width
                    height:72
                    Button {
                        Layout.fillWidth:true
                        Layout.fillHeight:true
                        contentItem:Column {
                            spacing:3
                            Text { width:parent.width; text:qsTr(modelData.title); color:panel.colors.text; font.family:typography.uiFamily; font.pixelSize:typography.bodySize; font.weight:Font.Normal }
                            Text { width:parent.width; text:qsTr(modelData.reason); color:panel.colors.muted; font.family:typography.uiFamily; font.pixelSize:typography.captionSize; elide:Text.ElideRight }
                        }
                        onClicked: {
                            if(panel.editingSlot >= 0) { panel.workspace.setSlot(panel.editingSlot,modelData.id); assignment.close() }
                            else panel.workspace.toggleMyMenu(modelData.id)
                        }
                    }
                    Button { text:modelData.pinned ? qsTr("已收藏 ✓") : qsTr("+ 收藏"); implicitWidth:102; Layout.fillHeight:true; onClicked:panel.workspace.toggleMyMenu(modelData.id) }
                }
                ScrollBar.vertical: ScrollBar { }
            }
        }
    }
}
