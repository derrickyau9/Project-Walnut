import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id:page
    property var colors:({background:"#000000",text:"#ffffff",muted:"#cccccc",helper:"#aaaaaa",raised:"#222222",line:"#444444"})
    color:colors.background
    property bool openNetwork:false
    property bool unsupported:false
    property bool scanned:false
    function refreshPage() { wifi.refresh(); if(wifi.available && !scanned) { scanned=true; wifi.scan() } }
    onVisibleChanged: if(visible) refreshPage()
    Component.onCompleted: refreshPage()
    Connections { target:wifi; function onChanged() { if(!region.activeFocus) region.text=wifi.country; if(page.visible && wifi.available && !page.scanned && !wifi.busy) {page.scanned=true;wifi.scan()} } }
    Timer { interval:2500; repeat:true; running:page.visible && !region.activeFocus && !ssid.activeFocus && !password.activeFocus; onTriggered:wifi.refresh() }
    function reveal(editor) {
        if(!editor.activeFocus) return
        Qt.callLater(function() {
            var y=editor.mapToItem(form,0,0).y
            viewport.contentY=Math.max(0,Math.min(y-8,viewport.contentHeight-viewport.height))
        })
    }
    Flickable {
        id:viewport
        anchors.fill:parent
        anchors.bottomMargin:softKeyboard.isOpen ? 252 : 0
        clip:true
        contentWidth:width
        contentHeight:form.implicitHeight+20
        boundsBehavior:Flickable.StopAtBounds
        ScrollBar.vertical:ScrollBar {}
        onHeightChanged:page.reveal(region.activeFocus ? region : password.activeFocus ? password : ssid)
    ColumnLayout {
        id:form
        x:10; y:10; width:parent.width-20
        spacing:6
        RowLayout {
            Layout.fillWidth:true
            Label { text:qsTr("地区"); color:page.colors.text }
            TextField { id:region; objectName:"wifiRegionInput"; Layout.preferredWidth:68; Layout.preferredHeight:64; maximumLength:2; placeholderText:qsTr("CN/US"); enabled:!wifi.busy; inputMethodHints:Qt.ImhUppercaseOnly|Qt.ImhNoPredictiveText; onActiveFocusChanged:page.reveal(region); Component.onCompleted:text=wifi.country }
            Button { text:qsTr("应用"); implicitHeight:64; enabled:!wifi.busy && /^[a-zA-Z]{2}$/.test(region.text); onClicked:{softKeyboard.done();wifi.setCountry(region.text)} }
            Label { text:qsTr("实际所在地：CN、US、JP、GB\u2026"); Layout.fillWidth:true; color:page.colors.helper; wrapMode:Text.Wrap; font.pixelSize:14 }
        }
        RowLayout {
            Layout.fillWidth:true
            Label { text:wifi.busy ? qsTr("处理中\u2026") : qsTr(wifi.message); textFormat:Text.PlainText; color:page.colors.muted; wrapMode:Text.Wrap; font.pixelSize:14; Layout.fillWidth:true }
            Button { text:qsTr("扫描"); implicitHeight:64; enabled:!wifi.busy; onClicked:wifi.scan() }
        }
        Label { visible:wifi.connected; text:wifi.ssid+"  "+wifi.address; textFormat:Text.PlainText; color:page.colors.text; font.pixelSize:14; Layout.fillWidth:true }
        ListView {
            Layout.fillWidth:true
            visible:!softKeyboard.isOpen
            Layout.preferredHeight:Math.max(64,Math.min(192,count*64))
            clip:true
            model:wifi.networks
            delegate:ItemDelegate {
                width:ListView.view.width
                height:64
                text:modelData.ssid+"   "+modelData.band+"   "+(modelData.open ? qsTr("开放网络") : qsTr("加密网络"))+"   "+modelData.rssi+qsTr(" dBm")
                onClicked: {
                    ssid.text=modelData.ssid;password.text="";page.openNetwork=modelData.open
                    page.unsupported=modelData.supported === false
                    if(!modelData.open && !page.unsupported) password.forceActiveFocus()
                }
                contentItem:Text { text:parent.text; textFormat:Text.PlainText; color:page.colors.text; elide:Text.ElideRight; verticalAlignment:Text.AlignVCenter; font.pixelSize:18 }
                background:Rectangle { color:page.colors.raised; border.color:page.colors.line }
            }
        }
        TextField { id:ssid; objectName:"wifiSsidInput"; Layout.fillWidth:true; Layout.preferredHeight:64; placeholderText:qsTr("网络名称，或从上方选择"); selectByMouse:true; enabled:wifi.available && !wifi.busy; inputMethodHints:Qt.ImhNoAutoUppercase|Qt.ImhNoPredictiveText; onTextEdited:page.unsupported=false; onActiveFocusChanged:page.reveal(ssid) }
        TextField { id:password; objectName:"wifiPasswordInput"; Layout.fillWidth:true; Layout.preferredHeight:64; placeholderText:qsTr("Wi-Fi 密码"); echoMode:showPassword.checked ? TextInput.Normal : TextInput.Password; enabled:wifi.available && !wifi.busy && !page.openNetwork; inputMethodHints:Qt.ImhNoAutoUppercase|Qt.ImhNoPredictiveText|Qt.ImhSensitiveData; onActiveFocusChanged:page.reveal(password); onAccepted:{softKeyboard.done();wifi.connectNetwork(ssid.text,password.text,page.openNetwork);password.text=""} }
        RowLayout {
            Layout.fillWidth:true
            CheckBox { implicitHeight:64; text:qsTr("开放网络"); checked:page.openNetwork; onToggled:page.openNetwork=checked }
            CheckBox { id:showPassword; implicitHeight:64; text:qsTr("显示密码") }
            Item { Layout.fillWidth:true }
            Button { text:qsTr("断开"); implicitHeight:64; enabled:wifi.available && !wifi.busy; onClicked:wifi.disconnectNetwork() }
            Button { text:qsTr("连接"); implicitHeight:64; enabled:wifi.available && !wifi.busy && ssid.text.length>0 && !page.unsupported; onClicked:{softKeyboard.done();wifi.connectNetwork(ssid.text,password.text,page.openNetwork);password.text=""} }
        }
        Label { text:page.unsupported ? qsTr("支持开放网络或 WPA/WPA2 个人网络；暂不支持 WEP、企业或仅 WPA3 网络。") : qsTr("已保存网络会在启动时重连。当前 Pi4 构建仅启用 5 GHz，未启用 2.4 GHz。"); color:page.colors.helper; font.pixelSize:14; wrapMode:Text.Wrap; Layout.fillWidth:true }
    }
    }
}
