"""Native Qt display localization; protocol and user values remain unchanged."""
from pathlib import Path
import json
import importlib.util
import hashlib
import os
import sys
import tempfile
import unittest

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
os.environ.setdefault('QT_QUICK_BACKEND','software')
UI=Path(__file__).resolve().parents[1]/'ui/on-device'
sys.path.insert(0,str(UI))
from PyQt5.QtCore import QObject,QUrl,pyqtProperty
from PyQt5.QtGui import QGuiApplication
from PyQt5.QtQml import QQmlApplicationEngine
from PyQt5.QtQuick import QQuickItem
from PyQt5.QtTest import QTest
from localization import install_localization, saved_language, validate_language
from system_archive import validate_preferences
from system_control import PREFERENCES, SystemController


class LocalizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QGuiApplication.instance() or QGuiApplication([])
        cls.previous=getattr(cls.app,'_daos_localization',None)
        cls.previous_language=cls.previous.language if cls.previous else None
        cls.local=install_localization(cls.app,'zh_CN')

    def tearDown(self):
        self.local.setLanguage('zh_CN')

    @classmethod
    def tearDownClass(cls):
        if cls.previous is not None:
            cls.local.setLanguage(cls.previous_language)
        else:
            cls.app.removeTranslator(cls.local._translator)
            if getattr(cls.app,'_daos_localization',None) is cls.local:
                del cls.app._daos_localization

    def test_live_qml_translation_preserves_identifiers_and_user_text(self):
        engine=QQmlApplicationEngine()
        self.local.bindEngine(engine)
        engine.loadData('''import QtQuick 2.15
Item {objectName:"camera-route";property string route:"camera"
property string display:qsTr("系统")
property string userName:"系统"
property string path:"/photos/系统.dng"
}'''.encode('utf-8'))
        self.assertTrue(engine.rootObjects())
        obj=engine.rootObjects()[0]
        self.assertEqual(obj.property('display'),'系统')
        self.local.setLanguage('en_US');self.app.processEvents()
        self.assertEqual(obj.property('display'),'System')
        self.assertEqual(obj.objectName(),'camera-route')
        self.assertEqual(obj.property('route'),'camera')
        self.assertEqual(obj.property('userName'),'系统')
        self.assertEqual(obj.property('path'),'/photos/系统.dng')
        self.local.setLanguage('zh_CN');self.app.processEvents()
        self.assertEqual(obj.property('display'),'系统')
        self.local._engines.remove(engine)

    def test_unknown_text_is_preserved(self):
        for language in ('en_US','zh_CN'):
            self.local.setLanguage(language)
            self.assertEqual(self.local.t('my own filename 系统-42.dng'),'my own filename 系统-42.dng')

    def test_controller_status_templates_preserve_opaque_values(self):
        self.local.setLanguage('en_US')
        path='F:/照片/系统.dng'
        self.assertEqual(self.local.t('配置已导出：'+path),'Configuration exported: '+path)
        self.assertEqual(self.local.t('资料库已同步 · 3 张'),'Library synchronized · 3 photos')
        for value in ('资料库已同步 · 用户名称 张','用户配置已导出：'+path):
            self.assertEqual(self.local.t(value),value)
        self.local.setLanguage('zh_CN')
        self.assertEqual(self.local.t('配置已导出：'+path),'配置已导出：'+path)

    def test_task_skip_template_accepts_only_the_complete_integer_status(self):
        self.local.setLanguage('en_US')
        for count in (0,1,12,1000):
            self.assertEqual(self.local.t('完成 · 跳过 '+str(count)+' 项'),'Completed · '+str(count)+' skipped')
        for text in ('完成 · 跳过 -1 项','完成 · 跳过 1.5 项','完成 · 跳过 用户 项','/photos/完成 · 跳过 2 项.jpg'):
            self.assertEqual(self.local.t(text),text)
        self.local.setLanguage('zh_CN')
        self.assertEqual(self.local.t('完成 · 跳过 2 项'),'完成 · 跳过 2 项')

    def test_task_page_translates_import_status_without_mutating_jobs(self):
        record={'kind':'import','status':'complete','stage':'完成 · 跳过 0 项','error':'','done':3,'total':3,'progress':1,
                'job_id':'job-复制导入','destination':'/photos/复制导入.jpg'}
        before=json.dumps(record,ensure_ascii=False,sort_keys=True)
        class Tasks(QObject):
            @pyqtProperty('QVariantList',constant=True)
            def jobs(self):return [record]
            @pyqtProperty(int,constant=True)
            def pendingCount(self):return 0
            @pyqtProperty(str,constant=True)
            def message(self):return ''
            @pyqtProperty(str,constant=True)
            def detailText(self):return ''
        tasks=Tasks();engine=QQmlApplicationEngine();self.local.bindEngine(engine)
        engine.rootContext().setContextProperty('taskFixture',tasks)
        engine.loadData(b'import QtQuick 2.15\nimport QtQuick.Window 2.15\nimport "."\nWindow {visible:true;width:800;height:480;TasksPage {anchors.fill:parent;taskService:taskFixture}}',QUrl.fromLocalFile(str(UI/'task-localization-test.qml')))
        self.assertTrue(engine.rootObjects());root=engine.rootObjects()[0]
        def visual_texts(item):
            result=[]
            for child in item.childItems():
                text=child.property('text')
                if isinstance(text,str):result.append(text)
                result.extend(visual_texts(child))
            return result
        try:
            for language,title,stage in [('en_US','Copy and import · Completed','Completed · 0 skipped'),('zh_CN','复制导入 · 已完成','完成 · 跳过 0 项')]:
                self.local.setLanguage(language);QTest.qWait(20)
                texts=visual_texts(root.contentItem())
                self.assertIn(title,texts);self.assertIn(stage,texts)
                self.assertEqual(json.dumps(record,ensure_ascii=False,sort_keys=True),before)
        finally:
            root.close();self.local._engines.remove(engine)

    def test_combo_translates_display_without_rewriting_model_or_current_text(self):
        engine=QQmlApplicationEngine();self.local.bindEngine(engine)
        engine.setBaseUrl(QUrl.fromLocalFile(str(UI/'localization-test.qml')))
        engine.loadData('''import QtQuick 2.15
import "."
LocalizedComboBox {objectName:"unchanged-id";model:[{id:"exposure",label:"曝光"}];textRole:"label"}
'''.encode('utf-8'),QUrl.fromLocalFile(str(UI/'localization-test.qml')))
        self.assertTrue(engine.rootObjects())
        obj=engine.rootObjects()[0]
        self.assertEqual(obj.property('currentText'),'曝光')
        self.local.setLanguage('en_US');self.app.processEvents()
        self.assertEqual(obj.property('displayText'),'Exposure')
        self.assertEqual(obj.property('currentText'),'曝光')
        self.assertEqual(obj.objectName(),'unchanged-id')
        self.assertEqual(obj.property('currentIndex'),0)
        self.local._engines.remove(engine)

    def test_native_catalog_manifest_matches_committed_sources(self):
        manifest=json.loads((UI/'locales/manifest.json').read_text(encoding='utf-8'))
        self.assertIn('LocalizedComboBox',manifest['contexts'])
        for language,row in manifest['files'].items():
            for extension,key in [('json','source_sha256'),('qm','qm_sha256')]:
                self.assertEqual(hashlib.sha256((UI/'locales'/(language+'.'+extension)).read_bytes()).hexdigest(),row[key])

    def test_language_validation_is_strict(self):
        for value in ('en_US','zh_CN'):
            self.assertEqual(validate_language(value),value)
        for value in ('en','zh','EN_US','en-US','',None,True,[],{}):
            with self.assertRaises(ValueError):validate_language(value)

    def test_startup_override_is_persisted_and_reloadable(self):
        with tempfile.TemporaryDirectory() as work:
            controller=SystemController(work,language='en_US')
            try:
                self.assertEqual(controller.preferences['language'],'en_US')
                self.assertEqual(saved_language(work),'en_US')
                stored=json.loads((Path(work)/'system-preferences.json').read_text(encoding='utf-8'))
                self.assertEqual(validate_preferences(stored)['language'],'en_US')
            finally:controller.close()
            restored=SystemController(work)
            try:self.assertEqual(restored.preferences['language'],'en_US')
            finally:restored.close()

    def test_preference_validation_keeps_other_fields_strict(self):
        for language in ('zh_CN','en_US'):
            self.assertEqual(validate_preferences(dict(PREFERENCES,language=language))['language'],language)
        for language in ('en_US_extra',[],False,None):
            with self.assertRaises(ValueError):validate_preferences(dict(PREFERENCES,language=language))
        with self.assertRaises(ValueError):validate_preferences(dict(PREFERENCES,route='system'))

    def test_invalid_saved_language_falls_back_without_rewriting(self):
        with tempfile.TemporaryDirectory() as work:
            path=Path(work)/'system-preferences.json';original=b'{"language":"unknown"}'
            path.write_bytes(original)
            self.assertEqual(saved_language(work),'zh_CN')
            self.assertEqual(path.read_bytes(),original)

    def test_old_loader_needs_no_i18n_context(self):
        engine=QQmlApplicationEngine()
        engine.loadData(b'import QtQuick 2.15\nItem {property string display: qsTr("Camera")}')
        self.assertTrue(engine.rootObjects())


