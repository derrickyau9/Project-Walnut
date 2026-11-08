import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: keyboard
    objectName: "touchKeyboard"
    height: 252
    readonly property bool hasController: typeof softKeyboard !== "undefined" && softKeyboard !== null
    // Older review tools can load the shell without installing a keyboard.
    // An inert data object keeps every child binding valid in those callers.
    readonly property var inputController: hasController ? softKeyboard : ({
        isOpen:false, terminal:false, sensitive:false, pinyinEnabled:false,
        inputLanguage:"en", mode:"letters", shiftState:0, editorText:"",
        cursorPosition:0, fieldLabel:"Text", preeditText:"", preeditCursor:0,
        candidates:[], candidatePage:0, hasNextCandidates:false, inputStatus:""
    })
    visible: typeof softKeyboard !== "undefined" && softKeyboard.isOpen
    color: "#111111"
    border.color: "#555555"
    border.width: 1
    // Overlay keys and the mirrored editor never take focus from the real field.
    z: 10000
    readonly property var letterRows: ["q w e r t y u i o p", "a s d f g h j k l", "z x c v b n m"]
    readonly property var numberRows: ["1 2 3 4 5 6 7 8 9 0", "- + . , : / ( ) % =", "_ @ # $ & * ! ? \\ ;"]
    readonly property var symbolRows: ["! @ # $ % ^ & * ( )", "[ ] { } < > = + - _", "/ \\ : ; \" ' , . ? ~"]
    readonly property var rows: keyboard.inputController.mode === "letters" ? letterRows : keyboard.inputController.mode === "numbers" ? numberRows : symbolRows

    component Key: Button {
        id: keyButton
        focusPolicy: Qt.NoFocus
        activeFocusOnTab: false
        Layout.fillWidth: true
        Layout.fillHeight: true
        implicitHeight: 36
        implicitWidth: 44
        padding: 0
        property bool emphasized: false
        scale: down ? 0.96 : 1.0
        Behavior on scale { NumberAnimation { duration: 65; easing.type: Easing.OutCubic } }
        background: Rectangle {
            radius: 4
            color: keyButton.down ? "#777777" : keyButton.emphasized ? "#555555" : "#292929"
            border.color: keyButton.enabled ? "#666666" : "#333333"
            Behavior on color { ColorAnimation { duration: 75 } }
        }
        contentItem: Text {
            text: keyButton.text
            color: keyButton.enabled ? "#ffffff" : "#888888"
            textFormat: Text.PlainText
            font.pixelSize: keyButton.text.length > 3 ? 13 : 19
            font.weight: Font.Medium
            horizontalAlignment: Text.AlignHCenter
            verticalAlignment: Text.AlignVCenter
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 6
        spacing: 4
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumHeight: 36
            Layout.maximumHeight: 36
            spacing: 4
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: "#000000"
                radius: 4
                clip: true
                TextInput {
                    id: editorPreview
                    objectName: "keyboardCursorEditor"
                    anchors.fill: parent
                    anchors.margins: 5
                    text: keyboard.inputController.editorText
                    cursorPosition: keyboard.inputController.cursorPosition
                    cursorVisible: !keyboard.inputController.terminal
                    activeFocusOnPress: false
                    readOnly: true
                    selectByMouse: false
                    autoScroll: true
                    color: "#ffffff"
                    selectionColor: "#555555"
                    verticalAlignment: TextInput.AlignVCenter
                    font.pixelSize: 17
                    Accessible.name: keyboard.inputController.sensitive ? qsTranslate("SoftKeyboard", "密码光标位置") : qsTranslate("SoftKeyboard", "当前编辑内容，点按定位光标")
                }
                Text {
                    anchors.fill: parent
                    anchors.margins: 6
                    visible: keyboard.inputController.terminal || keyboard.inputController.editorText.length === 0
                    text: keyboard.inputController.terminal ? qsTranslate("SoftKeyboard", "终端原始输入 · 保留方向键和 Ctrl+C") : qsTranslate("SoftKeyboard", keyboard.inputController.fieldLabel)
                    textFormat: Text.PlainText
                    color: "#aaaaaa"
                    font.pixelSize: 13
                    elide: Text.ElideRight
                    verticalAlignment: Text.AlignVCenter
                }
                MouseArea {
                    anchors.fill: parent
                    enabled: !keyboard.inputController.terminal
                    cursorShape: Qt.IBeamCursor
                    onClicked: keyboard.inputController.setCursorPosition(editorPreview.positionAt(mouse.x - 5, mouse.y - 5))
                }
            }
            Key { text: keyboard.inputController.terminal ? qsTranslate("SoftKeyboard", "Ctrl+C") : qsTranslate("SoftKeyboard", "全选"); Layout.fillWidth: false; Layout.preferredWidth: 60; onClicked: keyboard.inputController.key(keyboard.inputController.terminal ? "interrupt" : "selectAll"); Accessible.name: keyboard.inputController.terminal ? qsTranslate("SoftKeyboard", "中断终端命令") : qsTranslate("SoftKeyboard", "全选文本") }
            Key { text: "\u2190"; Layout.fillWidth: false; Layout.preferredWidth: 40; autoRepeat: true; onClicked: keyboard.inputController.key("left"); Accessible.name: qsTranslate("SoftKeyboard", "光标向左") }
            Key { text: "\u2192"; Layout.fillWidth: false; Layout.preferredWidth: 40; autoRepeat: true; onClicked: keyboard.inputController.key("right"); Accessible.name: qsTranslate("SoftKeyboard", "光标向右") }
            Key { text: qsTranslate("SoftKeyboard", "收起"); Layout.fillWidth: false; Layout.preferredWidth: 52; onClicked: keyboard.inputController.hide(); Accessible.name: qsTranslate("SoftKeyboard", "收起键盘") }
            Key { text: qsTranslate("SoftKeyboard", "完成"); Layout.fillWidth: false; Layout.preferredWidth: 64; emphasized: true; onClicked: keyboard.inputController.done(); Accessible.name: qsTranslate("SoftKeyboard", "确认文字并结束编辑") }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumHeight: 32
            Layout.maximumHeight: 32
            spacing: 4
            Text {
                visible: keyboard.inputController.preeditText.length === 0
                Layout.fillWidth: true
                text: qsTranslate("SoftKeyboard", keyboard.inputController.inputStatus)
                textFormat: Text.PlainText
                color: "#cccccc"
                font.pixelSize: 13
                elide: Text.ElideRight
            }
            Text {
                visible: keyboard.inputController.preeditText.length > 0
                Layout.preferredWidth: Math.min(180, Math.max(100, implicitWidth))
                Layout.maximumWidth: 180
                text: keyboard.inputController.preeditText.substring(0, keyboard.inputController.preeditCursor) + "\u2502" + keyboard.inputController.preeditText.substring(keyboard.inputController.preeditCursor)
                textFormat: Text.PlainText
                color: "#ffffff"
                font.pixelSize: 16
                elide: Text.ElideLeft
            }
            Flickable {
                id: candidateStrip
                objectName: "keyboardCandidates"
                visible: keyboard.inputController.preeditText.length > 0
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: candidateRow.width
                contentHeight: height
                boundsBehavior: Flickable.StopAtBounds
                flickableDirection: Flickable.HorizontalFlick
                Connections { target: keyboard.hasController ? keyboard.inputController : null; function onChanged() { candidateStrip.contentX = 0 } }
                Row {
                    id: candidateRow
                    height: candidateStrip.height
                    spacing: 4
                    Repeater {
                        model: keyboard.inputController.candidates
                        Key {
                            objectName: "pinyinCandidate" + index
                            width: Math.min(172, Math.max(62, candidateText.implicitWidth + 18))
                            height: 32
                            text: (index + 1) + " " + modelData.text
                            emphasized: index === 0
                            contentItem: Text {
                                id: candidateText
                                text: parent.text
                                textFormat: Text.PlainText
                                color: "#ffffff"
                                font.pixelSize: 17
                                verticalAlignment: Text.AlignVCenter
                                horizontalAlignment: Text.AlignHCenter
                                elide: Text.ElideRight
                            }
                            onClicked: keyboard.inputController.chooseCandidate(index)
                            Accessible.name: qsTranslate("SoftKeyboard", "候选 ") + (index + 1) + " " + modelData.text
                            ToolTip.visible: hovered
                            ToolTip.text: modelData.spelling + (modelData.partial ? qsTranslate("SoftKeyboard", " · 继续输入剩余拼音") : "")
                        }
                    }
                }
            }
            Text {
                visible: keyboard.inputController.preeditText.length > 0 && keyboard.inputController.candidates.length === 0
                text: qsTranslate("SoftKeyboard", "空格保留拼音")
                color: "#bbbbbb"
                font.pixelSize: 12
            }
            Key { visible: keyboard.inputController.preeditText.length > 0; text: "‹"; enabled: keyboard.inputController.candidatePage > 0; Layout.fillWidth: false; Layout.preferredWidth: 32; onClicked: keyboard.inputController.pageCandidates(-1); Accessible.name: qsTranslate("SoftKeyboard", "上一页候选") }
            Key { visible: keyboard.inputController.preeditText.length > 0; text: "›"; enabled: keyboard.inputController.hasNextCandidates; Layout.fillWidth: false; Layout.preferredWidth: 32; onClicked: keyboard.inputController.pageCandidates(1); Accessible.name: qsTranslate("SoftKeyboard", "下一页候选") }
            Key { visible: keyboard.inputController.preeditText.length > 0; text: qsTranslate("SoftKeyboard", "取消"); Layout.fillWidth: false; Layout.preferredWidth: 48; onClicked: keyboard.inputController.key("escape"); Accessible.name: qsTranslate("SoftKeyboard", "取消当前拼音") }
        }
        Repeater {
            model: keyboard.rows
            RowLayout {
                Layout.fillWidth: true
                Layout.minimumHeight: 36
                Layout.maximumHeight: 36
                spacing: 4
                Repeater {
                    model: modelData.split(" ")
                    Key {
                        objectName: "keyboardKey_" + modelData
                        readonly property string character: modelData
                        text: keyboard.inputController.mode === "letters" && keyboard.inputController.inputLanguage === "en" && keyboard.inputController.shiftState > 0 ? character.toUpperCase() : character
                        onClicked: keyboard.inputController.commit(text)
                        Accessible.name: text
                    }
                }
                Key {
                    objectName: "keyboardBackspace"
                    visible: index === 2
                    text: "\u232b"
                    Layout.preferredWidth: 88
                    autoRepeat: true
                    autoRepeatDelay: 450
                    autoRepeatInterval: 65
                    onClicked: keyboard.inputController.key("backspace")
                    Accessible.name: qsTranslate("SoftKeyboard", "退格，按住连续删除")
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.minimumHeight: 44
            Layout.maximumHeight: 44
            spacing: 4
            Key { text: keyboard.inputController.inputLanguage === "zh" ? qsTranslate("SoftKeyboard", "中") : qsTranslate("SoftKeyboard", "EN"); Layout.preferredWidth: 56; enabled: keyboard.inputController.pinyinEnabled; emphasized: keyboard.inputController.inputLanguage === "zh"; onClicked: keyboard.inputController.setLanguage(keyboard.inputController.inputLanguage === "zh" ? "en" : "zh"); Accessible.name: qsTranslate("SoftKeyboard", "切换中文拼音与英文") }
            Key { text: qsTranslate("SoftKeyboard", "ABC"); Layout.preferredWidth: 56; onClicked: keyboard.inputController.setMode("letters"); emphasized: keyboard.inputController.mode === "letters" }
            Key { text: "123"; Layout.preferredWidth: 56; onClicked: keyboard.inputController.setMode("numbers"); emphasized: keyboard.inputController.mode === "numbers" }
            Key { text: "#+="; Layout.preferredWidth: 56; onClicked: keyboard.inputController.setMode("symbols"); emphasized: keyboard.inputController.mode === "symbols" }
            Key { text: keyboard.inputController.inputLanguage === "zh" ? qsTranslate("SoftKeyboard", "分词 '") : keyboard.inputController.shiftState === 2 ? qsTranslate("SoftKeyboard", "CAPS") : qsTranslate("SoftKeyboard", "Shift"); Layout.preferredWidth: 60; enabled: keyboard.inputController.mode === "letters"; emphasized: keyboard.inputController.shiftState > 0; onClicked: keyboard.inputController.toggleShift(); Accessible.name: keyboard.inputController.inputLanguage === "zh" ? qsTranslate("SoftKeyboard", "拼音音节分隔符") : qsTranslate("SoftKeyboard", "切换大写") }
            Key { text: keyboard.inputController.preeditText.length > 0 ? qsTranslate("SoftKeyboard", "空格 · 选词") : qsTranslate("SoftKeyboard", "空格"); Layout.preferredWidth: 220; onClicked: keyboard.inputController.commit(" ") }
            Key { text: "/"; Layout.preferredWidth: 44; onClicked: keyboard.inputController.commit("/") }
            Key { text: keyboard.inputController.inputLanguage === "zh" && keyboard.inputController.mode === "letters" ? "。" : "."; Layout.preferredWidth: 44; onClicked: keyboard.inputController.commit(".") }
            Key { text: keyboard.inputController.preeditText.length > 0 ? qsTranslate("SoftKeyboard", "选词") : qsTranslate("SoftKeyboard", "Enter"); Layout.preferredWidth: 76; emphasized: true; onClicked: keyboard.inputController.key("enter"); Accessible.name: qsTranslate("SoftKeyboard", "确认拼音或发送回车") }
        }
    }
}
