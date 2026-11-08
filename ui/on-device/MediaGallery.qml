import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id: page
    objectName: "wholeLibraryGallery"
    property var gallery: null
    property var taskService: null
    property var processing: null
    property bool viewer: false
    property bool nativeZoom: false
    property bool compareMode: false
    property real pixelZoom: 1
    property real centerX: .5
    property real centerY: .5
    property real panX: 0
    property real panY: 0
    property string exportFormat: "original"
    property string fileMode: "import"
    property var chosenPaths: []
    property string editorSection: "tone"
    property bool whiteBalancePicker: false
    readonly property bool keyboardOpen: typeof softKeyboard !== "undefined" && softKeyboard.isOpen
    readonly property real keyboardAvailableHeight: Math.max(160, height - (keyboardOpen ? 252 : 0))
    color: "#080808"
    signal closeRequested()
    signal toolsRequested()
    function closeOverlays() {
        var popups = [optionsPopup, ratingPopup, exportPopup, browserPopup, purgePopup, editPopup, historyPopup, infoPopup, comparePopup, tasksPopup]
        var closed = false
        for (var i = 0; i < popups.length; ++i) if (popups[i].visible) { popups[i].close(); closed = true }
        if (whiteBalancePicker) { whiteBalancePicker = false; closed = true }
        return closed
    }
    function wbGain(channel) { var edits = gallery ? gallery.edits : ({}); return edits.wb ? Number(edits.wb[channel]) : 1 }
    function finishText() { if (typeof softKeyboard !== "undefined") softKeyboard.done() }
    function curveValue(index) { var edits = gallery ? gallery.edits : ({}); return edits.curve && edits.curve.length === 5 ? Number(edits.curve[index][1]) : index / 4 }
    function beginWhiteBalance() { whiteBalancePicker = true; nativeZoom = false; compareMode = false; editPopup.close() }
    function sampleWhiteBalance(x, y) {
        var left = (fitImage.width - fitImage.paintedWidth) / 2, top = (fitImage.height - fitImage.paintedHeight) / 2
        if (x < left || y < top || x > left + fitImage.paintedWidth || y > top + fitImage.paintedHeight || fitImage.paintedWidth <= 0 || fitImage.paintedHeight <= 0) return
        gallery.pickWhiteBalance((x - left) / fitImage.paintedWidth, (y - top) / fitImage.paintedHeight)
        whiteBalancePicker = false; editorSection = "whiteBalance"; editPopup.open()
    }
    onVisibleChanged: {
        if (!visible) closeOverlays()
        else if (gallery) gallery.refresh()
    }
    function selectPhoto(photo) {
        if (!gallery) return
        if (gallery.multiSelect) { gallery.toggleSelection(photo); return }
        nativeZoom = false; compareMode = false; pixelZoom = 1; centerX = .5; centerY = .5
        gallery.selectPhoto(photo); viewer = true
    }
    function requestTiles() {
        if (!nativeZoom || !gallery || !gallery.reviewWidth || !gallery.reviewHeight) return
        var w = Math.min(gallery.reviewWidth, Math.ceil(leftPane.width / pixelZoom))
        var h = Math.min(gallery.reviewHeight, Math.ceil(imageStage.height / pixelZoom))
        var x = Math.max(0, Math.min(gallery.reviewWidth - w, Math.round(centerX * gallery.reviewWidth - w / 2)))
        var y = Math.max(0, Math.min(gallery.reviewHeight - h, Math.round(centerY * gallery.reviewHeight - h / 2)))
        gallery.loadTile(x, y, w, h)
        if (compareMode && gallery.compareWidth && gallery.compareHeight) {
            w = Math.min(gallery.compareWidth, Math.ceil(rightPane.width / pixelZoom)); h = Math.min(gallery.compareHeight, Math.ceil(imageStage.height / pixelZoom))
            x = Math.max(0, Math.min(gallery.compareWidth - w, Math.round(centerX * gallery.compareWidth - w / 2)))
            y = Math.max(0, Math.min(gallery.compareHeight - h, Math.round(centerY * gallery.compareHeight - h / 2)))
            gallery.loadCompareTile(x, y, w, h)
        }
    }
    function toggleZoom() { nativeZoom = !nativeZoom; pixelZoom = 1; centerX = .5; centerY = .5; requestTiles() }
    function openBrowser(mode) {
        fileMode = mode; chosenPaths = []
        if (gallery) gallery.browseDirectory(gallery.browsePath)
        browserPopup.open()
    }
    function choosePath(path) {
        var paths = chosenPaths.slice(0), index = paths.indexOf(path)
        if (index >= 0) paths.splice(index, 1); else paths.push(path)
        chosenPaths = paths
    }
    function comparePhoto(photo) {
        if (!gallery) return
        gallery.setCompare(photo); compareMode = true; nativeZoom = true; pixelZoom = 1
        centerX = .5; centerY = .5; comparePopup.close(); tileTimer.restart()
    }
    Timer { id: tileTimer; interval: 40; onTriggered: page.requestTiles() }
    Keys.onEscapePressed: { if (!closeOverlays()) { if (viewer) viewer = false; else closeRequested() } event.accepted = true }
    focus: visible

    ColumnLayout {
        anchors.fill: parent; spacing: 4
        RowLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 64; spacing: 4
            MediaButton { implicitWidth: page.viewer ? 96 : 68; text: page.viewer ? qsTr("照片墙") : qsTr("返回"); onClicked: { if (page.viewer) { page.viewer = false; page.compareMode = false } else page.closeRequested() } }
            Text { Layout.fillWidth: true; text: page.viewer ? (page.gallery && page.gallery.current.rating ? page.gallery.current.rating + qsTr(" 星 · ") : "") + (page.compareMode ? qsTr("同位置双图查焦") : qsTr("原始影像")) : qsTr("照片库 · ") + (page.gallery ? page.gallery.totalCount : 0); color: "white"; font.pixelSize: 20; elide: Text.ElideRight }
            MediaButton { objectName: "galleryMultiSelectButton"; implicitWidth: 78; text: page.gallery && page.gallery.multiSelect ? qsTr("退出多选") : qsTr("多选"); emphasized: page.gallery && page.gallery.multiSelect; visible: !page.viewer; onClicked: page.gallery.setMultiSelect(!page.gallery.multiSelect) }
            MediaButton { implicitWidth: 86; text: page.nativeZoom ? Math.round(page.pixelZoom * 100) + "%" : "100%"; visible: page.viewer; onClicked: page.toggleZoom() }
            MediaButton { implicitWidth: 68; text: qsTr("对比"); visible: page.viewer; onClicked: comparePopup.open() }
            MediaButton { implicitWidth: 68; text: qsTr("任务"); onClicked: tasksPopup.open() }
            MediaButton { objectName: "galleryOptionsButton"; implicitWidth: 68; text: qsTr("更多"); onClicked: optionsPopup.open() }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 64; visible: !page.viewer; spacing: 4
            Repeater { model: [{key:"all",name:"全部"},{key:"rated",name:"已评分"},{key:"protected",name:"已保护"},{key:"trash",name:"回收站"}]; MediaButton { Layout.fillWidth: true; text: qsTr(modelData.name); emphasized: page.gallery && page.gallery.filterName === modelData.key; onClicked: page.gallery.setFilter(modelData.key) } }
            LocalizedComboBox { Layout.preferredWidth: 126; Layout.preferredHeight: 64; model: ["最新在前","最早在前","评分排序"]; font.pixelSize: 17; onActivated: if (page.gallery) page.gallery.setSort(["newest","oldest","rating"][currentIndex]) }
        }
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 0
            GridView {
                id: wall
                objectName: "wholeLibraryThumbnailWall"
                anchors.fill: parent; visible: !page.viewer
                clip: true; cellWidth: Math.floor(width / Math.max(2, Math.floor(width / 220))); cellHeight: 154; cacheBuffer: 154
                model: page.gallery ? page.gallery.model : null
                delegate: Rectangle {
                    id: photoCell
                    objectName: "galleryPhotoCell" + index
                    property var photo: model.item
                    width: wall.cellWidth - 6; height: wall.cellHeight - 6
                    color: "#151515"; border.width: photo.selected ? 2 : 1; border.color: photo.selected ? "#ffffff" : "#3b3b3b"; radius: 4
                    Image { anchors { top: parent.top; left: parent.left; right: parent.right; bottom: caption.top; margins: 3 } source: page.visible ? photo.thumbUrl || "" : ""; fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false }
                    Text { anchors.centerIn: parent; visible: !photo.thumbUrl; text: qsTr("正在读取"); color: "#777777"; font.pixelSize: 14 }
                    Rectangle { visible: photo.selected; width: 32; height: 32; anchors { top: parent.top; right: parent.right; margins: 4 } color: "#dddddd"; radius: 3; Text { anchors.centerIn: parent; text: "✓"; color: "#111111"; font.pixelSize: 22 } }
                    Column {
                        id: caption; anchors { bottom: parent.bottom; left: parent.left; right: parent.right; margins: 6 } height: 38; spacing: 2
                        Text { width: parent.width; text: new Date(Number(photo.timestamp || 0) * 1000).toLocaleString(Qt.locale(), "MM-dd hh:mm:ss") + (photo.origin_kind === "import" ? qsTr(" · 导入") : ""); color: "#dddddd"; font.pixelSize: 14; elide: Text.ElideRight }
                        Text { width: parent.width; text: (photo.rating ? photo.rating + "★  " : "") + (photo.protected ? qsTr("已保护  ") : "") + photo.width + "×" + photo.height; color: "#aaaaaa"; font.pixelSize: 13; elide: Text.ElideRight }
                    }
                    MouseArea { anchors.fill: parent; onClicked: page.selectPhoto(photoCell.photo.photo_uuid); onPressAndHold: { page.gallery.setMultiSelect(true); page.gallery.toggleSelection(photoCell.photo.photo_uuid) } }
                    Component.onCompleted: if (page.visible && page.gallery) page.gallery.requestThumbnail(photo.photo_uuid)
                }
                Text { anchors.centerIn: parent; width: parent.width - 30; visible: wall.count === 0; text: page.gallery && page.gallery.available ? qsTr("此筛选暂无照片。可复制导入文件，或切换筛选。") : qsTr("正在连接并索引资料库…"); color: "#aaaaaa"; font.pixelSize: 20; wrapMode: Text.WordWrap; horizontalAlignment: Text.AlignHCenter }
            }
            Item {
                id: imageStage
                anchors.fill: parent; visible: page.viewer; clip: true
                Item {
                    id: leftPane; anchors { top: parent.top; bottom: parent.bottom; left: parent.left } width: page.compareMode ? parent.width / 2 : parent.width; clip: true
                    Image { id: fitImage; anchors.fill: parent; visible: !page.nativeZoom; source: page.gallery ? page.gallery.reviewUrl : ""; fillMode: Image.PreserveAspectFit; asynchronous: true; cache: false }
                    Image { visible: page.nativeZoom; x: (parent.width - width) / 2 + page.panX; y: (parent.height - height) / 2 + page.panY; width: page.gallery ? page.gallery.tileWidth * page.pixelZoom : 0; height: page.gallery ? page.gallery.tileHeight * page.pixelZoom : 0; source: page.nativeZoom && page.gallery ? page.gallery.tileUrl : ""; smooth: false; asynchronous: true; cache: false }
                }
                Item {
                    id: rightPane; anchors { top: parent.top; bottom: parent.bottom; right: parent.right } width: parent.width / 2 - 1; visible: page.compareMode; clip: true
                    Rectangle { anchors.fill: parent; color: "#111111" }
                    Image { anchors.centerIn: parent; width: sourceSize.width * page.pixelZoom; height: sourceSize.height * page.pixelZoom; source: page.gallery ? page.gallery.compareUrl : ""; smooth: false; asynchronous: true; cache: false }
                }
                PinchArea {
                    anchors.fill: parent
                    property real startZoom: 1
                    onPinchStarted: startZoom = page.pixelZoom
                    onPinchUpdated: { if (!page.whiteBalancePicker) { page.nativeZoom = true; page.pixelZoom = Math.max(1, Math.min(8, startZoom * pinch.scale)) } }
                    onPinchFinished: page.requestTiles()
                    MouseArea {
                        anchors.fill: parent
                        property real startX: 0
                        property real startY: 0
                        onPressed: { startX = mouse.x; startY = mouse.y }
                        onClicked: if (page.whiteBalancePicker) page.sampleWhiteBalance(mouse.x, mouse.y)
                        onPositionChanged: if (pressed && page.nativeZoom) { page.panX = mouse.x - startX; page.panY = mouse.y - startY }
                        onReleased: {
                            if (page.nativeZoom && page.gallery) {
                                page.centerX = Math.max(0, Math.min(1, page.centerX - page.panX / page.pixelZoom / page.gallery.reviewWidth))
                                page.centerY = Math.max(0, Math.min(1, page.centerY - page.panY / page.pixelZoom / page.gallery.reviewHeight))
                                page.panX = 0; page.panY = 0; page.requestTiles()
                            }
                        }
                        onDoubleClicked: if (!page.whiteBalancePicker) page.toggleZoom()
                    }
                }
                Rectangle { anchors { left: parent.left; right: parent.right; top: parent.top } height: 46; visible: page.whiteBalancePicker; color: "#111111"; Text { anchors.centerIn: parent; text: qsTr("轻触画面中的中性灰 / 白区域取样 · 返回键取消"); color: "white"; font.pixelSize: 18 } }
                Text { anchors { left: parent.left; top: parent.top; margins: 8 } visible: !page.whiteBalancePicker; text: page.nativeZoom ? qsTr("原始像素 · 拖动联动位置") : qsTr("未标定显示预览 · 原片保留"); color: "#dddddd"; font.pixelSize: 13; style: Text.Outline; styleColor: "black" }
            }
        }
        RowLayout {
            Layout.fillWidth: true; Layout.preferredHeight: 64; spacing: 4
            Text { Layout.preferredWidth: 90; text: page.gallery && page.gallery.selectionCount ? qsTr("已选 ") + page.gallery.selectionCount : page.viewer ? qsTr("当前照片") : qsTr("未多选"); color: "#cccccc"; font.pixelSize: 17 }
            MediaButton { objectName:"galleryEditOrSelectButton"; Layout.fillWidth: true; text: page.viewer ? qsTr("编辑") : qsTr("全选"); onClicked: { if (page.viewer) { page.nativeZoom = false; editPopup.open() } else page.gallery.selectAll() } }
            MediaButton { Layout.fillWidth: true; text: qsTr("评分"); enabled: !!page.gallery && !page.gallery.busy; onClicked: ratingPopup.open() }
            MediaButton { objectName:"galleryExportButton"; Layout.fillWidth: true; text: qsTr("导出"); onClicked: exportPopup.open() }
            MediaButton { Layout.fillWidth: true; text: page.viewer ? qsTr("信息") : qsTr("取消选择"); onClicked: { if (page.viewer) infoPopup.open(); else page.gallery.clearSelection() } }
            MediaButton { Layout.fillWidth: true; text: qsTr("下一页"); visible: !page.viewer; enabled: page.gallery && page.gallery.nextCursor.length > 0 && !page.gallery.busy; onClicked: page.gallery.nextPage() }
            MediaButton { Layout.fillWidth: true; text: qsTr("图像工具"); visible: page.viewer; enabled: !!page.gallery && page.gallery.selectionCount <= 16; onClicked: page.toolsRequested() }
        }
        Text { Layout.fillWidth: true; Layout.preferredHeight: Math.min(36, implicitHeight); text: page.gallery ? (typeof i18n !== "undefined" ? i18n.t(page.gallery.message, i18n.revision) : page.gallery.message) : qsTr("资料库不可用"); color: "#999999"; font.pixelSize: 13; elide: Text.ElideRight; maximumLineCount: 1 }
    }

    Popup {
        id: optionsPopup
        parent: page; x: 12; y: 12; width: page.width - 24; height: page.keyboardAvailableHeight - 24; padding: 12; modal: true; focus: true
        onClosed: if(search.activeFocus)page.finishText()
        background: Rectangle { color: "#141414"; border.color: "#666666"; radius: 6 }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; visible:!page.keyboardOpen; Text { text: qsTr("资料库操作"); color: "white"; font.pixelSize: 22; Layout.fillWidth: true } MediaButton { text: qsTr("关闭"); onClicked: optionsPopup.close() } }
            GridLayout {
                columns: 3; Layout.fillWidth: true; rowSpacing: 8; columnSpacing: 8; visible:!page.keyboardOpen
                MediaButton { Layout.fillWidth: true; text: qsTr("复制导入"); onClicked: { optionsPopup.close(); page.openBrowser("import") } }
                MediaButton { Layout.fillWidth: true; text: qsTr("保护选择"); enabled: !!page.gallery && !page.gallery.busy && page.gallery.filterName !== "trash"; onClicked: { page.gallery.setProtected(true); optionsPopup.close() } }
                MediaButton { Layout.fillWidth: true; text: qsTr("取消保护"); enabled: !!page.gallery && !page.gallery.busy; onClicked: { page.gallery.setProtected(false); optionsPopup.close() } }
                MediaButton { Layout.fillWidth: true; text: qsTr("移入回收站"); enabled: !!page.gallery && !page.gallery.busy; onClicked: { page.gallery.trash(); optionsPopup.close() } }
                MediaButton { Layout.fillWidth: true; text: qsTr("恢复选择"); enabled: !!page.gallery && !page.gallery.busy && page.gallery.filterName === "trash"; onClicked: { page.gallery.restore(); optionsPopup.close() } }
                MediaButton { Layout.fillWidth: true; text: qsTr("永久清空"); enabled: !!page.gallery && page.gallery.filterName === "trash"; onClicked: { optionsPopup.close(); purgePopup.open() } }
                MediaButton { Layout.fillWidth: true; text: qsTr("编辑历史"); enabled: !!page.gallery && page.gallery.currentId.length > 0; onClicked: { optionsPopup.close(); historyPopup.open() } }
                MediaButton { Layout.fillWidth: true; text: qsTr("最新 / 重扫"); onClicked: { page.gallery.refresh(); optionsPopup.close() } }
                MediaButton { Layout.fillWidth: true; text: qsTr("图像工具"); enabled: !!page.gallery && page.gallery.selectedSources.length > 0; onClicked: { optionsPopup.close(); page.toolsRequested() } }
            }
            RowLayout {
                Layout.fillWidth: true
                TextField { id: search; Layout.fillWidth: true; Layout.preferredHeight: 64; Layout.minimumHeight:64; placeholderText: qsTr("按文件名搜索全库"); selectByMouse: true; font.pixelSize: 18; onAccepted:{page.gallery.setSearch(text);page.finishText();optionsPopup.close()} }
                MediaButton { text: qsTr("搜索"); onClicked: { page.gallery.setSearch(search.text);page.finishText();optionsPopup.close() } }
            }
            Text { Layout.fillWidth: true; visible:!page.keyboardOpen; text: qsTr("筛选、全选与批量操作覆盖整个已索引资料库。导出固定当前版本；源 RAW 和拍摄请求历史保留。多图计算最多选择 16 张。"); color: "#aaaaaa"; font.pixelSize: 15; wrapMode: Text.WordWrap }
            Item { Layout.fillHeight: true }
        }
    }
    Popup {
        id: ratingPopup
        parent: page; x: 16; y: (page.height - height) / 2; width: page.width - 32; height: 168; padding: 12; modal: true; focus: true
        background: Rectangle { color: "#151515"; border.color: "#666666"; radius: 6 }
        contentItem: ColumnLayout {
            Text { text: qsTr("为已选照片评分"); color: "white"; font.pixelSize: 22 }
            RowLayout { Layout.fillWidth: true; Repeater { model: 6; MediaButton { Layout.fillWidth: true; text: index ? index + "★" : qsTr("无"); onClicked: { page.gallery.setRating(index); ratingPopup.close() } } } }
        }
    }
    Popup {
        id: exportPopup
        parent: page; x: 12; y: 12; width: page.width - 24; height: page.keyboardAvailableHeight - 24; padding: 12; modal: true; focus: true
        onClosed:if(exportDirectory.activeFocus)page.finishText()
        background: Rectangle { color: "#151515"; border.color: "#666666"; radius: 6 }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; visible:!page.keyboardOpen; Text { text: qsTr("批量导出"); color: "white"; font.pixelSize: 22; Layout.fillWidth: true } MediaButton { text: qsTr("关闭"); onClicked: exportPopup.close() } }
            RowLayout { Layout.fillWidth: true; visible:!page.keyboardOpen; Repeater { model: [{key:"original",name:"原件 + 旁车"},{key:"jpeg",name:"原尺寸 JPEG"},{key:"tiff",name:"16 位 TIFF"}]; MediaButton { Layout.fillWidth: true; text: qsTr(modelData.name); emphasized: page.exportFormat === modelData.key; onClicked: page.exportFormat = modelData.key } } }
            Text { Layout.fillWidth: true; visible:!page.keyboardOpen; text: qsTr("导出冻结选择的编辑版本。JPEG/TIFF 是未标定派生影像；原件不会改变。已有同名文件不会覆盖或计为成功。"); color: "#bbbbbb"; font.pixelSize: 17; wrapMode: Text.WordWrap }
            RowLayout { Layout.fillWidth: true; TextField { id: exportDirectory; objectName:"galleryExportDirectory"; Layout.fillWidth: true; Layout.preferredHeight: 64; Layout.minimumHeight:64; placeholderText: qsTr("选择已有导出目录"); selectByMouse: true; font.pixelSize: 18; onAccepted:page.finishText() } MediaButton { text: qsTr("浏览"); onClicked: {page.finishText();page.openBrowser("destination")} } }
            Item { Layout.fillHeight: true }
            MediaButton { Layout.fillWidth: true; text: qsTr("创建后台导出任务"); emphasized: true; enabled: exportDirectory.text.length > 0; onClicked: {page.finishText();page.gallery.exportSelection(page.exportFormat, exportDirectory.text); exportPopup.close(); tasksPopup.open() } }
        }
    }
    Popup {
        id: browserPopup
        parent: page; x: 8; y: 8; width: page.width - 16; height: page.height - 16; padding: 10; modal: true; focus: true
        background: Rectangle { color: "#111111"; border.color: "#666666"; radius: 6 }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; MediaButton { text: qsTr("上一级"); onClicked: page.gallery.browseUp() } Text { Layout.fillWidth: true; text: page.gallery ? page.gallery.browsePath : ""; color: "#dddddd"; font.pixelSize: 16; elide: Text.ElideLeft } MediaButton { text: qsTr("关闭"); onClicked: browserPopup.close() } }
            ListView {
                id: browserList; Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4; model: page.gallery ? page.gallery.browseEntries : []
                delegate: MediaButton { width: browserList.width; height: 64; text: (modelData.isDirectory ? qsTr("目录  ") : page.chosenPaths.indexOf(modelData.path) >= 0 ? "✓  " : qsTr("文件  ")) + modelData.name; emphasized: page.chosenPaths.indexOf(modelData.path) >= 0; enabled: modelData.isDirectory || page.fileMode === "import"; onClicked: { if (modelData.isDirectory) page.gallery.browseDirectory(modelData.path); else page.choosePath(modelData.path) } }
            }
            Text { text: page.fileMode === "import" ? qsTr("支持 DNG/JPEG/PNG/TIFF，复制到资料库；源文件保留。已选 ") + page.chosenPaths.length + qsTr(" 个文件。") : qsTr("选择当前目录作为导出位置。"); color: "#aaaaaa"; font.pixelSize: 14; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            MediaButton { Layout.fillWidth: true; text: page.fileMode === "import" ? qsTr("复制导入选择") : qsTr("使用此目录"); enabled: page.fileMode !== "import" || page.chosenPaths.length > 0; onClicked: { if (page.fileMode === "import") { page.gallery.importPaths(page.chosenPaths.join("\n")); tasksPopup.open() } else exportDirectory.text = page.gallery.browsePath; browserPopup.close() } }
        }
    }
    Popup {
        id: purgePopup
        parent: page; x: 24; y: 40; width: page.width - 48; height: page.height - 80; padding: 16; modal: true; focus: true
        background: Rectangle { color: "#151515"; border.color: "#aaaaaa"; radius: 6 }
        contentItem: ColumnLayout {
            Text { text: qsTr("永久清空回收站？"); color: "white"; font.pixelSize: 24 }
            Text { Layout.fillWidth: true; text: qsTr("此资料库回收站中的原始文件和导入副本将永久删除，无法撤销。拍摄结果、编辑和任务历史仍保留。受保护或正在使用的资产会跳过。"); color: "#cccccc"; font.pixelSize: 18; wrapMode: Text.WordWrap }
            Item { Layout.fillHeight: true }
            RowLayout { Layout.fillWidth: true; MediaButton { Layout.fillWidth: true; text: qsTr("保留文件"); onClicked: purgePopup.close() } MediaButton { Layout.fillWidth: true; text: qsTr("确认永久清空"); emphasized: true; onClicked: { page.gallery.emptyTrash("PERMANENTLY_DELETE"); purgePopup.close(); tasksPopup.open() } } }
        }
    }
    Popup {
        id: editPopup
        parent: page; x: Math.max(0, page.width - width); y: 0; width: Math.min(360, page.width); height: page.keyboardAvailableHeight; padding: 12; modal: false; focus: true
        onClosed:if(styleName.activeFocus)page.finishText()
        background: Rectangle { color: "#151515"; border.color: "#666666" }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; visible:!page.keyboardOpen; Text { text: qsTr("非破坏编辑"); color: "white"; font.pixelSize: 21; Layout.fillWidth: true } MediaButton { objectName:"galleryEditDone"; implicitWidth: 68; text: qsTr("完成"); onClicked: editPopup.close() } }
            RowLayout {
                Layout.fillWidth: true; spacing: 4; visible:!page.keyboardOpen
                Repeater { model: [{key:"tone",name:"明暗"},{key:"whiteBalance",name:"白平衡"},{key:"curve",name:"曲线"},{key:"styles",name:"风格"}]; MediaButton { objectName:"galleryEditorTab"+modelData.key; Layout.fillWidth: true; implicitWidth: 70; text: qsTr(modelData.name); emphasized: page.editorSection === modelData.key; onClicked: page.editorSection = modelData.key } }
            }
            ScrollView {
                id: editScroll; Layout.fillWidth: true; Layout.fillHeight: true; contentWidth: availableWidth; clip: true
                ColumnLayout {
                    width: editScroll.availableWidth; spacing: 8
                    Repeater { model: page.editorSection === "tone" ? [{key:"exposure",name:"曝光 EV",min:-5,max:5,step:.1,initial:0},{key:"contrast",name:"对比度",min:.5,max:2,step:.05,initial:1},{key:"saturation",name:"饱和度",min:0,max:2,step:.05,initial:1},{key:"noise",name:"降噪",min:0,max:1,step:.05,initial:0},{key:"sharpen",name:"锐化",min:0,max:3,step:.1,initial:0}] : []
                        ColumnLayout { Layout.fillWidth: true; Text { text: qsTr(modelData.name) + "  " + Number(page.gallery && page.gallery.edits[modelData.key] !== undefined ? page.gallery.edits[modelData.key] : modelData.initial).toFixed(2); color: "#dddddd"; font.pixelSize: 18 } Slider { Layout.fillWidth: true; Layout.preferredHeight: 64; from: modelData.min; to: modelData.max; stepSize: modelData.step; value: page.gallery && page.gallery.edits[modelData.key] !== undefined ? page.gallery.edits[modelData.key] : modelData.initial; onMoved: page.gallery.setEdit(modelData.key, value) } }
                    }
                    MediaButton { Layout.fillWidth: true; visible: page.editorSection === "tone"; text: qsTr("顺时针旋转 90°"); onClicked: page.gallery.rotate() }
                    RowLayout { Layout.fillWidth: true; visible: page.editorSection === "tone"; Repeater { model: ["original","1:1","3:2","4:3"]; MediaButton { Layout.fillWidth: true; implicitWidth: 60; text: modelData === "original" ? qsTr("原比例") : qsTr(modelData); onClicked: page.gallery.setCrop(modelData) } } }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: page.editorSection === "whiteBalance"
                        MediaButton { objectName: "galleryWhiteBalancePicker"; Layout.fillWidth: true; text: qsTr("吸管：轻触画面灰点"); onClicked: page.beginWhiteBalance() }
                        Text { Layout.fillWidth: true; text: qsTr("取原始区域线性 RGB 均值，中性点推算 R/G/B 增益。尚未做相机色彩标定。"); color: "#bbbbbb"; font.pixelSize: 17; wrapMode: Text.WordWrap }
                        Repeater { model: ["红 R","绿 G","蓝 B"]; ColumnLayout { Layout.fillWidth: true; Text { text: qsTr(modelData) + "  ×" + page.wbGain(index).toFixed(3); color: "white"; font.pixelSize: 18 } Slider { objectName: "galleryWbGain" + index; Layout.fillWidth: true; Layout.preferredHeight: 64; from: .1; to: 8; stepSize: .01; value: page.wbGain(index); onMoved: page.gallery.setWhiteBalanceChannel(index, value) } } }
                        MediaButton { Layout.fillWidth: true; text: qsTr("重置 RGB 增益"); onClicked: { page.gallery.setWhiteBalanceChannel(0,1); page.gallery.setWhiteBalanceChannel(1,1); page.gallery.setWhiteBalanceChannel(2,1) } }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: page.editorSection === "curve"
                        Canvas {
                            id: curveGraph; objectName: "galleryCurveGraph"; Layout.fillWidth: true; Layout.preferredHeight: 100
                            property var points: page.gallery && page.gallery.edits.curve && page.gallery.edits.curve.length === 5 ? page.gallery.edits.curve : [[0,0],[.25,.25],[.5,.5],[.75,.75],[1,1]]
                            onPointsChanged: requestPaint()
                            onWidthChanged: requestPaint()
                            onPaint: { var ctx = getContext("2d"); ctx.fillStyle = "#080808"; ctx.fillRect(0,0,width,height); ctx.strokeStyle="#444444"; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(0,height); ctx.lineTo(width,0); ctx.stroke(); ctx.strokeStyle="white"; ctx.lineWidth=2; ctx.beginPath(); for (var i=0;i<points.length;++i) { var x=points[i][0]*width, y=(1-points[i][1])*height; if (i===0)ctx.moveTo(x,y);else ctx.lineTo(x,y) } ctx.stroke() }
                        }
                        Text { Layout.fillWidth: true; text: qsTr("固定黑白端点；三个中间节点自动保持单调，避免反转明暗。"); color: "#bbbbbb"; font.pixelSize: 17; wrapMode: Text.WordWrap }
                        Repeater { model: ["暗部 25%","中调 50%","亮部 75%"]; ColumnLayout { Layout.fillWidth: true; Text { text: qsTr(modelData) + " → " + Math.round(page.curveValue(index+1)*100) + "%"; color:"white"; font.pixelSize:18 } Slider { objectName: "galleryCurvePoint" + (index+1); Layout.fillWidth:true; Layout.preferredHeight:64; from:0; to:1; stepSize:.01; value:page.curveValue(index+1); onMoved:page.gallery.setCurvePoint(index+1,value) } } }
                        MediaButton { Layout.fillWidth: true; text: qsTr("重置直线曲线"); onClicked: page.gallery.resetCurve() }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; visible: page.editorSection === "styles"
                        Text { Layout.fillWidth: true; visible:!page.keyboardOpen; text: page.gallery && page.gallery.selectionCount ? qsTr("应用到 ") + page.gallery.selectionCount + qsTr(" 张冻结选择；各自裁切和旋转保留。") : qsTr("应用到当前照片；裁切和旋转保留。"); color: "#bbbbbb"; font.pixelSize: 17; wrapMode: Text.WordWrap }
                        TextField { id: styleName; objectName: "galleryStyleName"; Layout.fillWidth:true; Layout.preferredHeight:64; Layout.minimumHeight:64; placeholderText:qsTr("自定义风格名称"); maximumLength:80; font.pixelSize:18; selectByMouse:true; onAccepted:page.finishText(); onActiveFocusChanged:if(activeFocus)Qt.callLater(function(){editScroll.contentItem.contentY=0}) }
                        MediaButton { objectName:"gallerySaveStyle"; Layout.fillWidth:true; text:qsTr("将当前编辑保存为风格"); enabled:styleName.text.trim().length>0; onClicked:{page.finishText();page.gallery.saveStyle(styleName.text.trim());styleName.text="" } }
                        Repeater { model: !page.keyboardOpen && page.gallery ? page.gallery.styles : []; ColumnLayout { Layout.fillWidth:true; spacing:4; Text { Layout.fillWidth:true; text:(modelData.builtin ? qsTr(modelData.name) : modelData.name) + (modelData.builtin ? qsTr(" · 内置") : qsTr(" · 自定义"));color:"white";font.pixelSize:18;elide:Text.ElideRight } RowLayout { Layout.fillWidth:true; MediaButton { Layout.fillWidth:true;text:qsTr("应用风格");onClicked:page.gallery.applyStyle(modelData.id) } MediaButton { Layout.fillWidth:true;text:qsTr("删除风格");visible:!modelData.builtin;onClicked:page.gallery.deleteStyle(modelData.id) } } } }
                    }
                    Text { Layout.fillWidth: true; visible:!page.keyboardOpen; text: qsTr("参数和历史单独保存。原始像素不变；显示与导出均未做硬件色彩标定。"); color: "#aaaaaa"; font.pixelSize: 14; wrapMode: Text.WordWrap }
                }
            }
            RowLayout { Layout.fillWidth: true; visible:!page.keyboardOpen; MediaButton { Layout.fillWidth: true; text: qsTr("撤销"); onClicked: page.gallery.undo() } MediaButton { Layout.fillWidth: true; text: qsTr("重做"); onClicked: page.gallery.redo() } MediaButton { Layout.fillWidth: true; text: qsTr("历史"); onClicked: historyPopup.open() } }
        }
    }
    Popup {
        id: historyPopup
        parent: page; x: 12; y: 12; width: page.width - 24; height: page.height - 24; padding: 12; modal: true; focus: true
        background: Rectangle { color: "#141414"; border.color: "#666666"; radius: 6 }
        contentItem: ColumnLayout {
            RowLayout { Layout.fillWidth: true; Text { text: qsTr("不可变编辑历史"); color: "white"; font.pixelSize: 22; Layout.fillWidth: true } MediaButton { text: qsTr("关闭"); onClicked: historyPopup.close() } }
            ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4; model: page.gallery ? page.gallery.editHistory : []; delegate: MediaButton { width: ListView.view.width; height: 72; emphasized: page.gallery.current.edit_revision === modelData.revision; text: qsTr("版本 ") + modelData.revision + " · " + new Date(Number(modelData.created) * 1000).toLocaleString(); onClicked: page.gallery.selectHistory(modelData.revision) } }
            RowLayout { Layout.fillWidth: true; MediaButton { Layout.fillWidth:true; text:qsTr("最新历史"); onClicked:page.gallery.historyPage(-1) } MediaButton { Layout.fillWidth:true; text:qsTr("更早的 100 条"); enabled:page.gallery && page.gallery.editHistory.length === 100; onClicked:page.gallery.historyPage(page.gallery.editHistory[page.gallery.editHistory.length-1].revision) } }
        }
    }
    Popup {
        id: infoPopup
        parent: page; x: 12; y: 12; width: page.width - 24; height: page.height - 24; padding: 12; modal: true; focus: true
        background: Rectangle { color: "#141414"; border.color: "#666666"; radius: 6 }
        contentItem: ColumnLayout { RowLayout { Layout.fillWidth: true; Text { text: qsTr("资产与拍摄档案"); color: "white"; font.pixelSize: 22; Layout.fillWidth: true } MediaButton { text: qsTr("关闭"); onClicked: infoPopup.close() } } ScrollView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; TextArea { text: page.gallery ? page.gallery.archiveText : ""; readOnly: true; selectByMouse: true; textFormat: TextEdit.PlainText; wrapMode: TextEdit.WrapAnywhere; color: "#cccccc"; font.pixelSize: 15; background: null } } }
    }
    Popup {
        id: comparePopup
        parent: page; x: 16; y: 16; width: page.width - 32; height: page.height - 32; padding: 12; modal: true; focus: true
        background: Rectangle { color: "#141414"; border.color: "#666666"; radius: 6 }
        contentItem: ColumnLayout { RowLayout { Layout.fillWidth: true; Text { text: qsTr("选择当前页另一张进行同位置查焦"); color: "white"; font.pixelSize: 20; Layout.fillWidth: true } MediaButton { text: qsTr("关闭"); onClicked: comparePopup.close() } } ListView { Layout.fillWidth: true; Layout.fillHeight: true; clip: true; spacing: 4; model: page.gallery ? page.gallery.items : []; delegate: MediaButton { width: ListView.view.width; height: 64; enabled: modelData.photo_uuid !== page.gallery.currentId; text: modelData.filename + " · " + modelData.width + "×" + modelData.height; onClicked: page.comparePhoto(modelData.photo_uuid) } } }
    }
    Popup {
        id: tasksPopup
        parent: page; x: 0; y: 0; width: page.width; height: page.height; padding: 0; modal: true; focus: true
        background: Rectangle { color: "#111111" }
        contentItem: TasksPage { taskService: page.taskService; onCloseRequested: tasksPopup.close() }
    }
}
