"""Navigation and local display preferences for the DaOS camera shell.

These checks use no camera authority, image buffer or capture request. Optional
Qt checks exercise the binding adapter without creating a display or VM.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    'hf_device_shell_model', ROOT/'ui/on-device/shell_model.py')
module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = module
SPEC.loader.exec_module(module)
ShellState = module.ShellState
try:
    import PyQt5.QtCore  # The adapter is optional; no GUI/application is created.
    QT_AVAILABLE = True
except ImportError:
    QT_AVAILABLE = False


DEFAULTS = {'grid_enabled': False, 'theme': 'light', 'preview_brightness': 100}


def stored(preferences=None):
    return json.dumps({'schema_version': 1,
                       'preferences': dict(DEFAULTS if preferences is None else preferences)}).encode()


class ShellNavigationTests(unittest.TestCase):
    def test_navigation_back_and_same_route_do_not_create_duplicate_history(self):
        state = ShellState()
        self.assertEqual('camera', state.current_app)
        self.assertFalse(state.back())
        self.assertTrue(state.navigate('photos'))
        self.assertTrue(state.navigate('photos'))
        self.assertTrue(state.navigate('recipes'))
        self.assertTrue(state.back())
        self.assertEqual('photos', state.current_app)
        self.assertTrue(state.back())
        self.assertEqual('camera', state.current_app)
        self.assertFalse(state.back())

    def test_unknown_and_disabled_apps_keep_current_route(self):
        state = ShellState(available_apps=('camera', 'settings'))
        self.assertFalse(state.navigate('photos'))
        self.assertEqual('camera', state.current_app)
        self.assertTrue(state.error_text)
        self.assertFalse(state.navigate('__not_a_camera_app__'))
        self.assertEqual('camera', state.current_app)
        self.assertTrue(state.navigate('settings'))
        self.assertEqual('settings', state.current_app)
        photo = next(item for item in state.apps if item['id'] == 'photos')
        self.assertFalse(photo['enabled'])
        self.assertTrue(photo['reason'])

    def test_selecting_settings_group_navigates_without_saving_camera_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'ui'/'shell-preferences.json'
            state = ShellState(path)
            self.assertEqual('exposure', state.current_group)
            self.assertTrue(state.select_group('display'))
            self.assertEqual(('settings', 'display'), (state.current_app, state.current_group))
            self.assertFalse(state.select_group('sensor-register-editor'))
            self.assertEqual(('settings', 'display'), (state.current_app, state.current_group))
            self.assertTrue(state.back())
            self.assertEqual('camera', state.current_app)
            self.assertFalse(path.exists(), 'navigation must not persist camera/capture state')

    def test_menu_toggle_and_catalogue_copies_do_not_mutate_model(self):
        state = ShellState()
        self.assertTrue(state.menu_open)
        state.toggle_menu()
        self.assertFalse(state.menu_open)
        state.toggle_menu()
        self.assertTrue(state.menu_open)
        apps = state.apps
        expected_title = apps[0]['title']
        apps[0]['title'] = 'external mutation'
        apps.clear()
        self.assertEqual(expected_title, state.apps[0]['title'])
        groups = state.settings_groups
        groups[0]['id'] = 'foreign'
        self.assertEqual('exposure', state.settings_groups[0]['id'])

    def test_navigation_history_has_a_finite_bound(self):
        state = ShellState()
        for _ in range(100):
            state.navigate('photos')
            state.navigate('settings')
        count = 0
        while state.back():
            count += 1
            self.assertLessEqual(count, 64, 'local navigation history must remain bounded')


class ShellPreferenceTests(unittest.TestCase):
    def test_in_memory_preferences_are_explicitly_nonpersistent(self):
        state = ShellState()
        self.assertEqual(DEFAULTS, state.preferences)
        self.assertFalse(state.preferences_persistent)
        self.assertTrue(state.set_preference('theme', 'dark'))
        self.assertEqual('dark', state.preferences['theme'])
        copy = state.preferences
        copy['preview_brightness'] = 9000
        self.assertEqual(100, state.preferences['preview_brightness'])

    def test_display_preferences_persist_but_navigation_does_not(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'ui'/'shell-preferences.json'
            state = ShellState(path)
            self.assertTrue(state.preferences_persistent)
            self.assertTrue(state.set_preference('theme', 'dark'))
            self.assertTrue(state.set_preference('grid_enabled', True))
            self.assertTrue(state.set_preference('preview_brightness', 60))
            self.assertTrue(state.navigate('diagnostics'))
            document = json.loads(path.read_bytes())
            self.assertEqual({'schema_version', 'preferences'}, set(document))
            self.assertEqual({'grid_enabled', 'theme', 'preview_brightness'}, set(document['preferences']))
            self.assertNotIn('request_id', path.read_text(encoding='utf-8'))
            restored = ShellState(path)
            self.assertEqual(state.preferences, restored.preferences)
            self.assertEqual('camera', restored.current_app)

    def test_invalid_display_values_do_not_change_memory_or_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'shell-preferences.json'
            state = ShellState(path)
            self.assertTrue(state.set_preference('theme', 'dark'))
            before = path.read_bytes()
            invalid = [('theme', 'DaOS-ultra'), ('theme', None),
                       ('grid_enabled', 1), ('grid_enabled', 'true'),
                       ('preview_brightness', True), ('preview_brightness', 24),
                       ('preview_brightness', 101), ('preview_brightness', 50.0),
                       ('requested_exposure_us', 10000), ('sensor_power', True)]
            for key, value in invalid:
                with self.subTest(key=key, value=value):
                    self.assertFalse(state.set_preference(key, value))
                    self.assertEqual(before, path.read_bytes())
                    self.assertEqual('dark', state.preferences['theme'])
                    self.assertEqual(100, state.preferences['preview_brightness'])

    def test_replace_failure_is_not_acknowledged_or_applied(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'shell-preferences.json'
            state = ShellState(path)
            before = set(path.parent.iterdir())
            with patch.object(module.os, 'replace', side_effect=OSError('injected local storage failure')):
                self.assertFalse(state.set_preference('theme', 'dark'))
            self.assertEqual(DEFAULTS, state.preferences)
            self.assertFalse(path.exists())
            self.assertEqual(before, set(path.parent.iterdir()), 'failed write must not leave a temp file')
            self.assertTrue(state.error_text)

    def test_corrupt_file_is_preserved_until_explicit_bounded_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'shell-preferences.json'
            original = b'{ "theme": "unfinished'
            path.write_bytes(original)
            state = ShellState(path)
            self.assertEqual(DEFAULTS, state.preferences)
            self.assertTrue(state.error_text)
            self.assertFalse(state.set_preference('grid_enabled', True))
            self.assertEqual(original, path.read_bytes())
            self.assertTrue(state.reset_preferences())
            backups = list(path.parent.glob('shell-preferences-invalid-*.bak'))
            self.assertEqual(1, len(backups))
            self.assertEqual(original, backups[0].read_bytes())
            self.assertEqual(DEFAULTS, ShellState(path).preferences)
            self.assertTrue(state.set_preference('grid_enabled', True))

    def test_nonfinite_duplicate_and_unexpected_configuration_are_preserved(self):
        variants = [
            b'{"schema_version":true,"preferences":{"grid_enabled":false,"theme":"light","preview_brightness":100}}',
            b'{"schema_version":1,"preferences":{"grid_enabled":false,"theme":"light","theme":"dark","preview_brightness":100}}',
            b'{"schema_version":1,"preferences":{"grid_enabled":false,"theme":"light","preview_brightness":NaN}}',
            b'{"schema_version":1,"preferences":{"grid_enabled":false,"theme":"light","preview_brightness":100},"request_id":"foreign"}',
            b'{"schema_version":1,"preferences":{"grid_enabled":false,"theme":"light","preview_brightness":100,"sensor_power":true}}',
        ]
        with tempfile.TemporaryDirectory() as temporary:
            for index, original in enumerate(variants):
                with self.subTest(variant=index):
                    path = Path(temporary)/str(index)/'shell-preferences.json'
                    path.parent.mkdir()
                    path.write_bytes(original)
                    state = ShellState(path)
                    self.assertEqual(DEFAULTS, state.preferences)
                    self.assertTrue(state.error_text)
                    self.assertFalse(state.set_preference('theme', 'dark'))
                    self.assertEqual(original, path.read_bytes())

    def test_oversized_file_is_not_copied_overwritten_or_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'shell-preferences.json'
            original = b'x' * 8193
            path.write_bytes(original)
            state = ShellState(path)
            self.assertEqual(DEFAULTS, state.preferences)
            self.assertFalse(state.set_preference('theme', 'dark'))
            self.assertFalse(state.reset_preferences())
            self.assertEqual(original, path.read_bytes())
            self.assertEqual([], list(path.parent.glob('*.bak')))

    def test_concurrent_edit_is_not_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'shell-preferences.json'
            initial = ShellState(path)
            self.assertTrue(initial.set_preference('theme', 'dark'))
            state = ShellState(path)
            changed = stored({'grid_enabled': True, 'theme': 'light', 'preview_brightness': 80}) + b'\n\n'
            path.write_bytes(changed)
            self.assertFalse(state.set_preference('preview_brightness', 40))
            self.assertEqual(changed, path.read_bytes())
            self.assertEqual({'grid_enabled': False, 'theme': 'dark', 'preview_brightness': 100}, state.preferences)

    def test_new_file_appearing_after_load_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'shell-preferences.json'
            state = ShellState(path)
            original = stored({'grid_enabled': True, 'theme': 'dark', 'preview_brightness': 75})
            path.write_bytes(original)
            self.assertFalse(state.set_preference('theme', 'dark'))
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(DEFAULTS, state.preferences)

    def test_replaced_parent_directory_is_not_used_as_the_loaded_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            parent = directory/'ui'
            path = parent/'shell-preferences.json'
            state = ShellState(path)
            self.assertTrue(state.set_preference('theme', 'dark'))
            old_bytes = path.read_bytes()
            moved = directory/'preserved-ui'
            parent.rename(moved)
            parent.mkdir()
            foreign = stored({'grid_enabled': True, 'theme': 'light', 'preview_brightness': 75})
            path.write_bytes(foreign)
            self.assertFalse(state.set_preference('preview_brightness', 50))
            self.assertEqual(foreign, path.read_bytes())
            self.assertEqual(old_bytes, (moved/path.name).read_bytes())

    def test_symlink_state_is_not_followed_or_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory/'foreign.json'
            original = stored({'grid_enabled': True, 'theme': 'dark', 'preview_brightness': 65})
            target.write_bytes(original)
            path = directory/'ui'/'shell-preferences.json'
            path.parent.mkdir()
            try:
                path.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest('symbolic-link creation is not permitted on this host')
            state = ShellState(path)
            self.assertEqual(DEFAULTS, state.preferences)
            self.assertFalse(state.set_preference('theme', 'dark'))
            self.assertFalse(state.reset_preferences())
            self.assertTrue(path.is_symlink())
            self.assertEqual(original, target.read_bytes())

    def test_symlink_parent_is_not_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            target = directory/'foreign'
            target.mkdir()
            link = directory/'ui'
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest('directory symbolic-link creation is not permitted on this host')
            state = ShellState(link/'shell-preferences.json')
            self.assertFalse(state.set_preference('theme', 'dark'))
            self.assertFalse(state.reset_preferences())
            self.assertEqual([], list(target.iterdir()))


@unittest.skipUnless(QT_AVAILABLE and getattr(module, 'ShellController', None) is not None,
                     'PyQt5 shell adapter is optional on the pure-Python test host')
class ShellQtAdapterTests(unittest.TestCase):
    def test_qml_integral_brightness_is_normalized_only_at_the_bridge(self):
        controller = module.ShellController()
        self.assertTrue(controller.setPreference('preview_brightness', 75.0))
        self.assertEqual(75, controller.previewBrightness)
        for value in (74.5, float('nan'), float('inf'), True, '74'):
            with self.subTest(value=value):
                self.assertFalse(controller.setPreference('preview_brightness', value))
                self.assertEqual(75, controller.previewBrightness)
        core = ShellState()
        self.assertFalse(core.set_preference('preview_brightness', 75.0))
        self.assertEqual(100, core.preferences['preview_brightness'])

    def test_slots_expose_navigation_and_local_preferences_without_a_camera(self):
        controller = module.ShellController()
        events = []
        controller.changed.connect(lambda: events.append(True))
        self.assertEqual('camera', controller.currentApp)
        self.assertEqual('exposure', controller.currentGroup)
        self.assertEqual('light', controller.theme)
        self.assertEqual(100, controller.previewBrightness)
        self.assertFalse(controller.preferencesPersistent)
        controller.navigate('system')
        self.assertEqual('system', controller.currentApp)
        controller.back()
        self.assertEqual('camera', controller.currentApp)
        controller.selectGroup('display')
        self.assertEqual(('settings', 'display'), (controller.currentApp, controller.currentGroup))
        controller.setPreference('grid_enabled', True)
        controller.setPreference('theme', 'dark')
        self.assertTrue(controller.gridEnabled)
        self.assertEqual('dark', controller.theme)
        self.assertTrue(events)


if __name__ == '__main__':
    unittest.main()
