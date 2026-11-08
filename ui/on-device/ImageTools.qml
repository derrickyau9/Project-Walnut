import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Rectangle {
    id:tools
    objectName:"imageToolsWorkspace"
    color:"#080808"
    property var processing:null
    property var selectedSources:[]
    property string selectionLimitReason:""
    property var analysis:({})
    property string exportDirectory:""
    signal closeRequested()
    signal jobRequested(string kind,var params,string destination)
    readonly property string uiFamily:typeof cameraUIFont!=="undefined" ? cameraUIFont : "Sans Serif"
    readonly property var operations:[
        {id:"develop_jpeg",label:"原尺寸 JPEG",minimum:1},
        {id:"develop_tiff16",label:"16 位 TIFF",minimum:1},
        {id:"analyze",label:"图像分析与辅助",minimum:1},
        {id:"hdr",label:"HDR 辐射融合",minimum:2},
        {id:"exposure_fusion",label:"曝光融合",minimum:2},
        {id:"focus_stack",label:"焦点堆栈",minimum:2},
        {id:"denoise_stack",label:"多帧降噪",minimum:2},
        {id:"panorama",label:"全景拼接",minimum:2},
        {id:"astro_stack",label:"星空对齐堆栈",minimum:2},
        {id:"darkflat",label:"暗场 / 平场校正",minimum:2},
        {id:"negative",label:"负片反转",minimum:1}
    ]
    function parameters() {
        var result={exposure_ev:ev.value,temperature:temperature.value,tint:tint.value,
            contrast:contrast.value,saturation:saturation.value,noise:noise.value,
            sharpen:sharpen.value,rotation:rotation.currentIndex*90,quality:95,
            align:align.checked,sigma_clip:true,linear_tiff:true,
            sources:selectedSources,overlays:["zebra","falsecolor","peaking"]}
        if(curve.currentIndex===1)result.curve=[[0,0],[.15,.1],[.5,.5],[.85,.9],[1,1]]
        else if(curve.currentIndex===2)result.curve=[[0,0],[.1,.16],[.5,.5],[.9,.84],[1,1]]
        if(crop.currentIndex>0 && selectedSources.length){var source=selectedSources[0],w=source.display_width||source.width||(source.raw||{}).width||3,h=source.display_height||source.height||(source.raw||{}).height||2;var target=[0,1,1.5,4/3,16/9][crop.currentIndex],cw=Math.min(1,target/(w/h)),ch=Math.min(1,(w/h)/target);result.crop=[(1-cw)/2,(1-ch)/2,cw,ch]}
        return result
    }
    function submit() {
        var kind=operations[operation.currentIndex].id,p=parameters(),file=destination.text.trim()
        if(processing && typeof processing.submitJob==="function")processing.submitJob(kind,JSON.stringify(p),file)
        else tools.jobRequested(kind,p,file)
    }
    function updateSuffix() {
        if (!destination.text) return
        var suffix=operation.currentIndex===1 ? ".tiff" : ".jpg"
        destination.text=destination.text.replace(/\.(jpg|jpeg|tif|tiff)$/i,suffix)
    }
    component Action:Button {
        focusPolicy:Qt.NoFocus
        implicitHeight:64
        background:Rectangle {radius:4;color:parent.down?"#666666":"#292929";border.color:"#555555"}
        contentItem:Text {text:parent.text;color:parent.enabled?"#ffffff":"#777777";font.family:tools.uiFamily;font.pixelSize:16;horizontalAlignment:Text.AlignHCenter;verticalAlignment:Text.AlignVCenter}
    }
    ColumnLayout {
        anchors.fill:parent
        anchors.margins:12
        spacing:8
        RowLayout {
            Layout.fillWidth:true
            Layout.preferredHeight:64
            Action {text:qsTranslate("ImageTools", "返回");Layout.preferredWidth:76;onClicked:tools.closeRequested()}
            Text {text:qsTranslate("ImageTools", "影像工坊");color:"#ffffff";font.family:tools.uiFamily;font.weight:Font.Medium;font.pixelSize:24;Layout.fillWidth:true}
            Text {text:tools.selectedSources.length+qsTranslate("ImageTools", " 张已选");color:"#bbbbbb";font.family:tools.uiFamily;font.pixelSize:14}
        }
        ScrollView {
            Layout.fillWidth:true
            Layout.fillHeight:true
            clip:true
            contentWidth:availableWidth
            ColumnLayout {
                width:parent.width
                spacing:10
                Text {visible:tools.selectionLimitReason.length>0;Layout.fillWidth:true;text:tools.selectionLimitReason;wrapMode:Text.WordWrap;color:"#ffffff";font.family:tools.uiFamily;font.pixelSize:16}
                RowLayout {
                    Layout.fillWidth:true
                    Text {text:qsTranslate("ImageTools", "处理方式");color:"#cccccc";font.family:tools.uiFamily;font.pixelSize:16;Layout.preferredWidth:104}
                    LocalizedComboBox {font.family:tools.uiFamily;font.pixelSize:16;id:operation;objectName:"imageToolsOperation";Layout.fillWidth:true;Layout.preferredHeight:64;model:tools.operations;textRole:"label";onActivated:tools.updateSuffix()}
                }
                Text {Layout.fillWidth:true;text:operation.currentIndex===9?qsTranslate("ImageTools", "按顺序选择：照片、暗场、可选平场。校正发生在 RGB 显影域，原始数据保留。"):operation.currentIndex===7?qsTranslate("ImageTools", "选择有重叠与纹理的照片；匹配不足会停止，不输出错误拼接。"):operation.currentIndex===8?qsTranslate("ImageTools", "对齐星点位移后进行多帧统计合成；不声称补偿未知光学畸变。"):qsTranslate("ImageTools", "处理读取已验证原件，结果另存为派生文件；不触发拍摄，也不覆盖 RAW。");wrapMode:Text.WordWrap;color:"#aaaaaa";font.family:tools.uiFamily;font.pixelSize:13}
                RowLayout {Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "曝光");color:"white";Layout.preferredWidth:104} Slider {Layout.minimumHeight:64;id:ev;from:-5;to:5;stepSize:.1;value:0;Layout.fillWidth:true} Text {font.family:tools.uiFamily;font.pixelSize:16;text:ev.value.toFixed(1)+qsTranslate("ImageTools", " EV");color:"white";Layout.preferredWidth:72}}
                RowLayout {Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "白平衡色温");color:"white";Layout.preferredWidth:104} Slider {Layout.minimumHeight:64;id:temperature;from:2000;to:12000;stepSize:100;value:6500;Layout.fillWidth:true} Text {font.family:tools.uiFamily;font.pixelSize:16;text:Math.round(temperature.value)+" K";color:"white";Layout.preferredWidth:72}}
                RowLayout {Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "色调");color:"white";Layout.preferredWidth:104} Slider {Layout.minimumHeight:64;id:tint;from:-1;to:1;stepSize:.05;value:0;Layout.fillWidth:true} Text {font.family:tools.uiFamily;font.pixelSize:16;text:tint.value.toFixed(2);color:"white";Layout.preferredWidth:72}}
                RowLayout {Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "对比度");color:"white";Layout.preferredWidth:104} Slider {Layout.minimumHeight:64;id:contrast;from:.5;to:2;stepSize:.05;value:1;Layout.fillWidth:true} Text {font.family:tools.uiFamily;font.pixelSize:16;text:contrast.value.toFixed(2);color:"white";Layout.preferredWidth:72}}
                RowLayout {Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "饱和度");color:"white";Layout.preferredWidth:104} Slider {Layout.minimumHeight:64;id:saturation;from:0;to:2;stepSize:.05;value:1;Layout.fillWidth:true} Text {font.family:tools.uiFamily;font.pixelSize:16;text:saturation.value.toFixed(2);color:"white";Layout.preferredWidth:72}}
                RowLayout {Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "降噪");color:"white";Layout.preferredWidth:104} Slider {Layout.minimumHeight:64;id:noise;from:0;to:1;stepSize:.05;value:0;Layout.fillWidth:true} Text {font.family:tools.uiFamily;font.pixelSize:16;text:noise.value.toFixed(2);color:"white";Layout.preferredWidth:72}}
                RowLayout {Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "锐化");color:"white";Layout.preferredWidth:104} Slider {Layout.minimumHeight:64;id:sharpen;from:0;to:3;stepSize:.1;value:0;Layout.fillWidth:true} Text {font.family:tools.uiFamily;font.pixelSize:16;text:sharpen.value.toFixed(1);color:"white";Layout.preferredWidth:72}}
                RowLayout {
                    Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "曲线 / 方向");color:"white";Layout.preferredWidth:104}
                    LocalizedComboBox {font.family:tools.uiFamily;font.pixelSize:16;id:curve;model:["线性曲线","清晰曲线","柔和曲线"];Layout.fillWidth:true;Layout.preferredHeight:64}
                    LocalizedComboBox {font.family:tools.uiFamily;font.pixelSize:16;id:rotation;model:["0°","90°","180°","270°"];Layout.preferredWidth:116;Layout.preferredHeight:64}
                }
                RowLayout {Text {font.family:tools.uiFamily;font.pixelSize:16;text:qsTranslate("ImageTools", "裁切比例");color:"white";Layout.preferredWidth:104} LocalizedComboBox {font.family:tools.uiFamily;font.pixelSize:16;id:crop;model:["原始构图","1:1","3:2","4:3","16:9"];Layout.fillWidth:true;Layout.preferredHeight:64}}
                CheckBox {font.family:tools.uiFamily;font.pixelSize:16;Layout.minimumHeight:64;id:align;text:qsTranslate("ImageTools", "多帧亚像素对齐");checked:true;visible:operation.currentIndex>=3 && operation.currentIndex<=8;palette.windowText:"#ffffff"}
                TextField {font.family:tools.uiFamily;font.pixelSize:16;id:destination;objectName:"imageToolsDestination";Layout.fillWidth:true;Layout.preferredHeight:64;placeholderText:qsTranslate("ImageTools", "另存为绝对路径（JPEG / TIFF）");selectByMouse:true;inputMethodHints:Qt.ImhNoPredictiveText;text:tools.exportDirectory ? tools.exportDirectory+"/image-"+Date.now()+".jpg" : ""}
                Text {Layout.fillWidth:true;text:tools.processing?qsTranslate("ImageTools", tools.processing.message):qsTranslate("ImageTools", "使用后台任务队列；请选择相册中的原件。");color:"#bdbdbd";wrapMode:Text.WordWrap;font.family:tools.uiFamily;font.pixelSize:13}
                Rectangle {
                    visible:tools.analysis.histogram!==undefined
                    Layout.fillWidth:true
                    Layout.preferredHeight:126
                    color:"#111111"
                    Canvas {
                        id:histogram
                        anchors.fill:parent;anchors.margins:8
                        property var data:histogramStage.currentIndex===1 && tools.analysis.raw ? tools.analysis.raw.combined : (tools.analysis.histogram||[])[3]||[]
                        onDataChanged:requestPaint()
                        onPaint:{var c=getContext("2d");c.reset();if(data.length<4)return;var h=data,max=1;for(var i=0;i<h.length;i++)max=Math.max(max,h[i]);c.strokeStyle="#eeeeee";c.lineWidth=1;c.beginPath();for(i=0;i<h.length;i++){var x=i*width/255,y=height-h[i]*height/max;if(i===0)c.moveTo(x,y);else c.lineTo(x,y)}c.stroke()}
                    }
                }
                LocalizedComboBox { id:histogramStage; visible:tools.analysis.histogram!==undefined; Layout.fillWidth:true; Layout.preferredHeight:64; font.family:tools.uiFamily; font.pixelSize:16; model:tools.analysis.raw ? ["RGB / 亮度 · 处理后 γ2.2 显示域","RAW 码值 · 去马赛克 / 白平衡之前"] : ["RGB / 亮度 · 处理后 γ2.2 显示域"] }
                Text {font.family:tools.uiFamily;font.pixelSize:16;visible:tools.analysis.suggested_exposure_ev!==undefined;Layout.fillWidth:true;text:qsTranslate("ImageTools", "建议曝光补偿 ")+Number(tools.analysis.suggested_exposure_ev||0).toFixed(2)+qsTranslate("ImageTools", " EV · 高光 ")+(Number(tools.analysis.highlight_fraction||0)*100).toFixed(1)+"%";color:"#cccccc";wrapMode:Text.WordWrap}
            }
        }
        Action {Layout.fillWidth:true;Layout.preferredHeight:64;text:operation.currentIndex===2?qsTranslate("ImageTools", "分析原件"):qsTranslate("ImageTools", "创建派生影像");enabled:tools.selectedSources.length>=tools.operations[operation.currentIndex].minimum && (!tools.processing || tools.processing.available);onClicked:tools.submit()}
    }
}
