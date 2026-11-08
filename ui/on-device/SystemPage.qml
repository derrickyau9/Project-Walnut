import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: page
    objectName: "systemMaintenancePage"
    property var systemController: typeof systemControl !== "undefined" ? systemControl : null
    property var linkController:typeof cameraLink !== "undefined" ? cameraLink : null
    property int tab: 0
    property var activeEditor:null
    readonly property bool keyboardOpen:typeof softKeyboard !== "undefined" && softKeyboard.isOpen
    signal closeRequested()
    color: "#080808"
    Typography { id: typeStyle }
    function pref(key,value) { if(systemController) systemController.setPreference(key,value) }
    function confirm(action,title,argument) { confirmDialog.action=action; confirmDialog.titleText=title;confirmDialog.argument=argument || ""; confirmDialog.open() }
    function closeOverlays() {var opened=confirmDialog.visible;confirmDialog.close();return opened}
    function samePath(a,b) {
        var left=String(a || "").replace(/\\/g,"/").trim(),right=String(b || "").replace(/\\/g,"/").trim()
        return Qt.platform.os === "windows" ? left.toLowerCase() === right.toLowerCase() : left === right
    }
    function reveal(editor) {
        if(!editor || !editor.activeFocus)return
        activeEditor=editor
        Qt.callLater(function(){if(editor.activeFocus)scroll.contentY=Math.max(0,Math.min(editor.mapToItem(content,0,0).y-4,Math.max(0,scroll.contentHeight-scroll.height)))})
    }
    Component.onCompleted: if(systemController) systemController.refresh()
    ColumnLayout {
        anchors.fill: parent; spacing: 8
        RowLayout {
            visible:!page.keyboardOpen
            Layout.fillWidth: true
            MediaButton { text: qsTr("返回"); implicitWidth: 64; onClicked: page.closeRequested() }
            Text { Layout.fillWidth: true; text: qsTr("系统与维护"); color: "white"; font.family: typeStyle.titleFamily; font.pixelSize: 24; font.weight: Font.Medium; font.letterSpacing: 1.2 }
            MediaButton { text: qsTr("刷新"); enabled: page.systemController && !page.systemController.busy; onClicked: page.systemController.refresh() }
        }
        RowLayout {
            visible:!page.keyboardOpen
            Layout.fillWidth: true
            Repeater { model: ["系统", "备份恢复", "应用更新", "开发", "联机"]
                MediaButton { Layout.fillWidth: true; text: qsTr(modelData); emphasized: page.tab === index; onClicked: page.tab=index }
            }
        }
        RowLayout {
            visible:!page.keyboardOpen && page.systemController && page.systemController.restartRequired
            Layout.fillWidth:true
            Text {Layout.fillWidth:true;text:qsTr("恢复后需要重新载入界面；拍摄参数保持当前。");font.pixelSize:14;color:"#dddddd";wrapMode:Text.WordWrap}
            MediaButton {text:qsTr("重新载入");onClicked:page.systemController.reloadConfiguration()}
            MediaButton {text:qsTr("核对恢复");onClicked:page.systemController.recoverPendingConfiguration()}
        }
        Flickable {
            id: scroll
            Layout.fillWidth: true; Layout.fillHeight: true
            Layout.bottomMargin: typeof softKeyboard !== "undefined" && softKeyboard.isOpen ? 252 : 0
            clip: true; contentWidth: width; contentHeight: content.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}
            onHeightChanged:page.reveal(page.activeEditor)
            Column {
                id: content; width: scroll.width; spacing: 10
                ColumnLayout {
                    width:content.width; visible: page.tab === 0
                    Text { Layout.fillWidth: true; text: page.systemController ? page.systemController.clockText : ""; color: "#eeeeee"; font.family: typeStyle.numericFamily; font.pixelSize: 24 }
                    RowLayout {
                        Layout.fillWidth:true
                        Text {text:qsTr("语言");color:"white";font.pixelSize:18}
                        LocalizedComboBox {
                            objectName:"systemLanguageChoice"
                            Layout.fillWidth:true;implicitHeight:64
                            model:[{code:"zh_CN",label:"简体中文"},{code:"en_US",label:"English"}]
                            textRole:"label"
                            currentIndex:page.systemController && page.systemController.preferences.language === "en_US" ? 1 : 0
                            enabled:page.systemController && !page.systemController.busy
                            onActivated:page.pref("language",model[index].code)
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("时区"); color: "white"; font.pixelSize: 18 }
                        LocalizedComboBox {
                            Layout.fillWidth: true; implicitHeight: 64
                            model: page.systemController ? page.systemController.timezones : []
                            currentIndex: page.systemController ? Math.max(0,model.indexOf(page.systemController.preferences.timezone)) : 0
                            onActivated: page.pref("timezone",currentText)
                        }
                        MediaButton { text: qsTr("应用到系统"); implicitWidth:116; enabled: page.systemController && page.systemController.nativeReady && !page.systemController.busy; onClicked: page.systemController.systemAction("set-timezone") }
                    }
                    Text {text:qsTr("手动设置 UTC 时间");color:"#eeeeee";font.pixelSize:18}
                    RowLayout {
                        Layout.fillWidth:true
                        TextField {id:utcInput;objectName:"manualUtcInput";Layout.fillWidth:true;Layout.preferredHeight:64;font.pixelSize:18;placeholderText:"2026-11-06T12:00:00Z";selectByMouse:true;inputMethodHints:Qt.ImhLatinOnly|Qt.ImhNoAutoUppercase|Qt.ImhNoPredictiveText;onActiveFocusChanged:page.reveal(utcInput);onAccepted:{if(typeof softKeyboard !== "undefined")softKeyboard.done();page.confirm("set-time","设置系统 UTC 时间？",text.trim())}}
                        MediaButton {text:qsTr("设置 UTC");implicitWidth:112;enabled:page.systemController && page.systemController.nativeReady && !page.systemController.busy && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/.test(utcInput.text);onClicked:page.confirm("set-time","设置系统 UTC 时间？",utcInput.text.trim())}
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("屏幕休眠"); color: "white"; font.pixelSize: 18 }
                        LocalizedComboBox {
                            Layout.fillWidth: true; implicitHeight: 64
                            property var values: [0,30,60,120,300,600,1800]
                            model: [qsTr("永不"), qsTr("30 秒"), qsTr("1 分钟"), qsTr("2 分钟"), qsTr("5 分钟"), qsTr("10 分钟"), qsTr("30 分钟")]
                            currentIndex: page.systemController ? Math.max(0,values.indexOf(page.systemController.preferences.idle_seconds)) : 4
                            onActivated: page.pref("idle_seconds",values[index])
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("硬件背光"); color: "white"; font.pixelSize: 18 }
                        Slider { Layout.fillWidth: true; implicitHeight: 64; from: 1; to: 100; stepSize: 1; value: page.systemController && page.systemController.state.backlight_percent !== undefined ? page.systemController.state.backlight_percent : 100; enabled: page.systemController && page.systemController.state.backlight_supported && !page.systemController.busy; onMoved: page.systemController.setBacklight(Math.round(value)) }
                        Text { text: page.systemController && page.systemController.state.backlight_supported ? page.systemController.state.backlight_percent + "%" : qsTr("设备未提供"); color: "#aaaaaa"; font.pixelSize: 16 }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        MediaButton { Layout.fillWidth: true; text: qsTr("同步网络时间"); enabled: page.systemController && page.systemController.nativeReady && !page.systemController.busy; onClicked: page.confirm("sync-time","同步设备时间？") }
                        MediaButton { Layout.fillWidth: true; text: qsTr("重启"); enabled: page.systemController && page.systemController.state.power_supported; onClicked: page.confirm("reboot","安全重启设备？") }
                        MediaButton { Layout.fillWidth: true; text: qsTr("关机"); enabled: page.systemController && page.systemController.state.power_supported; onClicked: page.confirm("shutdown","安全关闭设备？") }
                    }
                    Text { Layout.fillWidth: true; text: page.systemController && page.systemController.nativeReady ? qsTr("内核：") + (page.systemController.state.kernel || "") + qsTr("\n可用空间：") + Math.round((page.systemController.state.data_free_bytes || 0)/1048576) + qsTr(" MiB") : page.systemController && page.systemController.state.native ? qsTr("原生系统服务尚未就绪；未确认的系统操作不会报成功。") : qsTr("桌面预览：系统时间和硬件操作在 DaOS BSD 设备上启用。"); color: "#aaaaaa"; font.pixelSize: 14; wrapMode: Text.WrapAnywhere }
                }
                ColumnLayout {
                    width:content.width; visible: page.tab === 1
                    Text { Layout.fillWidth: true; text: qsTr("备份工作台、样式、配方、偏好与照片元数据。开发权限、密钥、控制权、原始照片和设备身份保留本机；C1 参数只入档，不会自动应用。"); color: "#cccccc"; font.pixelSize: 16; wrapMode: Text.WordWrap }
                    MediaButton { Layout.fillWidth: true; text: qsTr("创建配置备份"); enabled: page.systemController && !page.systemController.busy; onClicked: page.systemController.createDefaultBackup() }
                    TextField { id: backupPath;objectName:"systemBackupPath"; Layout.fillWidth: true; Layout.preferredHeight: 64; placeholderText: qsTr("备份文件的绝对路径 .tar.gz"); font.pixelSize: 18; selectByMouse: true;onActiveFocusChanged:page.reveal(backupPath);onTextEdited:if(page.systemController)page.systemController.clearInspection("backup") }
                    RowLayout {
                        Layout.fillWidth: true
                        MediaButton { Layout.fillWidth: true; text: qsTr("检查备份"); enabled: backupPath.text.length > 0 && page.systemController && !page.systemController.busy; onClicked: page.systemController.inspectBackup(backupPath.text) }
                        MediaButton { Layout.fillWidth: true; text: qsTr("确认恢复配置"); enabled: page.systemController && page.systemController.backupInfo.restorable === true && page.samePath(page.systemController.backupInfo.sourcePath,backupPath.text) && !page.systemController.busy; onClicked: page.confirm("restore","恢复已校验的配置？") }
                    }
                    Text { Layout.fillWidth: true; text: page.systemController && page.systemController.backupInfo.files ? qsTr("已校验 ") + page.systemController.backupInfo.files.length + qsTr(" 项配置；恢复不会触发拍摄。") : qsTr("选择备份后先检查，再确认恢复。"); color: "#aaaaaa"; font.pixelSize: 15; wrapMode: Text.WordWrap }
                    Text {Layout.fillWidth:true;visible:page.systemController && page.systemController.backupInfo.restorable === false;text:page.systemController ? (page.systemController.backupInfo.reason ? qsTr(page.systemController.backupInfo.reason) : qsTr("媒体元数据存在冲突，未恢复任何配置。")) : "";color:"#dddddd";font.pixelSize:14;wrapMode:Text.WordWrap}
                }
                ColumnLayout {
                    width:content.width; visible: page.tab === 2
                    Text { Layout.fillWidth: true; text: qsTr("更新会先准备完整版本，校验后切换；旧版本保留用于回退。"); color: "#cccccc"; font.pixelSize: 16; wrapMode: Text.WordWrap }
                    TextField { id: updatePath; Layout.fillWidth: true; Layout.preferredHeight: 64; placeholderText: qsTr("更新包绝对路径 .tar.gz"); font.pixelSize: 18; selectByMouse: true;onActiveFocusChanged:page.reveal(updatePath);onTextEdited:if(page.systemController)page.systemController.clearInspection("update") }
                    TextField { id: updateHash; Layout.fillWidth: true; Layout.preferredHeight: 64; placeholderText: qsTr("发布包的 SHA-256"); maximumLength: 64; font.pixelSize: 16; selectByMouse: true;inputMethodHints:Qt.ImhLatinOnly|Qt.ImhNoAutoUppercase|Qt.ImhNoPredictiveText;onActiveFocusChanged:page.reveal(updateHash);onTextEdited:if(page.systemController)page.systemController.clearInspection("update") }
                    RowLayout {
                        Layout.fillWidth: true
                        MediaButton { Layout.fillWidth: true; text: qsTr("检查更新"); enabled: page.systemController && !page.systemController.busy && updateHash.text.length === 64; onClicked: page.systemController.inspectUpdate(updatePath.text,updateHash.text.toLowerCase()) }
                        MediaButton { Layout.fillWidth: true; text: qsTr("安装已检查版本"); enabled: page.systemController && page.systemController.nativeReady && page.systemController.updateInfo.version && page.samePath(page.systemController.updateInfo.path,updatePath.text) && page.systemController.updateInfo.sha256 === updateHash.text.trim().toLowerCase() && !page.systemController.busy; onClicked: page.confirm("activate-update","安装应用更新？") }
                        MediaButton { Layout.fillWidth: true; text: qsTr("回退应用"); enabled: page.systemController && page.systemController.nativeReady && !page.systemController.busy; onClicked: page.confirm("rollback-update","恢复上一个应用版本？") }
                    }
                    Text { Layout.fillWidth: true; text: page.systemController && page.systemController.updateInfo.version ? qsTr("已校验 ") + page.systemController.updateInfo.version + " · " + page.systemController.updateInfo.files + qsTr(" 个文件") : qsTr("请使用本次交付的安装包与校验摘要。"); color: "#aaaaaa"; font.pixelSize: 15; wrapMode: Text.WrapAnywhere }
                }
                ColumnLayout {
                    width:content.width; visible: page.tab === 3
                    Switch { text: qsTr("开发模式"); Layout.preferredHeight: 64; font.pixelSize: 18; checked: page.systemController && page.systemController.preferences.developer_mode;enabled:page.systemController && !page.systemController.busy && (!page.systemController.state.native || page.systemController.nativeReady); onToggled: page.pref("developer_mode",checked) }
                    Text { Layout.fillWidth: true; text: qsTr("日常联机使用受控相机接口。开发模式保留已授权 USB 主机的终端与诊断能力。\n诊断导出会排除无线密码、私钥和会话令牌。"); color: "#bbbbbb"; font.pixelSize: 16; wrapMode: Text.WordWrap }
                }
                ColumnLayout {
                    width:content.width;visible:page.tab === 4
                    Switch {text:qsTr("启用受控联机");Layout.preferredHeight:64;font.pixelSize:18;checked:page.linkController && page.linkController.enabled;enabled:!!page.linkController;onToggled:if(page.linkController)page.linkController.setEnabled(checked)}
                    RowLayout {
                        Layout.fillWidth:true
                        MediaButton {Layout.fillWidth:true;text:qsTr("开启局域网");enabled:page.linkController && page.linkController.enabled && !page.linkController.webEnabled;onClicked:page.linkController.startWeb()}
                        MediaButton {Layout.fillWidth:true;text:qsTr("关闭局域网");enabled:page.linkController && page.linkController.webEnabled;onClicked:page.linkController.stopWeb()}
                    }
                    Text {text:qsTr("配对码");color:"#cccccc";font.pixelSize:18}
                    NumericReadout {Layout.fillWidth:true;Layout.preferredHeight:48;value:page.linkController && page.linkController.pairingCode ? page.linkController.pairingCode : "— — —";pixelSize:28;ink:"#ffffff";activityName:"pairing-code"}
                    TextEdit {Layout.fillWidth:true;Layout.preferredHeight:64;readOnly:true;selectByMouse:true;text:page.linkController && page.linkController.webUrl ? page.linkController.webUrl : qsTr("局域网服务尚未开启");color:"#eeeeee";font.pixelSize:18;wrapMode:TextEdit.WrapAnywhere}
                    Text {Layout.fillWidth:true;text:page.linkController && page.linkController.owner ? qsTr("当前控制：")+page.linkController.owner : qsTr("当前控制：本机");color:"#bbbbbb";font.pixelSize:18}
                    MediaButton {Layout.fillWidth:true;text:qsTr("本机接管");enabled:page.linkController && !!page.linkController.owner;onClicked:page.linkController.takeLocalControl()}
                    Text {Layout.fillWidth:true;text:page.linkController ? qsTr(page.linkController.message) : qsTr("联机服务未就绪");color:"#cccccc";font.pixelSize:15;wrapMode:Text.WordWrap}
                    Text {Layout.fillWidth:true;text:qsTr("USB 使用固定受控接口；本机快门和拨轮随时接管。首次网页连接输入配对码；代码过期后可关闭并重新开启局域网服务。");color:"#aaaaaa";font.pixelSize:14;wrapMode:Text.WordWrap}
                }
            }
        }
        Text {visible:!page.keyboardOpen; Layout.fillWidth: true; text: page.systemController ? qsTr(page.systemController.message) : ""; color: "#cccccc"; font.pixelSize: 14; wrapMode: Text.WrapAnywhere; maximumLineCount: 3 }
    }
    Dialog {
        id: confirmDialog; parent: Overlay.overlay; modal: true; focus: true
        property string action: ""; property string titleText: ""
        property string argument:""
        width: Math.min(600,page.width-32); x: (parent.width-width)/2; y: 28
        background: Rectangle { color: "#151515"; border.color: "#666666"; radius: 8 }
        contentItem: ColumnLayout {
            spacing: 18
            Text { Layout.fillWidth: true; text: qsTr(confirmDialog.titleText); color: "white"; font.pixelSize: 24; font.weight: Font.Medium; wrapMode: Text.WordWrap }
            Text {Layout.fillWidth:true;visible:confirmDialog.argument.length>0;text:confirmDialog.argument;color:"#cccccc";font.pixelSize:18;wrapMode:Text.WrapAnywhere}
            RowLayout {
                Layout.fillWidth: true
                MediaButton { Layout.fillWidth: true; text: qsTr("取消"); onClicked: confirmDialog.close() }
                MediaButton { Layout.fillWidth: true; text: qsTr("确认"); emphasized: true; onClicked: { if(confirmDialog.action === "restore") page.systemController.restoreBackup(); else if(confirmDialog.action === "set-time")page.systemController.setUtcTime(confirmDialog.argument); else page.systemController.systemAction(confirmDialog.action); confirmDialog.close() } }
            }
        }
    }
}
