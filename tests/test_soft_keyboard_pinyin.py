"""Offline Pinyin and real Qt editor/terminal interaction regression checks."""
import json
import hashlib
import os
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / 'ui' / 'on-device'
EVIDENCE = ROOT / 'build' / 'DaOS_Camera_Experience_001' / 'keyboard-qa'
sys.path.insert(0, str(UI))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ.setdefault('QT_QUICK_BACKEND', 'software')
os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
if os.name == 'nt':
    os.environ.setdefault('QT_QPA_FONTDIR', str(Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts'))

from pinyin_ime import PinyinEngine


class PinyinDictionaryTests(unittest.TestCase):
    def test_real_dictionary_coverage_and_sentence_decoding(self):
        engine = PinyinEngine()
        self.assertGreater(engine.entry_count, 60000)
        examples = {'nihao':'你好', 'zhongguo':'中国', 'baipingheng':'白平衡',
                    'shumeipai':'树莓派', 'lvse':'绿色', "xi'an":'西安',
                    'jintiantianqihenhao':'今天天气很好', 'nihaozhongguo':'你好中国'}
        for spelling, text in examples.items():
            with self.subTest(spelling=spelling):
                self.assertEqual(engine.lookup(spelling)[0].text, text)
        self.assertIn('中国', [item.text for item in engine.lookup('zhongg')[:9]])
        self.assertEqual(engine.lookup('x'*65), ())
        engine.connection.close()

    def test_missing_dictionary_does_not_fake_chinese(self):
        engine = PinyinEngine(EVIDENCE / 'not-installed.sqlite3')
        self.assertFalse(engine.available)
        self.assertEqual(engine.lookup('nihao'), ())


try:
    from PyQt5.QtCore import QObject, QPoint, QPointF, Qt, QUrl
    from PyQt5.QtGui import QGuiApplication, QFont, QFontDatabase, QRawFont
    from PyQt5.QtQml import QQmlApplicationEngine, qmlRegisterType
    from PyQt5.QtQuick import QQuickItem, QQuickWindow
    from PyQt5.QtTest import QTest
    from soft_keyboard import SoftKeyboardController
    from terminal_surface import TerminalSurface
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


@unittest.skipUnless(QT_AVAILABLE, 'PyQt5 UI runtime is not installed')
class QtPinyinInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QGuiApplication.instance() or QGuiApplication([])
        cls.font_id = QFontDatabase.addApplicationFont(str(UI / 'fonts' / 'NotoSansCJKsc-Regular.otf'))
        cls.heading_font_id = QFontDatabase.addApplicationFont(str(UI / 'fonts' / 'NotoSansCJKsc-Medium.otf'))
        cls.app.setFont(QFont('Noto Sans CJK SC', 10))
        qmlRegisterType(TerminalSurface, 'DaOS.KeyboardTest', 1, 0, 'TestTerminal')
        EVIDENCE.mkdir(parents=True, exist_ok=True)

    def setUp(self):
        self.keyboard = SoftKeyboardController(self.app)
        self.engine = QQmlApplicationEngine()
        self.warnings = []
        self.engine.warnings.connect(lambda warnings:self.warnings.extend(item.toString() for item in warnings))
        self.engine.rootContext().setContextProperty('softKeyboard', self.keyboard)
        self.engine.loadData(b'''import QtQuick 2.15
import QtQuick.Controls 2.15
import DaOS.KeyboardTest 1.0
ApplicationWindow { width:800; height:480; visible:true; color:"#000000"
    Flickable {objectName:"scroll";anchors.fill:parent;contentHeight:900;clip:true
        TextField {objectName:"coveredEditor";x:20;y:650;width:400;height:44;placeholderText:"Covered field"}
    }
    TextField {objectName:"editor";x:20;y:20;width:400;height:44;placeholderText:"Camera profile";
        property int accepts:0; onAccepted:accepts++}
    TextField {objectName:"password";x:20;y:70;width:400;height:44;placeholderText:"Wi-Fi password";
        echoMode:TextInput.Password;inputMethodHints:Qt.ImhSensitiveData|Qt.ImhNoPredictiveText}
    TextField {objectName:"numeric";x:20;y:120;width:400;height:44;inputMethodHints:Qt.ImhDigitsOnly}
    TestTerminal {objectName:"terminal";x:20;y:172;width:760;height:48;visible:false}
    SoftKeyboard {anchors.left:parent.left;anchors.right:parent.right;anchors.bottom:parent.bottom}
}''', QUrl.fromLocalFile(str(UI / 'KeyboardQAHarness.qml')))
        self.assertTrue(self.engine.rootObjects(), self.warnings)
        self.window = self.engine.rootObjects()[0]
        self.window.requestActivate()
        self.editor = self.focus('editor')

    def tearDown(self):
        self.app.removeEventFilter(self.keyboard)
        self.keyboard.timer.stop()
        try:self.app.focusObjectChanged.disconnect(self.keyboard.refresh)
        except (RuntimeError, TypeError):pass
        self.keyboard._clear_composition()
        self.window.close()
        self.engine.deleteLater()
        self.keyboard.deleteLater()
        QTest.qWait(20)

    def focus(self, name):
        item = self.window.findChild(QObject, name)
        item.forceActiveFocus(Qt.OtherFocusReason)
        QTest.qWait(25)
        self.keyboard.refresh()
        self.assertIs(self.keyboard.target, item)
        return item

    def visual(self, name):
        pending = [self.window.contentItem()]
        while pending:
            item = pending.pop()
            if item.objectName() == name:return item
            pending.extend(item.childItems())
        self.fail('Visible item not found: ' + name)

    def screenshot(self, name):
        result = self.window.contentItem().grabToImage()
        for _ in range(100):
            QTest.qWait(10)
            if not result.image().isNull():break
        image = result.image()
        self.assertEqual((image.width(), image.height()), (800, 480))
        self.assertTrue(image.save(str(EVIDENCE / name)))

    def test_chinese_preedit_space_enter_and_visible_candidates(self):
        self.keyboard.commit('nihao')
        self.assertEqual(self.editor.property('text'), '')
        self.assertEqual(self.editor.property('preeditText'), 'nihao')
        self.assertEqual(self.keyboard.candidates[0]['text'], '你好')
        self.screenshot('pinyin-composition-800x480.png')
        self.keyboard.commit(' ')
        self.assertEqual(self.editor.property('text'), '你好')
        self.keyboard.commit('zhongguo')
        self.keyboard.key('enter')
        self.assertEqual(self.editor.property('text'), '你好中国')
        self.assertEqual(self.editor.property('accepts'), 0)
        self.keyboard.key('enter')
        self.assertEqual(self.editor.property('accepts'), 1)
        self.assertFalse(self.warnings, self.warnings)

    def test_partial_word_choice_keeps_tail_and_backspace_edits_composition(self):
        self.keyboard.commit('nihaozhongguo')
        index = next(index for index, item in enumerate(self.keyboard.candidates) if item['text'] == '你好')
        self.keyboard.chooseCandidate(index)
        self.assertEqual(self.editor.property('text'), '你好')
        self.assertEqual(self.keyboard.preeditText, 'zhongguo')
        self.keyboard.commit(' ')
        self.assertEqual(self.editor.property('text'), '你好中国')
        self.keyboard.commit('nihao')
        self.keyboard.key('left');self.keyboard.key('left');self.keyboard.key('backspace')
        self.assertEqual(self.keyboard.preeditText, 'niao')
        self.keyboard.commit('h')
        self.assertEqual(self.keyboard.preeditText, 'nihao')
        self.keyboard.key('escape')
        self.assertEqual(self.editor.property('text'), '你好中国')

    def test_physical_keyboard_number_choice_and_candidate_pages(self):
        for character in 'shi':QTest.keyClick(self.window, ord(character.upper()))
        self.assertEqual(self.keyboard.preeditText, 'shi')
        selected = self.keyboard.candidates[1]['text']
        QTest.keyClick(self.window, Qt.Key_2)
        self.assertEqual(self.editor.property('text'), selected)
        self.keyboard.commit('shi')
        self.assertTrue(self.keyboard.hasNextCandidates)
        self.keyboard.pageCandidates(1)
        selected = self.keyboard.candidates[0]['text']
        previous = self.editor.property('text')
        self.keyboard.commit('1')
        self.assertEqual(self.editor.property('text'), previous + selected)
        self.assertEqual(self.keyboard.preeditText, '')

    def test_password_defaults_to_ascii_and_never_exposes_plaintext(self):
        self.keyboard.commit('nihao')
        password = self.focus('password')
        self.assertEqual(self.keyboard.preeditText, '')
        self.assertEqual(self.keyboard.inputLanguage, 'en')
        self.assertFalse(self.keyboard.pinyinEnabled)
        self.keyboard.setLanguage('zh')
        self.keyboard.commit('Example123!')
        self.assertEqual(password.property('text'), 'Example123!')
        self.assertEqual(self.keyboard.candidates, [])
        for visible in (self.keyboard.editorText, self.keyboard.previewText, self.keyboard.inputStatus):
            self.assertNotIn('Example', visible)
        self.assertEqual(set(self.keyboard.editorText), {'\u2022'})
        self.screenshot('password-800x480.png')

    def test_unicode_cursor_selection_and_ascii_layouts(self):
        self.editor.setProperty('text', 'A😀中B')
        self.keyboard.setCursorPosition(3)  # after the two-unit emoji
        self.keyboard.commit('nihao'); self.keyboard.commit(' ')
        self.assertEqual(self.editor.property('text'), 'A😀你好中B')
        self.keyboard.key('backspace')
        self.assertEqual(self.editor.property('text'), 'A😀你中B')
        self.keyboard.key('selectAll')
        self.keyboard.setLanguage('en')
        self.keyboard.commit('abc')
        self.keyboard.setMode('numbers');self.keyboard.commit('123')
        self.keyboard.setMode('symbols');self.keyboard.commit('@_')
        self.assertEqual(self.editor.property('text'), 'abc123@_')
        self.keyboard.setMode('letters');self.keyboard.toggleShift();self.keyboard.commit('Z')
        self.assertEqual(self.keyboard.shiftState, 0)
        self.keyboard.done()
        self.assertFalse(self.keyboard.isOpen)

    def test_terminal_keeps_raw_bytes_and_does_not_mirror_its_output(self):
        terminal = self.window.findChild(QObject, 'terminal')
        terminal._running = True
        terminal.setProperty('visible', True)
        sent = []
        terminal.send = sent.append
        self.focus('terminal')
        self.assertTrue(self.keyboard.terminal)
        self.assertEqual(self.keyboard.inputLanguage, 'en')
        self.keyboard.commit('ls -la')
        for key in ('left', 'backspace', 'enter', 'interrupt'):self.keyboard.key(key)
        self.assertEqual(sent, [b'ls -la', b'\x1b[D', b'\x7f', b'\r', b'\x03'])
        self.assertEqual(self.keyboard.editorText, '')
        self.assertEqual(self.keyboard.candidates, [])
        terminal._running = False

    def test_touch_keys_retain_editor_focus_and_flickable_reveals_editor(self):
        for character in 'nihao':
            key = self.visual('keyboardKey_' + character)
            point = key.mapToScene(QPointF(key.width()/2, key.height()/2))
            QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(int(point.x()), int(point.y())))
        self.assertEqual(self.keyboard.preeditText, 'nihao')
        self.assertIs(self.app.focusObject(), self.editor)
        QTest.qWait(40)  # Let Qt Quick lay out the newly-created candidate delegates.
        candidate = self.visual('pinyinCandidate0')
        point = candidate.mapToScene(QPointF(candidate.width()/2, candidate.height()/2))
        QTest.mouseClick(self.window, Qt.LeftButton, Qt.NoModifier, QPoint(int(point.x()), int(point.y())))
        self.assertEqual(self.editor.property('text'), '你好')
        covered = self.focus('coveredEditor')
        self.keyboard.avoid_editor()
        bottom = covered.mapToScene(QPointF(0, covered.height())).y()
        self.assertLessEqual(bottom, 480-252)
        self.assertGreater(self.window.findChild(QObject, 'scroll').property('contentY'), 0)
        self.keyboard.hide()
        self.assertEqual(self.window.findChild(QObject, 'scroll').property('bottomMargin'), 0)

    def test_bundled_static_font_identity_and_chinese_glyphs(self):
        source = json.loads((UI / 'fonts' / 'SOURCE.json').read_text(encoding='utf-8'))
        for name, details in source['files'].items():
            self.assertEqual(hashlib.sha256((UI / 'fonts' / name).read_bytes()).hexdigest(), details['sha256'])
        self.assertGreaterEqual(self.font_id, 0)
        self.assertGreaterEqual(self.heading_font_id, 0)
        self.assertIn('Noto Sans CJK SC', QFontDatabase.applicationFontFamilies(self.font_id))
        for weight, style in ((QFont.Normal, 'Regular'), (QFont.Medium, 'Medium')):
            selected = QFont('Noto Sans CJK SC', 18)
            selected.setWeight(weight)
            font = QRawFont.fromFont(selected)
            self.assertEqual(font.familyName(), 'Noto Sans CJK SC')
            self.assertEqual(font.styleName(), style)
            self.assertEqual(font.weight(), weight)
            self.assertFalse(font.fontTable('fvar'))
            self.assertTrue(font.fontTable('CFF '))
            self.assertTrue(all(font.supportsCharacter(ord(character)) for character in '中文拼音候选拍摄白平衡树莓派ABC123'))

    def test_legacy_shell_without_controller_keeps_keyboard_hidden(self):
        legacy = QQmlApplicationEngine()
        warnings = []
        legacy.warnings.connect(lambda messages:warnings.extend(item.toString() for item in messages))
        legacy.loadData(b'''import QtQuick 2.15
import QtQuick.Controls 2.15
ApplicationWindow {width:800;height:480;visible:true
    SoftKeyboard {anchors.left:parent.left;anchors.right:parent.right;anchors.bottom:parent.bottom}
}''', QUrl.fromLocalFile(str(UI / 'LegacyKeyboardQAHarness.qml')))
        self.assertTrue(legacy.rootObjects(), warnings)
        window = legacy.rootObjects()[0]
        QTest.qWait(30)
        self.assertFalse(window.findChild(QObject, 'touchKeyboard').property('visible'))
        self.assertFalse(warnings, warnings)
        window.close();legacy.deleteLater()


if __name__ == '__main__':
    unittest.main()
