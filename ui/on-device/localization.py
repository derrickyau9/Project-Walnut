"""Display-only English/Simplified Chinese localization for the native Qt shell.

Catalog lookup never rewrites identifiers, commands, file paths or user values.
QML uses qsTr for interface strings, so old preview loaders remain valid without
an i18n context; installing this translator enables the selected language.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import weakref

from PyQt5.QtCore import QObject, QTranslator, pyqtProperty, pyqtSignal, pyqtSlot

LANGUAGES = ('zh_CN', 'en_US')
DEFAULT_LANGUAGE = 'zh_CN'


def validate_language(value):
    if not isinstance(value, str) or value not in LANGUAGES:
        raise ValueError('Unsupported interface language')
    return value


def saved_language(state_directory):
    path = Path(state_directory) / 'system-preferences.json'
    try:
        if path.is_symlink() or path.stat().st_size > 16384:
            return DEFAULT_LANGUAGE
        return validate_language(json.loads(path.read_text(encoding='utf-8'))['language'])
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_LANGUAGE


def _catalogs():
    source = Path(__file__).with_name('locales')
    result = {}
    for language in LANGUAGES:
        path = source / (language + '.json')
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
            raise ValueError('Invalid interface translation catalog')
        result[language] = value
    return result


class Localization(QObject):
    changed = pyqtSignal()

    def __init__(self, application, language=DEFAULT_LANGUAGE):
        super().__init__(application)
        self._language = validate_language(language)
        self._revision = 0
        self._catalogs = _catalogs()
        self._engines = []
        self._application=application
        self._translators={}
        for code in LANGUAGES:
            translator=QTranslator(self)
            if not translator.load(str(Path(__file__).with_name('locales')/(code+'.qm'))):
                raise ValueError('Compiled interface translation catalog is missing: '+code)
            self._translators[code]=translator
        self._translator = self._translators[self._language]
        application.installTranslator(self._translator)

    @property
    def catalog(self):
        return self._catalogs[self._language]

    @pyqtProperty(str, notify=changed)
    def language(self):
        return self._language

    @pyqtProperty(int, notify=changed)
    def revision(self):
        return self._revision

    @pyqtSlot(str)
    def setLanguage(self, language):
        value = validate_language(language)
        if value == self._language:
            return
        self._application.removeTranslator(self._translator)
        self._language = value
        self._translator=self._translators[value]
        self._application.installTranslator(self._translator)
        self._revision += 1
        self.changed.emit()
        for engine in tuple(self._engines):
            try:engine.retranslate()
            except RuntimeError:self._engines.remove(engine)

    @pyqtSlot(str, result=str)
    @pyqtSlot(str, int, result=str)
    def t(self, source, revision=0):
        if source in self.catalog:
            return self.catalog[source]
        # Only these controller-owned status formats contain variable data.
        # Keep the opaque path unchanged and accept numeric counts only.
        prefix = '配置已导出：'
        if source.startswith(prefix):
            return self.catalog.get(prefix, prefix) + source[len(prefix):]
        match = re.fullmatch(r'资料库已同步 · ([0-9]+) 张', source)
        if match:
            return self.catalog.get('资料库已同步 · {count} 张', '资料库已同步 · {count} 张').format(count=match[1])
        match = re.fullmatch(r'完成 · 跳过 ([0-9]+) 项', source)
        if match:
            return self.catalog.get('完成 · 跳过 {count} 项', '完成 · 跳过 {count} 项').format(count=match[1])
        return source

    def bindEngine(self, engine):
        """Bind before load; accepts QQmlApplicationEngine or QQuickView.engine()."""
        engine.rootContext().setContextProperty('i18n', self)
        if engine not in self._engines:
            self._engines.append(engine)
            reference=weakref.ref(engine)
            def discard(*_):
                value=reference()
                if value in self._engines:self._engines.remove(value)
            engine.destroyed.connect(discard)

    def bindSystemController(self, controller):
        def refresh():
            self.setLanguage(controller.preferences['language'])
        refresh()
        controller.changed.connect(refresh)


def install_localization(application, language=DEFAULT_LANGUAGE):
    """Return one retained translator per app; callers keep this object alive."""
    current = getattr(application, '_daos_localization', None)
    if current is None:
        current = Localization(application, language)
        application._daos_localization = current
    else:
        current.setLanguage(language)
    return current
