import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: playback
    objectName: "cameraPlayback"
    property var cameraController: null
    property var apps: null
    property var theme: ({})
    readonly property var media: apps ? apps.media : null
    readonly property var pageItems: media ? media.items : cameraController ? cameraController.libraryItems : []
    readonly property int pageCount: pageItems ? pageItems.length : 0
    readonly property bool loading: cameraController ? cameraController.libraryLoading : false
    readonly property bool canRead: cameraController ? cameraController.canBrowseCaptures : false
    readonly property bool canAct: canRead && !loading && !cameraController.busy
    readonly property string selectedId: cameraController ? cameraController.selectedCaptureId : ""
    readonly property int pageIndex: indexOfCapture(selectedId)
    readonly property bool hasOlderPage: cameraController && cameraController.libraryNextCursor.length > 0
    readonly property bool overlayOpen: infoPopup.visible || exportDialog.visible || ratingPopup.visible || libraryPopup.visible || trashPopup.visible || editPopup.visible
    property bool completed: false
    property bool entryActive: false
    property bool entryRefreshNeeded: false
    property bool waitingForPage: false
    property bool nativeZoom: false
    property real pixelZoom: 1
    property real centerX: cameraController && cameraController.reviewWidth ? cameraController.reviewWidth / 2 : 0
    property real centerY: cameraController && cameraController.reviewHeight ? cameraController.reviewHeight / 2 : 0
    property real panX: 0
    property real panY: 0
    property bool modernLibraryOpen: false
    readonly property string mediaStatus: media ? media.message : ""
    onMediaStatusChanged: statusTimer.restart()
    signal closeRequested()

    function indexOfCapture(identity) {
        for (var i = 0; i < pageItems.length; ++i)
            if (pageItems[i].request_id === identity) return i
        return -1
    }
    function selectRequest(identity) {
        if (!canAct || indexOfCapture(identity) < 0) return false
        cameraController.selectCapture(identity)
        return true
    }
    function selectRelative(offset) {
        var i = pageIndex + offset
        if (i >= 0 && i < pageCount) selectRequest(pageItems[i].request_id)
    }
    function updateEntry() {
        if (!completed || !visible || !entryActive || !cameraController) return
        if (entryRefreshNeeded && canAct) {
            entryRefreshNeeded = false; waitingForPage = true
            cameraController.refreshLibrary(); return
        }
        if (waitingForPage && !loading) {
            // Library completion and the media-filter projection notify in the
            // same event turn. Select only after both bindings have settled.
            entrySettle.restart()
        }
    }
    function enterPlayback() {
        if (!completed || !visible || entryActive) return
        entryActive = true; entryRefreshNeeded = true; updateEntry()
    }
    function refreshNewest() {
        if (canAct) { waitingForPage = true; cameraController.refreshLibrary() }
    }
    function olderPage() {
        if (canAct && hasOlderPage) { waitingForPage = true; cameraController.nextLibraryPage() }
    }
    function closeOverlay() {
        var popups = [exportDialog, infoPopup, ratingPopup, libraryPopup, trashPopup, editPopup]
        for (var i = 0; i < popups.length; ++i) if (popups[i].visible) { popups[i].close(); return true }
        return false
    }
    function closeOverlays() { var modernClosed = modernGallery.closeOverlays(); return closeOverlay() || modernClosed }
    function validExportPath(value) {
        return value.length > 0 && !/[\x00-\x1f]/.test(value) && /^(?:[A-Za-z]:[\\\/]|\/)/.test(value) && /\.dng$/i.test(value) && value.indexOf("://") < 0
    }
    function submitExport() {
        if (!canAct || !selectedId || exportDialog.submitted) return
        var path = exportPath.text.trim()
        if (!path && apps) path = apps.exportDirectory + "/photo-" + selectedId + "-" + Date.now() + (exportDialog.jpeg ? ".jpg" : ".dng")
        if (exportDialog.jpeg) {
            if (!/^(?:[A-Za-z]:[\\\/]|\/)/.test(path) || !/\.jpe?g$/i.test(path) || /[\x00-\x1f]/.test(path)) { exportDialog.localMessage = "请选择新的 JPEG 绝对路径"; return }
            exportPath.text = path; exportDialog.submitted = true
            media.exportJpeg(path); return
        }
        if (!validExportPath(path)) { exportDialog.localMessage = "请选择新的 .dng 绝对路径"; return }
        exportPath.text = path; exportDialog.submitted = true
        cameraController.exportSelected(path)
    }
    function requestTile() {
        if (!nativeZoom || !cameraController || !cameraController.reviewWidth || !cameraController.loadReviewTile) return
        var rw = cameraController.reviewWidth, rh = cameraController.reviewHeight
        var w = Math.min(rw, Math.ceil(imageStage.width / pixelZoom))
        var h = Math.min(rh, Math.ceil(imageStage.height / pixelZoom))
        centerX = Math.max(w / 2, Math.min(rw - w / 2, centerX))
        centerY = Math.max(h / 2, Math.min(rh - h / 2, centerY))
        cameraController.loadReviewTile(Math.max(0, Math.floor(centerX - w / 2)), Math.max(0, Math.floor(centerY - h / 2)), w, h)
    }
    function toggleZoom() {
        nativeZoom = !nativeZoom; pixelZoom = 1
        centerX = cameraController.reviewWidth / 2; centerY = cameraController.reviewHeight / 2
        requestTile()
    }
    onSelectedIdChanged: { nativeZoom = false; pixelZoom = 1; panX = 0; panY = 0 }
    onVisibleChanged: {
        if (visible) enterPlayback()
        else { entryActive = false; waitingForPage = false; modernLibraryOpen = false; closeOverlay() }
    }
    Component.onCompleted: { completed = true; enterPlayback() }
    onPageItemsChanged: if (waitingForPage) updateEntry()
    onCanActChanged: if (waitingForPage) updateEntry()
    Timer {
        id: statusTimer
        interval: 4500
    }
    Timer {
        id: entrySettle
        interval: 0; repeat: false
        onTriggered: {
            if (!playback.visible || !playback.entryActive || !playback.waitingForPage || playback.loading) return
            if (!playback.canAct) return
            if (playback.pageCount > 0 && playback.pageIndex < 0) playback.selectRequest(playback.pageItems[0].request_id)
            playback.waitingForPage = false
        }
    }
    Connections {
        target: playback.cameraController
        function onChanged() {
            playback.updateEntry()
            if (exportDialog.submitted && !exportDialog.jpeg && !exportDialog.sidecarSubmitted && playback.cameraController.exportMessage.indexOf("Exported ") === 0) {
                exportDialog.sidecarSubmitted = true
                if (playback.media) playback.media.exportSidecar(exportPath.text + ".daos.json")
            }
        }
    }
    focus: visible
    Keys.onLeftPressed: if (!overlayOpen) { selectRelative(-1); event.accepted = true }
    Keys.onRightPressed: if (!overlayOpen) { selectRelative(1); event.accepted = true }
    Keys.onEscapePressed: { if (!closeOverlay()) closeRequested(); event.accepted = true }

    Rectangle { anchors.fill: parent; color: "#000000" }
    RowLayout {
        id: topBar
        anchors { top: parent.top; left: parent.left; right: parent.right }
        height: 64; spacing: 4
        MediaButton { objectName: "playbackCloseButton"; text: qsTr("返回"); onClicked: playback.closeRequested() }
        MediaButton { objectName: "wholeLibraryOpenButton"; text: qsTr("照片库"); enabled: playback.canRead; onClicked: { if (playback.media && playback.media.gallery && playback.media.gallery.available) { modernGallery.viewer = false; playback.modernLibraryOpen = true } else libraryPopup.open() } }
        Text {
            Layout.fillWidth: true; color: "#eeeeee"; font.pixelSize: 14
            text: playback.pageIndex >= 0 ? (playback.pageIndex + 1) + " / " + playback.pageCount + (playback.media && playback.media.busy ? qsTr(" · 保存中") : playback.media && playback.media.rating ? "    " + playback.media.rating + qsTr(" 星") : "") : qsTr("已保存照片")
            elide: Text.ElideRight
        }
        MediaButton { objectName: "playbackZoomButton"; text: playback.nativeZoom ? Math.round(playback.pixelZoom * 100) + "%" : qsTr("适屏 / 100%"); enabled: playback.selectedId.length > 0; onClicked: playback.toggleZoom() }
        MediaButton { objectName: "playbackInfoButton"; text: qsTr("信息"); enabled: playback.selectedId.length > 0; onClicked: infoPopup.open() }
    }
    Item {
        id: imageStage
        anchors { left: parent.left; right: parent.right; top: topBar.bottom; bottom: bottomBar.top }
        clip: true
        Image {
            id: reviewImage
            objectName: "playbackReviewImage"
            anchors.fill: parent
            source: playback.cameraController ? (playback.media && playback.media.previewUrl ? playback.media.previewUrl : playback.cameraController.reviewOriginalUrl || playback.cameraController.reviewUrl) : ""
            fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false
            visible: !playback.nativeZoom
        }
        Image {
            id: tileImage
            objectName: "playbackOriginalTile"
            visible: playback.nativeZoom
            source: playback.nativeZoom && playback.cameraController ? playback.cameraController.reviewTileUrl : ""
            width: playback.cameraController ? playback.cameraController.reviewTileWidth * playback.pixelZoom : 0
            height: playback.cameraController ? playback.cameraController.reviewTileHeight * playback.pixelZoom : 0
            x: (imageStage.width - width) / 2 + playback.panX
            y: (imageStage.height - height) / 2 + playback.panY
            smooth: false; cache: false; asynchronous: true
        }
        Text {
            anchors.centerIn: parent; width: Math.min(parent.width - 150, 540)
            visible: !playback.selectedId || (!reviewImage.source.toString() && !playback.nativeZoom)
            text: playback.loading ? qsTr("正在读取照片…") : playback.pageCount ? qsTr("从照片库选择一张照片") : qsTr("当前筛选没有照片，请到照片库切换筛选。")
            color: "#bcbcbc"; font.pixelSize: 18; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
        }
        PinchArea {
            anchors.fill: parent
            property real initialZoom: 1
            onPinchStarted: initialZoom = playback.pixelZoom
            onPinchUpdated: {
                if (!playback.nativeZoom && pinch.scale > 1.15) { playback.nativeZoom = true; playback.pixelZoom = 1; playback.requestTile() }
                else if (playback.nativeZoom) playback.pixelZoom = Math.max(1, Math.min(8, initialZoom * pinch.scale))
            }
            onPinchFinished: {
                if (playback.nativeZoom && pinch.scale < .75 && playback.pixelZoom <= 1.01) playback.nativeZoom = false
                else playback.requestTile()
            }
            MouseArea {
                anchors.fill: parent
                property real startX: 0
                property real startY: 0
                onPressed: { startX = mouse.x; startY = mouse.y }
                onPositionChanged: if (pressed && playback.nativeZoom) { playback.panX = mouse.x - startX; playback.panY = mouse.y - startY }
                onReleased: {
                    if (playback.nativeZoom) {
                        playback.centerX -= playback.panX / playback.pixelZoom; playback.centerY -= playback.panY / playback.pixelZoom
                        playback.panX = 0; playback.panY = 0; playback.requestTile()
                    } else if (mouse.x - startX > 90) playback.selectRelative(-1)
                    else if (mouse.x - startX < -90) playback.selectRelative(1)
                }
                onDoubleClicked: playback.toggleZoom()
            }
        }
        MediaButton { objectName: "playbackPreviousButton"; anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter; width: 64; text: "‹"; font.pixelSize: 32; enabled: playback.canAct && playback.pageIndex > 0; onClicked: playback.selectRelative(-1) }
        MediaButton { objectName: "playbackNextButton"; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; width: 64; text: "›"; font.pixelSize: 32; enabled: playback.canAct && playback.pageIndex >= 0 && playback.pageIndex < playback.pageCount - 1; onClicked: playback.selectRelative(1) }
        Rectangle {
            visible: playback.nativeZoom || (playback.media && playback.media.trashed)
            anchors { left: parent.left; bottom: parent.bottom; margins: 8 }
            color: "#111111"; width: badge.implicitWidth + 20; height: 30
            Text { id: badge; anchors.centerIn: parent; color: "#cccccc"; font.pixelSize: 12; text: playback.nativeZoom ? qsTr("原始像素 · 拖动检查") : qsTr("回收站 · RAW 已保留") }
        }
        Rectangle {
            anchors { bottom: parent.bottom; horizontalCenter: parent.horizontalCenter; margins: 6 }
            width: Math.min(parent.width - 20, statusText.implicitWidth + 24); height: statusText.implicitHeight + 14
            visible: statusTimer.running && playback.mediaStatus.length > 0
            color: "#202020"; border.color: "#666666"; radius: 4
            Text { id: statusText; anchors.centerIn: parent; width: Math.min(implicitWidth, imageStage.width - 44); text: playback.mediaStatus; color: "#eeeeee"; font.pixelSize: 13; wrapMode: Text.WordWrap }
        }
    }
    RowLayout {
        id: bottomBar
        anchors { left: parent.left; right: parent.right; bottom: parent.bottom }
        height: 64; spacing: 4
        MediaButton { objectName: "playbackRateButton"; Layout.fillWidth: true; text: qsTr("评分"); enabled: !!playback.media && playback.media.ready && !playback.media.busy; onClicked: ratingPopup.open() }
        MediaButton { objectName: "playbackProtectButton"; Layout.fillWidth: true; text: playback.media && playback.media.protected ? qsTr("取消保护") : qsTr("保护"); emphasized: playback.media && playback.media.protected; enabled: !!playback.media && playback.media.ready && !playback.media.busy; onClicked: playback.media.setProtected(!playback.media.protected) }
        MediaButton { objectName: "playbackTrashButton"; Layout.fillWidth: true; text: playback.media && playback.media.trashed ? qsTr("恢复") : qsTr("回收站"); enabled: !!playback.media && playback.media.ready && !playback.media.busy; onClicked: { if (playback.media.trashed) playback.media.restore(); else trashPopup.open() } }
        MediaButton { objectName: "playbackEditButton"; Layout.fillWidth: true; text: qsTr("编辑"); enabled: !!playback.media && playback.media.ready && !playback.media.busy; onClicked: editPopup.open() }
        MediaButton { objectName: "playbackExportButton"; Layout.fillWidth: true; text: qsTr("导出"); enabled: playback.canAct && playback.selectedId.length > 0; onClicked: exportDialog.open() }
        MediaButton { objectName: "playbackOlderPageButton"; Layout.fillWidth: true; text: qsTr("更早"); enabled: playback.canAct && playback.hasOlderPage; onClicked: playback.olderPage() }
        MediaButton { objectName: "playbackRefreshButton"; Layout.fillWidth: true; text: qsTr("最新"); enabled: playback.canAct; onClicked: playback.refreshNewest() }
    }

    Popup {
        id: libraryPopup
        objectName: "playbackLibraryPopup"
        parent: playback; x: 12; y: 8; width: playback.width - 24; height: playback.height - 16
        modal: true; focus: true; padding: 12
        background: Rectangle { color: "#111111"; border.color: "#666666"; radius: 8 }
        contentItem: ColumnLayout {
            spacing: 8
            RowLayout {
                Layout.fillWidth: true; spacing: 4
                Repeater {
                    model: [{key:"all",label:"全部"},{key:"rated",label:"已评分"},{key:"protected",label:"已保护"},{key:"trash",label:"回收站"}]
                    MediaButton { Layout.fillWidth: true; text: qsTr(modelData.label); emphasized: playback.media && playback.media.filterName === modelData.key; onClicked: playback.media.setFilter(modelData.key) }
                }
                MediaButton { text: qsTr("关闭"); onClicked: libraryPopup.close() }
            }
            Text { text: qsTr("筛选当前页 · ") + playback.pageCount + qsTr(" 张。更早 / 最新切换页面。"); color: "#aaaaaa"; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            ListView {
                objectName: "playbackLibraryList"; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4
                model: playback.pageItems
                delegate: MediaButton {
                    width: ListView.view.width; height: 72
                    emphasized: modelData.request_id === playback.selectedId
                    text: (modelData.rating ? modelData.rating + "★  " : "") + (modelData.protected ? qsTr("[保护]  ") : "") + modelData.filename + "    " + modelData.width + "×" + modelData.height
                    onClicked: { playback.selectRequest(modelData.request_id); libraryPopup.close() }
                }
            }
            Text { text: playback.media ? (typeof i18n !== "undefined" ? i18n.t(playback.media.message, i18n.revision) : qsTr(playback.media.message)) : ""; visible: text.length > 0; color: "#bcbcbc"; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12 }
        }
    }
    Popup {
        id: ratingPopup
        objectName: "playbackRatingPopup"
        parent: playback; x: (playback.width - width) / 2; y: (playback.height - height) / 2
        width: Math.min(playback.width - 24, 600); height: 168; modal: true; focus: true; padding: 12
        background: Rectangle { color: "#111111"; border.color: "#777777"; radius: 8 }
        contentItem: ColumnLayout {
            Text { text: qsTr("照片评分"); color: "#ffffff"; font.pixelSize: 18 }
            RowLayout {
                Layout.fillWidth: true
                Repeater { model: 6; MediaButton { Layout.fillWidth: true; text: index ? index + "★" : qsTr("无"); enabled: !!playback.media && playback.media.ready && !playback.media.busy; emphasized: playback.media && playback.media.rating === index; onClicked: { playback.media.setRating(index); ratingPopup.close() } } }
            }
            Text { text: qsTr("评分保存于旁车，原始 DNG 不变。"); color: "#aaaaaa"; font.pixelSize: 12 }
        }
    }
    Popup {
        id: trashPopup
        parent: playback; x: 70; y: (playback.height - height) / 2; width: playback.width - 140; height: 222
        modal: true; focus: true; padding: 16
        background: Rectangle { color: "#111111"; border.color: "#777777"; radius: 8 }
        contentItem: ColumnLayout {
            Text { text: qsTr("移入照片回收站？"); color: "#ffffff"; font.pixelSize: 20 }
            Text { text: qsTr("原始 DNG 仍然保留，可在照片库 → 回收站恢复。受保护照片无法移入回收站。"); color: "#cccccc"; font.pixelSize: 15; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout { Layout.fillWidth: true; MediaButton { Layout.fillWidth: true; text: qsTr("保留照片"); onClicked: trashPopup.close() } MediaButton { Layout.fillWidth: true; text: qsTr("移入回收站"); emphasized: true; onClicked: { playback.media.moveToTrash(); trashPopup.close() } } }
        }
    }
    Popup {
        id: editPopup
        objectName: "playbackEditPopup"
        parent: playback; x: playback.width - width; y: 0; width: Math.min(350, playback.width - 80); height: playback.height
        modal: false; focus: true; padding: 12
        background: Rectangle { color: "#141414"; border.color: "#666666" }
        contentItem: ColumnLayout {
            spacing: 8
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: qsTr("预览编辑"); color: "#ffffff"; font.pixelSize: 20 } MediaButton { text: qsTr("完成"); onClicked: editPopup.close() } }
            ScrollView {
                id: editScroll
                Layout.fillWidth: true; Layout.fillHeight: true; clip: true; contentWidth: availableWidth
                ColumnLayout {
                    width: editScroll.availableWidth; spacing: 8
            Repeater {
                model: [{key:"exposure",label:"曝光 / EV",min:-5,max:5,step:.1},{key:"contrast",label:"对比度",min:.5,max:2,step:.05},{key:"saturation",label:"饱和度",min:0,max:2,step:.05}]
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 0
                    Text { text: qsTr(modelData.label) + "    " + (playback.media ? Number(playback.media.selectedEdits[modelData.key]).toFixed(2) : ""); color: "#dddddd"; font.pixelSize: 14 }
                    Slider { objectName: "playbackEdit-" + modelData.key; Layout.fillWidth: true; Layout.preferredHeight: 64; from: modelData.min; to: modelData.max; stepSize: modelData.step; value: playback.media ? playback.media.selectedEdits[modelData.key] : 1; onMoved: playback.media.setEdit(modelData.key, value) }
                }
            }
            Text { Layout.fillWidth: true; text: qsTr("显示预览调整单独保存。RAW 像素不变；100% 检查显示原图。"); color: "#aaaaaa"; font.pixelSize: 12; wrapMode: Text.WordWrap }
            MediaButton { Layout.fillWidth: true; text: qsTr("旋转 90°"); onClicked: playback.media.rotate() }
            RowLayout { Layout.fillWidth: true; spacing: 4; Repeater { model: ["original","1:1","3:2","4:3"]; MediaButton { Layout.fillWidth: true; implicitWidth: 60; text: modelData === "original" ? qsTr("原比例") : qsTr(modelData); onClicked: playback.media.setCrop(modelData) } } }
                }
            }
            MediaButton { Layout.fillWidth: true; text: qsTr("重置编辑"); onClicked: playback.media.resetEdits() }
        }
    }
    Popup {
        id: infoPopup
        objectName: "playbackInfoPopup"
        parent: playback; x: 24; y: 12; width: playback.width - 48; height: playback.height - 24
        modal: true; focus: true; padding: 12
        background: Rectangle { color: "#111111"; border.color: "#555555"; radius: 8 }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; Text { Layout.fillWidth: true; text: qsTr("拍摄档案"); color: "#ffffff"; font.pixelSize: 20 } MediaButton { objectName: "playbackInfoCloseButton"; text: qsTr("关闭"); onClicked: infoPopup.close() } }
            ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; TextArea { objectName: "playbackInfoText"; text: playback.media ? playback.media.archiveText : playback.cameraController.selectedCaptureInfo; readOnly: true; selectByMouse: true; wrapMode: TextEdit.WrapAnywhere; textFormat: TextEdit.PlainText; color: "#cccccc"; font.pixelSize: 13; background: null } }
        }
    }
    Popup {
        id: exportDialog
        objectName: "playbackExportDialog"
        property bool submitted: false
        property bool jpeg: false
        property bool sidecarSubmitted: false
        property string localMessage: ""
        parent: playback; x: 24; y: 12; width: playback.width - 48; height: playback.height - 24
        modal: true; focus: true; padding: 16
        onOpened: { submitted = false; sidecarSubmitted = false; jpeg = false; localMessage = ""; exportPath.text = "" }
        background: Rectangle { color: "#111111"; border.color: "#666666"; radius: 8 }
        contentItem: ColumnLayout {
            spacing: 10
            Text { text: qsTr("导出照片"); color: "#ffffff"; font.pixelSize: 20 }
            RowLayout { Layout.fillWidth: true; MediaButton { Layout.fillWidth: true; text: qsTr("原始 DNG + 旁车"); emphasized: !exportDialog.jpeg; enabled: !exportDialog.submitted; onClicked: { exportDialog.jpeg = false; exportPath.text = "" } } MediaButton { Layout.fillWidth: true; text: qsTr("JPEG 预览样张"); emphasized: exportDialog.jpeg; enabled: !exportDialog.submitted; onClicked: { exportDialog.jpeg = true; exportPath.text = "" } } }
            Text { text: exportDialog.jpeg ? qsTr("最大 2048px 的未标定 SDR 样张，应用预览编辑。原始 RAW 保留。留空自动保存到 Exports。") : qsTr("新的 .dng 绝对路径，留空自动保存到 Exports。成功后附带 .daos.json 编辑旁车；已有文件不会被覆盖。"); color: "#cccccc"; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            TextField { id: exportPath; objectName: "playbackExportPathInput"; Layout.fillWidth: true; Layout.preferredHeight: 64; placeholderText: qsTr("留空 = 自动保存到 Exports"); color: "#eeeeee"; placeholderTextColor: "#aaaaaa"; font.pixelSize: 15; selectByMouse: true; background: Rectangle { color: "#222222"; border.color: "#777777"; radius: 4 } }
            Text { Layout.fillWidth: true; Layout.fillHeight: true; text: exportDialog.localMessage || (exportDialog.jpeg && playback.media ? (typeof i18n !== "undefined" ? i18n.t(playback.media.message, i18n.revision) : qsTr(playback.media.message)) : playback.cameraController ? playback.cameraController.exportMessage + (exportDialog.sidecarSubmitted && playback.media ? "\n" + (typeof i18n !== "undefined" ? i18n.t(playback.media.message, i18n.revision) : qsTr(playback.media.message)) : "") : ""); color: "#bcbcbc"; font.pixelSize: 13; wrapMode: Text.WordWrap }
            RowLayout { Layout.fillWidth: true; MediaButton { objectName: "playbackExportCancelButton"; Layout.fillWidth: true; text: qsTr("关闭"); onClicked: exportDialog.close() } MediaButton { objectName: "playbackExportConfirmButton"; Layout.fillWidth: true; text: qsTr("导出副本"); emphasized: true; enabled: playback.canAct && !exportDialog.submitted && (!playback.media || !playback.media.exporting); onClicked: playback.submitExport() } }
        }
    }
    MediaGallery {
        id: modernGallery
        anchors.fill: parent
        z: 20
        visible: playback.modernLibraryOpen && !!playback.media && !!playback.media.gallery && playback.media.gallery.available
        gallery: playback.media ? playback.media.gallery : null
        taskService: playback.apps ? playback.apps.tasks : null
        processing: typeof imageProcessing !== "undefined" ? imageProcessing : null
        onCloseRequested: playback.closeRequested()
        onToolsRequested: if (typeof cameraShell !== "undefined") { cameraShell.selectGroup("image_tools"); cameraShell.navigate("settings") }
    }
}