class NativeLocalizationCaptureTests(unittest.TestCase):
    def test_native_capture_across_en_zh_en_preserves_authority_and_routes(self):
        root=UI.parents[1]
        native=os.environ.get('HF_NATIVE_CAMERAD') or os.environ.get('HF_NATIVE_DAEMON')
        if not native:
            candidates=[root/'build/native/Release/hf-camerad.exe',root/'build/native/hf-camerad',
                        root/'build/native/hf-camerad.exe',root/'build/readme-gallery-native-001/Release/hf-camerad.exe']
            native=next((str(path)for path in candidates if path.is_file()),None)
        if not native or not Path(native).is_file():
            self.skipTest('Build hf-camerad and set HF_NATIVE_CAMERAD for native UI capture QA')
        path=root/'tests/test_camera_first_ui.py'
        spec=importlib.util.spec_from_file_location('localization_native_ui_fixture',path)
        fixture=importlib.util.module_from_spec(spec);sys.modules[spec.name]=fixture;spec.loader.exec_module(fixture)
        fixture.CameraFirstUITests.setUpClass()
        app=fixture.CameraFirstUITests.app
        previous_local=getattr(app,'_daos_localization',None)
        previous_language=previous_local.language if previous_local else None
        local=install_localization(app,'en_US')
        original=fixture.QQmlApplicationEngine
        def engine_factory():
            engine=original();local.bindEngine(engine);return engine
        fixture.QQmlApplicationEngine=engine_factory
        fixture.native_command=lambda:[native]
        case=fixture.CameraFirstUITests();case.setUp()
        try:
            case.launch(backend='native')
            local.bindSystemController(case.system_control)
            case.controller.setSensorMode('sim-small')
            case.wait(lambda:case.controller.confirmedSettings.get('sensor_mode')=='sim-small' and case.controller.settingsView.get('canEdit'))
            case.shell.selectGroup('exposure');case.shell.navigate('settings')
            expected=dict(case.controller.confirmedSettings)
            for language,caption in [('en_US','Menu'),('zh_CN','菜单'),('en_US','Menu')]:
                previous=case.controller.lastRequestId
                case.system_control.setPreference('language',language)
                case.wait(lambda:not case.system_control.busy and local.language==language)
                self.assertEqual(case.shell.currentApp,'settings')
                self.assertEqual(case.shell.currentGroup,'exposure')
                self.assertEqual(dict(case.controller.confirmedSettings),expected)
                self.assertEqual(case.controller.lastRequestId,previous,'Language switch submitted a capture')
                button=case.window.findChild(fixture.QObject,'menuButton')
                self.assertEqual(button.property('text'),caption)
                case.controller.shutter()
                case.wait(lambda:case.controller.lastRequestId!=previous)
                case.wait(lambda:case.controller.captureState=='complete')
                self.assertEqual(dict(case.controller.confirmedSettings),expected)
        finally:
            case.tearDown()
            if previous_local is not None:
                local.setLanguage(previous_language)
            else:
                app.removeTranslator(local._translator)
                if getattr(app,'_daos_localization',None) is local:del app._daos_localization


if __name__=='__main__':unittest.main()
