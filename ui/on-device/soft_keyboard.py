"""Touch keyboard and offline Pinyin through Qt's normal input-method path."""
from PyQt5.QtCore import QObject, QCoreApplication, QEvent, QPointF, Qt, QTimer, pyqtProperty, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QInputMethodEvent, QInputMethodQueryEvent, QKeyEvent, QTextCharFormat, QWindow
from pinyin_ime import PinyinEngine


class SoftKeyboardController(QObject):
    changed = pyqtSignal()

    def __init__(self, app):
        super().__init__(app)
        self.app, self.engine, self.target = app, PinyinEngine(), None
        self.opened = self.dismissed = self.active = False
        self.layout, self.shift, self.panel_height = 'letters', 0, 252
        self.preview = self.editor_text = ''
        self.cursor = self.anchor = 0
        self.label = 'Text'
        self.sensitive_input = self.terminal_input = False
        self.pinyin_allowed = True
        self.preferred_language = self.language = 'zh'
        self.composition, self.composition_cursor = '', 0
        self.candidate_items, self.candidate_page = (), 0
        self._sending, self._consumed_keys = False, set()
        self._avoidance_item, self._saved_bottom_margin = None, 0
        app.focusObjectChanged.connect(self.refresh)
        app.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def query(self):
        target = self.app.focusObject()
        if target is None: return None, None
        try:
            if target.property('readOnly') or target.property('enabled') is False: return None, None
            event = QInputMethodQueryEvent(Qt.ImEnabled | Qt.ImHints | Qt.ImSurroundingText |
                                          Qt.ImCursorPosition | Qt.ImAnchorPosition)
            QCoreApplication.sendEvent(target, event)
            return (target, event) if event.value(Qt.ImEnabled) else (None, None)
        except RuntimeError: return None, None

    @staticmethod
    def prop(target, name, default=None):
        try:
            value = target.property(name) if target else None
            return default if value is None else value
        except RuntimeError: return default

    def _state(self):
        return (self.opened, self.active, self.layout, self.preview, self.label, self.shift,
                self.editor_text, self.cursor, self.anchor, self.sensitive_input,
                self.terminal_input, self.language, self.pinyin_allowed, self.composition,
                self.composition_cursor, self.candidate_items, self.candidate_page)

    def refresh(self, *_):
        before = self._state()
        target, event = self.query()
        different = target is not self.target
        hints = int(event.value(Qt.ImHints) or 0) if event else 0
        if different:
            self._restore_avoidance()
            self._consumed_keys.clear()
            # An unconfirmed word must never move into another field/password.
            self._clear_composition(self.target)
            self.dismissed, self.shift = False, 0
            self.layout = 'numbers' if hints & int(Qt.ImhDigitsOnly | Qt.ImhFormattedNumbersOnly | Qt.ImhPreferNumbers) else 'letters'
            if hints & int(Qt.ImhUppercaseOnly): self.shift = 2
        self.target, self.active = target, target is not None
        self.opened = self.active and not self.dismissed
        self.terminal_input = bool(self.prop(target, 'terminalInput', False))
        self.sensitive_input = bool(hints & int(Qt.ImhHiddenText | Qt.ImhSensitiveData)) or bool(self.prop(target, 'echoMode', 0))
        latin = Qt.ImhLatinOnly | Qt.ImhDigitsOnly | Qt.ImhFormattedNumbersOnly | Qt.ImhUppercaseOnly | Qt.ImhLowercaseOnly
        self.pinyin_allowed = not (self.sensitive_input or self.terminal_input or hints & int(latin))
        if not self.pinyin_allowed or not self.engine.available:
            self._clear_composition(target)
            self.language = 'en'
        elif different: self.language = self.preferred_language
        if event is not None:
            text = str(event.value(Qt.ImSurroundingText) or '')
            raw = text.encode('utf-16-le', errors='surrogatepass')
            self.cursor = max(0, min(int(event.value(Qt.ImCursorPosition) or 0), len(raw)//2))
            self.anchor = max(0, min(int(event.value(Qt.ImAnchorPosition) or 0), len(raw)//2))
            if self.sensitive_input:
                # Only masks cross the QML boundary. No typing is persisted.
                self.editor_text = '\u2022' * (len(raw)//2)
                self.preview = '\u2022'*min(self.cursor, 20) + '\u2502' + '\u2022'*min(len(raw)//2-self.cursor, 20)
            elif self.terminal_input:
                self.editor_text, self.preview = '', 'Raw terminal input'
            else:
                self.editor_text = text
                left = raw[:2*self.cursor].decode('utf-16-le', errors='replace')[-45:]
                right = raw[2*self.cursor:].decode('utf-16-le', errors='replace')[:30]
                self.preview = left + '\u2502' + right
            self.label = str(self.prop(target, 'placeholderText', 'Text') or 'Text')[:64]
        else:
            self.preview = self.editor_text = ''
            self.cursor = self.anchor = 0
        if before != self._state(): self.changed.emit()
        if self.opened: QTimer.singleShot(0, self.avoid_editor)
        else: self._restore_avoidance()

    def _restore_avoidance(self):
        if self._avoidance_item is not None:
            try:
                item = self._avoidance_item
                item.setProperty('bottomMargin', self._saved_bottom_margin)
                maximum = max(0, float(item.property('contentHeight'))-item.height()+self._saved_bottom_margin)
                item.setProperty('contentY', min(float(item.property('contentY')), maximum))
            except RuntimeError: pass
        self._avoidance_item = None

    def avoid_editor(self):
        """Scroll a covered editor in its closest Flickable; never move UI roots."""
        target = self.target
        if not self.opened or self.terminal_input or target is None: return
        try:
            if not hasattr(target, 'mapToScene') or target.window() is None: return
            window_bottom = target.window().height() - self.panel_height - 8
            item = target.parentItem()
            while item is not None:
                if item.property('contentY') is not None and item.property('contentHeight') is not None:
                    top = item.mapToScene(QPointF(0, 0)).y() + 6
                    bottom = min(window_bottom, top + item.height() - 12)
                    if item.property('bottomMargin') is not None:
                        if self._avoidance_item is not item:
                            self._restore_avoidance()
                            self._avoidance_item, self._saved_bottom_margin = item, float(item.property('bottomMargin'))
                        # Extra scroll extent reveals even the final field when
                        # its viewport has not already reserved keyboard space.
                        reserve = max(0, top+item.height()-window_bottom)
                        item.setProperty('bottomMargin', max(self._saved_bottom_margin, reserve))
                    field_top = target.mapToScene(QPointF(0, 0)).y()
                    field_bottom = field_top + target.height()
                    delta = field_bottom-bottom if field_bottom > bottom else min(0, field_top-top)
                    maximum = max(0, float(item.property('contentHeight'))-item.height()+float(item.property('bottomMargin') or 0))
                    if delta: item.setProperty('contentY', max(0, min(maximum, float(item.property('contentY'))+delta)))
                    break
                item = item.parentItem()
        except (RuntimeError, TypeError, AttributeError): pass

    def _send(self, target, event):
        if target is None: return
        self._sending = True
        try: QCoreApplication.sendEvent(target, event)
        except RuntimeError: pass
        finally: self._sending = False

    def _preedit(self):
        attributes = []
        if self.composition:
            style = QTextCharFormat(); style.setFontUnderline(True)
            attributes = [QInputMethodEvent.Attribute(QInputMethodEvent.TextFormat, 0, len(self.composition), style),
                          QInputMethodEvent.Attribute(QInputMethodEvent.Cursor, self.composition_cursor, 1, None)]
        self._send(self.target, QInputMethodEvent(self.composition, attributes))

    def _clear_composition(self, target=None):
        had_composition = bool(self.composition)
        self.composition, self.composition_cursor = '', 0
        self.candidate_items, self.candidate_page = (), 0
        if had_composition: self._send(target or self.target, QInputMethodEvent())

    def _update_candidates(self):
        self.candidate_items = self.engine.lookup(self.composition)
        self.candidate_page = 0
        self._preedit(); self.changed.emit()

    def _literal(self, text):
        target, _ = self.query()
        if target is None: return
        event = QInputMethodEvent(); event.setCommitString(text)
        self._send(target, event)

    def _finish_composition(self, literal=False):
        while self.composition:
            if literal or not self.candidate_items:
                text = self.composition
                self._clear_composition(); self._literal(text)
                break
            self.chooseCandidate(0)

    def _handle_physical(self, event):
        if event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            if self.composition: self._finish_composition()
            return False
        names = {Qt.Key_Backspace:'backspace', Qt.Key_Delete:'delete', Qt.Key_Left:'left',
                 Qt.Key_Right:'right', Qt.Key_Home:'home', Qt.Key_End:'end',
                 Qt.Key_Return:'enter', Qt.Key_Enter:'enter', Qt.Key_Escape:'escape'}
        if event.key() in names and self.composition:
            self.key(names[event.key()]); return True
        text = event.text()
        if text and (text.isascii() and text.isalpha() or text == "'" and self.composition):
            self.commit(text); return True
        if self.composition and text and text.isprintable():
            self.commit(text); return True
        if text in (',', '.', '!', '?', ';', ':'):
            self.commit(text); return True
        return False

    def eventFilter(self, watched, event):
        if self._sending: return False
        if event.type() == QEvent.MouseButtonRelease and isinstance(watched, QWindow):
            if not self.opened or event.localPos().y() < watched.height()-self.panel_height:
                def revisit():
                    target, _ = self.query()
                    if target is not None: self.dismissed = False
                    self.refresh()
                QTimer.singleShot(0, revisit)
        if event.type() in (QEvent.ShortcutOverride, QEvent.KeyPress, QEvent.KeyRelease):
            target, _ = self.query()
            if target is not self.target: self.refresh()
            if target is None or not self.pinyinEnabled or self.language != 'zh' or self.layout != 'letters': return False
            if watched is not target and not isinstance(watched, QWindow): return False
            if event.type() == QEvent.ShortcutOverride:
                if self.composition and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
                    event.accept(); return True
            elif event.type() == QEvent.KeyRelease:
                if event.key() in self._consumed_keys:
                    self._consumed_keys.discard(event.key()); return True
            elif self._handle_physical(event):
                self._consumed_keys.add(event.key()); return True
        return False

    @pyqtProperty(bool, notify=changed)
    def isOpen(self): return self.opened
    @pyqtProperty(bool, notify=changed)
    def editing(self): return self.active
    @pyqtProperty(str, notify=changed)
    def mode(self): return self.layout
    @pyqtProperty(int, notify=changed)
    def shiftState(self): return self.shift
    @pyqtProperty(str, notify=changed)
    def previewText(self): return self.preview
    @pyqtProperty(str, notify=changed)
    def editorText(self): return self.editor_text
    @pyqtProperty(int, notify=changed)
    def cursorPosition(self): return self.cursor
    @pyqtProperty(str, notify=changed)
    def fieldLabel(self): return self.label
    @pyqtProperty(bool, notify=changed)
    def terminal(self): return self.terminal_input
    @pyqtProperty(bool, notify=changed)
    def sensitive(self): return self.sensitive_input
    @pyqtProperty(bool, notify=changed)
    def pinyinEnabled(self): return self.pinyin_allowed and self.engine.available
    @pyqtProperty(str, notify=changed)
    def inputLanguage(self): return self.language
    @pyqtProperty(str, notify=changed)
    def preeditText(self): return self.composition
    @pyqtProperty(int, notify=changed)
    def preeditCursor(self): return self.composition_cursor
    @pyqtProperty('QVariantList', notify=changed)
    def candidates(self):
        start = self.candidate_page*self.engine.PAGE_SIZE
        return [{'text': item.text, 'spelling':item.spelling, 'partial':item.consumed<len(self.composition)}
                for item in self.candidate_items[start:start+self.engine.PAGE_SIZE]]
    @pyqtProperty(int, notify=changed)
    def candidatePage(self): return self.candidate_page
    @pyqtProperty(bool, notify=changed)
    def hasNextCandidates(self): return (self.candidate_page+1)*self.engine.PAGE_SIZE < len(self.candidate_items)
    @pyqtProperty(str, notify=changed)
    def inputStatus(self):
        if self.terminal_input: return '终端 · 英文原始输入'
        if self.sensitive_input: return '密码 · 英文输入'
        if not self.engine.available: return '离线词库未安装 · 英文输入'
        return '中文拼音 · 空格选词 / 1–9选词' if self.language == 'zh' else '英文 · ABC / 数字 / 符号'

    @pyqtSlot(str)
    def commit(self, text):
        self.refresh()
        if self.target is None or not text: return
        chinese = self.pinyinEnabled and self.language == 'zh' and self.layout == 'letters'
        if chinese and all(character.isascii() and character.isalpha() for character in text):
            if len(self.composition)+len(text) <= self.engine.MAX_INPUT:
                at = self.composition_cursor
                self.composition = self.composition[:at]+text.lower()+self.composition[at:]
                self.composition_cursor += len(text)
                self._update_candidates()
            return
        if chinese and text == "'" and self.composition:
            at = self.composition_cursor
            if at and self.composition[at-1] != "'" and len(self.composition) < self.engine.MAX_INPUT:
                self.composition = self.composition[:at]+"'"+self.composition[at:]
                self.composition_cursor += 1; self._update_candidates()
            return
        if self.composition and text == ' ':
            if self.candidate_items: self.chooseCandidate(0)
            else: self._finish_composition(literal=True)
            return
        if self.composition and len(text) == 1 and text in '123456789':
            self.chooseCandidate(int(text)-1); return
        if self.composition: self._finish_composition()
        if chinese: text = {',':'，', '.':'。', '?':'？', '!':'！', ':':'：', ';':'；'}.get(text, text)
        self._literal(text)
        if self.shift == 1: self.shift = 0
        self.refresh()

    @pyqtSlot(int)
    def chooseCandidate(self, index):
        absolute = self.candidate_page*self.engine.PAGE_SIZE+index
        if not 0 <= index < self.engine.PAGE_SIZE or absolute >= len(self.candidate_items): return
        target, _ = self.query()
        if target is None or target is not self.target or not self.pinyin_allowed:
            self.refresh(); return
        item = self.candidate_items[absolute]
        remaining = self.composition[item.consumed:]
        self._clear_composition(); self._literal(item.text)
        self.composition, self.composition_cursor = remaining, len(remaining)
        self._update_candidates(); self.refresh()

    @pyqtSlot(int)
    def pageCandidates(self, direction):
        pages = max(1, (len(self.candidate_items)+self.engine.PAGE_SIZE-1)//self.engine.PAGE_SIZE)
        self.candidate_page = max(0, min(pages-1, self.candidate_page+direction))
        self.changed.emit()

    @pyqtSlot(int)
    def setCursorPosition(self, position):
        self.refresh()
        if self.target is None or self.terminal_input: return
        old_cursor, old_length = self.cursor, len(self.editor_text.encode('utf-16-le'))//2
        self._finish_composition(); self.refresh()
        length = len(self.editor_text.encode('utf-16-le'))//2
        if position > old_cursor: position += length-old_length
        try: self.target.setProperty('cursorPosition', max(0, min(position, length)))
        except RuntimeError: return
        self.refresh()

    @pyqtSlot(str)
    def key(self, name):
        self.refresh()
        target = self.target
        if target is None: return
        if self.composition:
            at = self.composition_cursor
            if name in ('backspace', 'delete'):
                if name == 'backspace' and at:
                    self.composition = self.composition[:at-1]+self.composition[at:]; self.composition_cursor -= 1
                elif name == 'delete' and at < len(self.composition):
                    self.composition = self.composition[:at]+self.composition[at+1:]
                self._update_candidates(); return
            if name in ('left', 'right', 'home', 'end'):
                self.composition_cursor = (0 if name == 'home' else len(self.composition) if name == 'end'
                    else max(0, min(len(self.composition), at+(-1 if name == 'left' else 1))))
                self._preedit(); self.changed.emit(); return
            if name == 'escape': self._clear_composition(); self.changed.emit(); return
            # First Enter confirms the word; it never submits the form too.
            if name == 'enter': self._finish_composition(); self.refresh(); return
            self._finish_composition()
        if name == 'newline': self._literal('\n'); self.refresh(); return
        keys = {'backspace':Qt.Key_Backspace, 'delete':Qt.Key_Delete, 'left':Qt.Key_Left,
                'right':Qt.Key_Right, 'home':Qt.Key_Home, 'end':Qt.Key_End,
                'enter':Qt.Key_Return, 'selectAll':Qt.Key_A, 'interrupt':Qt.Key_C,
                'escape':Qt.Key_Escape, 'tab':Qt.Key_Tab}
        if name not in keys: return
        modifiers = Qt.ControlModifier if name in ('selectAll','interrupt') else Qt.NoModifier
        for kind in (QEvent.KeyPress, QEvent.KeyRelease): self._send(target, QKeyEvent(kind, keys[name], modifiers))
        self.refresh()

    @pyqtSlot(str)
    def setMode(self, mode):
        if mode in ('letters', 'numbers', 'symbols'):
            self._finish_composition(); self.layout = mode; self.changed.emit()

    @pyqtSlot(str)
    def setLanguage(self, language):
        self.refresh()
        if language not in ('en','zh') or language == 'zh' and not self.pinyinEnabled: return
        self._finish_composition()
        self.language = self.preferred_language = language
        self.layout, self.shift = 'letters', 0
        self.changed.emit()

    @pyqtSlot()
    def toggleShift(self):
        if self.language == 'zh' and self.pinyinEnabled: self.commit("'"); return
        self.shift = (self.shift+1)%3; self.changed.emit()

    @pyqtSlot()
    def hide(self):
        self._finish_composition(); self.dismissed = True; self.refresh()

    @pyqtSlot()
    def done(self):
        self._finish_composition()
        target, _ = self.query()
        self.dismissed = True
        if target is not None:
            try: target.setProperty('focus', False)
            except RuntimeError: pass
        self.refresh()
