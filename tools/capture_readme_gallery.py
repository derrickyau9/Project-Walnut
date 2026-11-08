"""Capture unchanged Project Walnut Qt frames with user-supplied demo photographs.

Uses separate new owned control-QA and imported-JPEG display sessions. No physical
camera, network pairing, existing photo library, pixel editing or credential input.
The gallery is a desktop rendering demonstration, not hardware validation.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import sys
import time
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
UI=ROOT/'ui/on-device'


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def set_up_fixture(case,fixture,data_root=None):
    """Redirect only this fixture's new TemporaryDirectory, keeping ROOT intact."""
    if data_root is None:
        case.setUp();return
    original=fixture.tempfile
    def temporary_directory(*args,**kwargs):
        kwargs['dir']=data_root
        return original.TemporaryDirectory(*args,**kwargs)
    fixture.tempfile=SimpleNamespace(TemporaryDirectory=temporary_directory)
    try:case.setUp()
    finally:fixture.tempfile=original
    assert case.directory.resolve().is_relative_to(data_root)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True,help='new output directory, never overwrite')
    parser.add_argument('--native-command',type=Path,required=True)
    parser.add_argument('--language',choices=('zh_CN','en_US'),required=True)
    parser.add_argument('--demo-image',type=Path,required=True,help='explicit display-only local sample JPEG, independent of HF-Sim capture')
    parser.add_argument('--data-root',type=Path,help='new private parent for owned synthetic data; use a neutral project path for visible directory labels')
    parser.add_argument('--width',type=int,default=960)
    parser.add_argument('--height',type=int,default=600)
    parser.add_argument('--limit',type=int,default=0,help='bounded development capture prefix')
    parser.add_argument('--untranslated-development',action='store_true',help='Chinese-only development run; never publish this as bilingual proof')
    args=parser.parse_args(argv)
    assert 800<=args.width<=1920 and 480<=args.height<=1200
    native=args.native_command.resolve(strict=True)
    data_root=None
    if args.data_root is not None:
        data_root=args.data_root.absolute()
        if data_root.exists() or data_root.is_symlink() or data_root.resolve()!=data_root:
            raise ValueError('A new canonical private synthetic-data root is required')
        data_root.mkdir(parents=True,exist_ok=False)
        data_root=data_root.resolve(strict=True)
    out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
    os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
    os.environ.setdefault('QT_QUICK_BACKEND','software')
    os.environ.setdefault('QSG_RENDER_LOOP','basic')
    os.environ['QML_DISABLE_DISK_CACHE']='1'
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    if os.name=='nt':os.environ.setdefault('QT_QPA_FONTDIR',str(Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'))
    sys.path[:0]=[str(ROOT),str(UI)]
    from PyQt5 import QtCore,QtGui,QtQml,QtQuick,QtTest
    from sample_preview import image_url
    sample_url=image_url(args.demo_image)
    sample_manifest=json.loads((UI/'demo-images/SOURCES.json').read_text(encoding='utf8'))
    sample_paths={item['file']:UI/'demo-images'/item['file'] for item in sample_manifest['files']}
    assert set(sample_paths)=={'coast.jpg','campus-building.jpg','campus-trees.jpg'}
    assert args.demo_image.resolve() in {path.resolve() for path in sample_paths.values()}
    for item in sample_manifest['files']:
        assert sha(sample_paths[item['file']])==item['sha256']
        assert sample_paths[item['file']].stat().st_size==item['bytes']
        image_url(sample_paths[item['file']])
    if args.untranslated_development:
        assert args.language=='zh_CN'
        class DevelopmentLocalization(QtCore.QObject):
            @QtCore.pyqtProperty(int,constant=True)
            def revision(self):return 0
            @QtCore.pyqtSlot(str,int,result=str)
            def t(self,value,revision=0):return value
            def bindEngine(self,engine):engine.rootContext().setContextProperty('i18n',self)
            def setLanguage(self,language):pass
        install_localization=lambda app,language:DevelopmentLocalization(app)
    else:
        from localization import install_localization
    spec=importlib.util.spec_from_file_location('readme_actual_ui_fixture',ROOT/'tests/test_camera_first_ui.py')
    fixture=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fixture;spec.loader.exec_module(fixture)
    fixture.CameraFirstUITests.setUpClass()
    app=fixture.CameraFirstUITests.app
    ui_font='Segoe UI' if os.name=='nt' else 'Sans Serif'
    for path in (UI/'fonts').glob('*'):
        if path.suffix.lower() in ('.otf','.ttf'):
            font_id=QtGui.QFontDatabase.addApplicationFont(str(path))
            families=QtGui.QFontDatabase.applicationFontFamilies(font_id) if font_id>=0 else []
            if families:ui_font=families[0]
    app.setFont(QtGui.QFont(ui_font,10))
    from ui_palette import apply_monochrome_palette
    apply_monochrome_palette(app)
    localization=install_localization(app,args.language)
    original_engine=fixture.QQmlApplicationEngine
    def engine_factory():
        engine=original_engine();localization.bindEngine(engine)
        from level_controller import LevelController
        case.level=LevelController(case.directory,parent=case.apps)
        case.helpers.append(case.level)
        engine.rootContext().setContextProperty('_readmeScopeTarget',None)
        engine.rootContext().setContextProperty('cameraUIFont',ui_font)
        engine.rootContext().setContextProperty('cameraTitleFont',ui_font)
        engine.rootContext().setContextProperty('samplePreviewSource',sample_url)
        engine.rootContext().setContextProperty('levelControl',case.level)
        return engine
    fixture.QQmlApplicationEngine=engine_factory
    fixture.native_command=lambda:[str(native)]
    case=fixture.CameraFirstUITests();set_up_fixture(case,fixture,data_root)
    started=time.monotonic();deadline=started+600
    frames=[];actions=[]
    source_paths=[p for p in UI.iterdir() if p.is_file() and p.suffix in ('.py','.qml','.json')]
    source_paths+=sorted((UI/'locales').glob('*.json'))
    source_paths+=sorted((UI/'locales').glob('*.qm'))
    source_paths+=sorted(p for p in (UI/'fonts').glob('*') if p.suffix.lower() in ('.otf','.ttf'))
    source_paths+=sorted(p for p in (UI/'demo-images').iterdir() if p.is_file())
    source_paths.append(UI/'LEVEL-PROVIDER.md')
    source_paths += [ROOT/'tests/test_camera_first_ui.py',Path(__file__)]
    before={p.relative_to(ROOT).as_posix():sha(p) for p in source_paths}
    report={'format':'Project-Walnut-readme-gallery-v1','passed':False,'language':args.language,
            'runtime':{'system':platform.system(),'machine':platform.machine(),'qt':QtCore.QT_VERSION_STR,
                       'pyqt':QtCore.PYQT_VERSION_STR,'qpa':app.platformName(),'width':args.width,'height':args.height,'font_family':ui_font},
            'capture_backend':'Current C++ hf-camerad / HF-Sim','native_sha256':sha(native),
            'native_source_sha256':{p.relative_to(ROOT).as_posix():sha(p) for p in
                  [ROOT/'camera/native/CMakeLists.txt',*sorted((ROOT/'camera/native/src').glob('*')),
                   *sorted((ROOT/'camera/native/include/hf').glob('*'))] if p.is_file()},
            'untranslated_development_only':args.untranslated_development,
            'preview_source':'Explicit user-supplied sample JPEG; independent HF-Sim RAW authority','source_sha256':before,
            'viewfinder_sample':{'file':args.demo_image.name,'sha256':sha(args.demo_image)},
            'explicit_private_data_root':data_root is not None,
            'synthetic_data_parent':str(case.directory.parent.parent),
            'physical_camera_display_network_or_GPIO_validated':False,
            'control_qa':{'capture_actions':0,'captures':[],'separate_session':True,'passed':False},
            'display_capture_actions':0,'display_photo_imports':[],'frames':frames,'actions':actions,
            'level_examples':[],'physical_IMU_measurements':False,
            'limitations':['Windows desktop offscreen Qt rendering, not a photographed device display',
                           'Video Q is a layout; recording is unsupported by current backend',
                           'Desktop Wi-Fi/system/terminal unavailable states are genuine',
                           'No network service or pairing enabled; no credentials shown',
                           'Level DEMO angles are explicit display-test inputs, not IMU measurements',
                           'Views demonstrate controls; screenshots are not proof every operation was executed']}
    def pump(ms=250):QtTest.QTest.qWait(ms)
    def wait(predicate,stage,seconds=10):
        until=min(deadline,time.monotonic()+seconds)
        while time.monotonic()<until:
            if predicate():return
            pump(20)
        raise RuntimeError('Gallery condition timeout: '+stage)
    def qml(obj,code):
        # A composite component's root is registered in its creation context;
        # its ordinary child items carry the context containing internal IDs.
        # Resolve the leading identifier before executing any state mutation.
        roots=[]
        for statement in code.split(';'):
            statement=re.sub(r'^\s*return\s+','',statement).strip()
            match=re.match(r'([A-Za-z_][A-Za-z_0-9]*)',statement)
            if match:roots.append(match.group(1))
        candidates=[obj]+obj.findChildren(QtCore.QObject)
        selected=None
        anchors={'Main':'body','CameraMenu':'menu','CameraPlayback':'playback',
                 'MediaGallery':'page','ShootingWorkspacePanel':'panel',
                 'SystemPage':'page','ImageTools':'tools','RecipeWorkspace':'recipes'}
        anchor=next((value for prefix,value in anchors.items() if obj.metaObject().className().startswith(prefix)),None)
        case.engine.rootContext().setContextProperty('_readmeScopeTarget',obj)
        for candidate in candidates:
            context=QtQml.QQmlEngine.contextForObject(candidate)
            if context is None:continue
            conditions=["typeof "+name+" !== 'undefined'" for name in roots]
            if anchor:conditions.append("typeof "+anchor+" !== 'undefined' && "+anchor+" === _readmeScopeTarget")
            probe=QtQml.QQmlExpression(context,obj,' && '.join(conditions))
            value,_=probe.evaluate()
            if value and not probe.hasError():selected=context;break
        if selected is None:raise RuntimeError('QML identifiers unavailable: '+','.join(roots))
        expression=QtQml.QQmlExpression(selected,obj,'(function(){'+code+'})()')
        result,_=expression.evaluate()
        if expression.hasError():raise RuntimeError(expression.error().toString())
        return result
    def get(obj,expression):return qml(obj,'return '+expression+';')
    def items():
        found=[];seen=set()
        def walk(item):
            if id(item) in seen:return
            seen.add(id(item));found.append(item)
            for child in item.childItems():walk(child)
        walk(case.window.contentItem());return found
    def visible_text():
        texts=[]
        for item in items():
            if not item.isVisible() or item.opacity()<.02:continue
            rect=item.mapRectToScene(QtCore.QRectF(0,0,item.width(),item.height()))
            rect=rect.intersected(QtCore.QRectF(0,0,args.width,args.height))
            parent=item.parentItem()
            while parent is not None:
                if parent.clip():rect=rect.intersected(parent.mapRectToScene(QtCore.QRectF(0,0,parent.width(),parent.height())))
                if parent.opacity()<.02:rect=QtCore.QRectF()
                parent=parent.parentItem()
            if rect.isEmpty():continue
            value=item.property('text')
            if isinstance(value,str) and value.strip():texts.append(value.strip())
        return list(dict.fromkeys(texts))
    forbidden=re.compile(r'\b(?:Hasselblad|Leica|Fujifilm|Sony|Canon|Nikon|Panasonic|Olympus|Pentax|Lumix)\b',re.I)
    def grab(key,zh,en,features):
        if args.limit and len(frames)>=args.limit:raise StopIteration
        pump(320)
        texts=visible_text()
        assert not any(forbidden.search(t) for t in texts),'Unwanted external brand visible'
        assert not case.link.webEnabled and not case.link.pairingCode,'Pairing must stay disabled'
        image_result=case.window.contentItem().grabToImage()
        assert image_result is not None
        ready=[];image_result.ready.connect(lambda:ready.append(True))
        wait(lambda:bool(ready),'actual Qt frame '+key)
        image=image_result.image();name=key+'.png'
        assert image.width()==args.width and image.height()==args.height
        assert image.save(str(out/name))
        frames.append({'key':key,'file':name,'title_zh':zh,'title_en':en,'features':features,
                       'sha256':sha(out/name),'width':image.width(),'height':image.height(),
                       'selected_photo_uuid':case.apps.media.gallery.currentId,
                       'selected_photo_media_type':case.apps.media.gallery.current.get('media_type'),
                       'visible_text':texts,'elapsed_seconds':round(time.monotonic()-started,3)})
        (out/'capture-progress.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print('GALLERY '+args.language+' '+key,flush=True)
    def reset():
        case.keyboard.hide();qml(case.window,'returnFromHardware();taskbarExpanded=false;');pump(250)
    def menu(group):
        reset();case.shell.selectGroup(group);case.shell.navigate('settings');pump(400)
        return case.item('cameraMenu')
    def page(group):
        m=menu(group)
        return get(m,'page.item && page.item.item ? page.item.item : page.item')
    def launch_session():
        case.launch(backend='native',width=args.width,height=args.height)
        if not args.untranslated_development:
            case.system_control.setPreference('language',args.language)
            wait(lambda:case.system_control.preferences.get('language')==args.language and not case.system_control.busy,'language preference confirmed')
            localization.bindSystemController(case.system_control)
        localization.setLanguage(args.language)
        case.link.setEnabled(False);case.workspace.setReducedMotion(True)
        case.controller.setSensorMode('sim-small')
        wait(lambda:case.controller.confirmedSettings.get('sensor_mode')=='sim-small' and case.controller.settingsView.get('canEdit'),'small synthetic mode confirmed')
        wait(lambda:case.window.property('samplePreviewActive') and case.item('viewfinderImage').property('status')==1,'real JPEG sample viewfinder')
    def asset_sha(photo):
        digest=hashlib.sha256();length=0
        with case.controller._client.open_library_asset(photo) as stream:
            for part in iter(lambda:stream.read(262144),b''):digest.update(part);length+=len(part)
        return digest.hexdigest(),length
    prior_warnings=[]
    try:
        launch_session()
        control_library=case.controller._client.describe_library()['library_id']
        # Real authority QA runs in its own session; none of its RAWs enter display.
        for index in range(3):
            previous=case.controller.lastRequestId
            case.controller.shutter();report['control_qa']['capture_actions']+=1
            wait(lambda:case.controller.lastRequestId!=previous,'new synthetic request')
            wait(lambda:case.controller.captureState=='complete','synthetic photo complete')
            request=case.controller.lastRequestId;digest=hashlib.sha256();length=0
            with case.controller._client.open_capture(request) as stream:
                for part in iter(lambda:stream.read(262144),b''):digest.update(part);length+=len(part)
            report['control_qa']['captures'].append({'request_id':request,'status':'complete','bytes':length,'sha256':digest.hexdigest()})
        report['control_qa'].update(passed=True,library_id=control_library)
        prior_warnings.extend(case.warnings);case.tearDown();case=None
        pump(100)
        case=fixture.CameraFirstUITests();set_up_fixture(case,fixture,data_root);launch_session()
        assert not case.controller.lastRequestId and case.controller.captureState=='idle'
        report['display_session_directory']=str(case.directory)
        display_library=case.controller._client.describe_library()['library_id'];assert display_library!=control_library
        report['display_library_id']=display_library
        gallery=case.apps.media.gallery;wait(lambda:gallery.available,'clean display library')
        import_inputs=case.directory/'GalleryInputs';import_inputs.mkdir()
        for name,source in sample_paths.items():
            with (import_inputs/name).open('xb') as target:target.write(source.read_bytes())
            assert sha(import_inputs/name)==sha(source)
        gallery.importPaths('\n'.join(str(import_inputs/name) for name in sample_paths))
        wait(lambda:gallery.totalCount==3 and len(gallery.items)==3 and not gallery.busy,'three real JPEG copy imports',seconds=45)
        by_sha={item['sha256']:item for item in sample_manifest['files']};photos={}
        for item in gallery.items:
            assert item['media_type']=='image/jpeg',item
            digest,length=asset_sha(item['photo_uuid']);source=by_sha[digest]
            assert length==source['bytes']
            photos[source['file']]=item['photo_uuid']
            report['display_photo_imports'].append({'file':source['file'],'photo_uuid':item['photo_uuid'],'sha256':digest,'bytes':length,'media_type':item['media_type'],
                'neutral_source_file':str(import_inputs/source['file']),'neutral_source_sha256':sha(import_inputs/source['file']),
                'method':'MediaWorkspace.importPaths / native copyImport'})
        assert set(photos)==set(sample_paths)
        wait(lambda:all(item.get('thumbUrl') for item in gallery.items),'real JPEG thumbnails',seconds=45)
        for name in ('C1','C2','C3'):case.workspace.saveProfile(name)
        case.apps.saveCaptureRecipe('Bracket example' if args.language=='en_US' else '包围示例','bracket',3,0)
        case.apps.saveCaptureRecipe('Interval example' if args.language=='en_US' else '间隔示例','interval',3,1000)
        export=case.directory/'Exports';export.mkdir(exist_ok=True)
        browse=case.directory/'GalleryInputs';browse.mkdir(exist_ok=True)
        case.workspace.exportWorkspace(str(case.directory/'workspace-example.json'))
        reset();case.shell.setPreference('grid_enabled',True)
        grab('01-capture','拍摄与示例预览','Capture and sample preview',['capture-controls','user-supplied-JPEG-preview','framing-grid','confirmed-settings'])
        assert case.level.setEnabled(True)
        case.level.clearDemo()
        assert case.level.state=='unavailable' and not case.level.available and case.level.rollDeg is None and case.level.pitchDeg is None
        report['level_examples'].append({'key':'01b-level-unavailable','state':'unavailable','roll_deg':None,'pitch_deg':None,'physical_IMU':False})
        grab('01b-level-unavailable','水平仪未连接','Level unavailable',['level-enabled','no-attitude-provider','no-fabricated-measurement'])
        for key,zh,en,roll,pitch in [('01c-level-demo-aligned','水平仪水平示例','Level aligned demo',0.,0.),('01d-level-demo-tilted','水平仪倾斜示例','Level tilted demo',-6.,3.)]:
            assert case.level.setDemoAngles(roll,pitch) and case.level.state=='demo' and case.level.isDemo
            report['level_examples'].append({'key':key,'state':'demo','roll_deg':roll,'pitch_deg':pitch,'physical_IMU':False})
            grab(key,zh,en,['electronic-level','explicit-DEMO','roll','pitch','display-only-test-angles'])
        case.level.clearDemo();assert case.level.setEnabled(False)
        qml(case.window,'taskbarExpanded=true;')
        grab('02-taskbar','应用任务栏','Application taskbar',['taskbar','camera-navigation','app-navigation'])
        reset();qml(case.window,"openQuick('exposure');")
        grab('03-exposure-editor','曝光与增益','Exposure and analogue gain',['exposure','analogue-gain','confirmed-control'])
        reset();qml(case.window,"openWorkspace('quick');")
        panel=get(case.window,'workspacePanel')
        grab('04-quick-photo','照片十二格快捷面板','Photo quick controls',['photo-Q','12-custom-slots','direct-parameter-control'])
        case.workspace.setMode('video')
        grab('05-quick-video','视频布局与能力边界','Video layout and capability limits',['video-Q-layout','unsupported-recording-reason'])
        case.workspace.setMode('photo');qml(panel,'pickSlot(0);')
        grab('06-quick-assignment','快捷功能分配','Quick-control assignment',['quick-slot-assignment','available-actions','unavailable-reasons'])
        reset();qml(case.window,"openWorkspace('my_menu');")
        grab('07-my-menu','我的菜单','My Menu',['favorites','pinned-tools','favorites-management'])
        p=page('workspace');qml(p,'controlsScroll.contentItem.contentY=140;')
        grab('08-custom-profiles','C1–C3 记忆','C1–C3 custom profiles',['C1','C2','C3','save-confirmed-settings','load-profile'])
        qml(p,'controlsScroll.contentItem.contentY=controlsScroll.contentItem.contentHeight-controlsScroll.height;')
        grab('09-input-mappings','Fn 与拨轮映射','Fn and dial mappings',['Fn1','Fn2','dial','hold-actions','taskbar-autohide','reduced-motion'])
        qml(p,'controlsScroll.contentItem.contentY=0;importDialog.open();')
        grab('10-layout-import','工作台配置分享','Workspace configuration sharing',['layout-presets','export-workspace','import-workspace'])
        for key,group,zh,en,features in [
            ('11-exposure-menu','exposure','曝光菜单','Exposure settings',['manual-exposure','shutter','gain']),
            ('12-image-raw','image','图像与 RAW','Image and RAW',['sensor-mode','Bayer','DNG','RAW-capabilities']),
            ('13-display','display','显示与构图','Display and framing',['theme','preview-brightness','grid','electronic-level-toggle']),
            ('14-storage','storage','存储信息','Storage information',['storage-capacity','saved-captures','storage-limitations']),
            ('15-wifi','wifi','无线网络','Wireless network',['wifi-settings','desktop-service-unavailable'])]:
            menu(group);grab(key,zh,en,features)
        p=page('system_control')
        for index,key,zh,en,features in [
            (0,'16-system','系统偏好','System preferences',['language','timezone','idle-screen','clock','safe-power-actions']),
            (1,'17-backup','配置备份与恢复','Configuration backup and restore',['backup','backup-inspection','restore-confirmation']),
            (2,'18-updates','应用更新与回退','Application update and rollback',['update-inspection','SHA256','version-rollback']),
            (3,'19-developer','开发与诊断','Developer and diagnostics',['developer-mode','authorized-USB','diagnostic-export']),
            (4,'20-link','受控联机','Controlled tethering',['USB-controlled-interface','local-takeover','pairing-disabled','LAN-disabled'])]:
            qml(p,'tab='+str(index)+';');grab(key,zh,en,features)
        menu('terminal');pump(500)
        grab('21-terminal','终端入口','Terminal entry',['terminal','POSIX-required-desktop-state','terminal-shortcuts'])
        p=page('system_control');qml(p,'tab=1;backupPath.forceActiveFocus();')
        case.keyboard.setLanguage('en' if args.language=='en_US' else 'zh')
        pump(300)
        grab('22-touch-keyboard','触摸键盘','Touch keyboard',['soft-keyboard','text-entry','pinyin-option','symbols'])
        menu('capabilities');grab('23-capabilities','能力与性能','Capabilities and performance',['actual-capabilities','unsupported-features','observed-UI-performance'])
        reset();case.shell.navigate('diagnostics');pump(400)
        grab('24-diagnostics','运行诊断','Runtime diagnostics',['runtime-identity','diagnostics','actual-backend-status'])
        reset();case.shell.navigate('recipes');pump(400)
        recipe=next(i for i in items() if i.isVisible() and i.metaObject().className().startswith('RecipeWorkspace'))
        grab('25-recipes','拍摄计划','Capture plans',['workflow-plans','pause-resume-cancel','UUID-reconciliation'])
        qml(recipe,'planEditor.open();')
        grab('26-bracketing','曝光包围计划','Exposure-bracket plan',['bracketing','bounded-synthetic-sequence'])
        qml(recipe,"planEditor.kind='interval';")
        grab('27-interval','间隔与校准序列','Interval and calibration sequences',['interval','dark-sequence','flat-sequence','timing-limits'])
        qml(recipe,'planEditor.close();looksTab=true;')
        grab('28-photo-looks','照片显示风格','Photo rendering looks',['non-destructive-looks','RAW-preservation'])
        menu('tasks');grab('29-tasks','后台任务中心','Background task center',['persistent-tasks','processing-tasks','transfer-tasks','cancellation'])
        p=page('image_tools');grab('30-image-tools','图像计算工作台','Image processing workspace',['image-analysis','histogram','processing-inputs','derived-output'])
        qml(p,'operation.popup.open();')
        grab('31-processing-operations','图像处理功能','Image processing operations',['JPEG','16bit-TIFF','HDR','exposure-fusion','focus-stack','denoise-stack','panorama','astro-stack','dark-flat','negative'])
        reset();case.shell.navigate('photos');pump(500)
        playback=case.item('cameraPlayback')
        gallery=case.apps.media.gallery
        gallery.refresh();wait(lambda:gallery.totalCount==3 and not gallery.busy,'whole JPEG library loaded')
        qml(playback,'modernLibraryOpen=true;modernGallery.viewer=false;')
        view=case.item('wholeLibraryGallery');pump(400)
        grab('32-photo-library','照片库','Photo library',['thumbnail-library','filters','sort','pagination','import'])
        gallery.setMultiSelect(True);gallery.selectAll();wait(lambda:gallery.selectionCount>=3,'multiselection')
        grab('33-multi-select','多选与批量管理','Multi-selection and batch management',['persistent-selection','batch-actions','selected-count'])
        gallery.clearSelection();wait(lambda:gallery.selectionCount==0,'batch selection cleared')
        gallery.setMultiSelect(False);gallery.selectPhoto(photos['campus-building.jpg'])
        qml(view,'viewer=true;');wait(lambda:bool(gallery.reviewUrl),'photo viewer image')
        grab('34-viewer','照片回放','Photo playback',['image-viewer','rendered-JPEG-review','imported-photo-metadata'])
        gallery.setCompare(photos['campus-trees.jpg']);wait(lambda:bool(gallery.compareUrl),'comparison image')
        qml(view,'compareMode=true;nativeZoom=true;requestTiles();')
        wait(lambda:bool(gallery.tileUrl),'real JPEG pixel tiles',seconds=30)
        grab('35-compare-zoom','并排与像素查看','Side-by-side and pixel inspection',['comparison','native-pixel-tiles','pan-zoom'])
        gallery.selectPhoto(photos['coast.jpg']);wait(lambda:gallery.currentId==photos['coast.jpg'] and bool(gallery.reviewUrl),'coast photo for editing')
        qml(view,"nativeZoom=false;compareMode=false;editorSection='tone';editPopup.open();")
        before_edit=gallery.reviewUrl
        gallery.setEdit('exposure',.3);wait(lambda:len(gallery.editHistory)>=2 and not gallery.busy and bool(gallery.reviewUrl) and gallery.reviewUrl!=before_edit,'real JPEG edit revision and rendered derivative',seconds=30)
        grab('36-tone-crop','非破坏明暗编辑','Non-destructive tone editing',['exposure-edit','contrast','saturation','noise','sharpen','original-preserved'])
        qml(view,'editScroll.contentItem.contentY=editScroll.contentItem.contentHeight-editScroll.height;')
        grab('36b-rotation-crop','旋转与裁切','Rotation and crop',['rotation','crop','undo-redo','reset-edits'])
        qml(view,'editScroll.contentItem.contentY=0;')
        qml(view,"editorSection='whiteBalance';")
        grab('37-white-balance','白平衡与 RGB','White balance and RGB',['white-balance','RGB-gains','neutral-picker'])
        qml(view,"editorSection='curve';")
        grab('38-tone-curve','色调曲线','Tone curve',['curve-control','shadow-midtone-highlight'])
        qml(view,"editorSection='styles';")
        grab('39-custom-styles','自定义风格','Custom styles',['save-style','apply-style','non-destructive-preset'])
        qml(view,'editPopup.close();historyPopup.open();')
        grab('40-edit-history','编辑历史','Edit history',['edit-history','revision-selection','restore-edit-version'])
        qml(view,'historyPopup.close();exportPopup.open();')
        grab('41-export','批量导出','Batch export',['original-and-sidecar','full-size-JPEG','16bit-TIFF','background-export'])
        qml(view,'exportPopup.close();');gallery.browseDirectory(str(browse));pump(300);qml(view,"openBrowser('import');")
        grab('42-import','文件浏览与复制导入','File browser and copy import',['import-browser','copy-import','destination-browser'])
        qml(view,'browserPopup.close();optionsPopup.open();')
        grab('43-library-actions','照片库工具','Photo library actions',['search','protect','trash','restore','rescan'])
        qml(view,'optionsPopup.close();');gallery.setRating(4);wait(lambda:not gallery.busy and gallery.current.get('rating')==4,'real JPEG rating saved')
        qml(view,'ratingPopup.open();')
        grab('44-rating','照片评分','Photo ratings',['rating','batch-rating'])
        qml(view,'ratingPopup.close();infoPopup.open();')
        grab('45-photo-info','照片元数据','Photo metadata',['capture-identity','metadata','editing-provenance'])
        qml(view,'infoPopup.close();purgePopup.open();')
        grab('46-trash-confirmation','永久清空确认','Permanent-delete confirmation',['trash-purge-confirmation','cancel-safe-default'])
        for imported in report['display_photo_imports']:
            assert asset_sha(imported['photo_uuid'])==(imported['sha256'],imported['bytes'])
        report['display_original_JPEG_bytes_unchanged']=True
        report['passed']=True
    except StopIteration:
        report['development_prefix_only']=True;report['passed']=True
    finally:
        report['qml_warnings']=prior_warnings+(case.warnings if case is not None else [])
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        report['source_changed_during_capture']=[name for name,d in before.items() if sha(ROOT/name)!=d]
        report['passed']=report['passed'] and not report['source_changed_during_capture']
        (out/'capture.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        if case is not None:case.tearDown()
    return 0 if report['passed'] else 1


if __name__=='__main__':raise SystemExit(main())
