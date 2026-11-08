"""Local camera app behavior, portable recipe interoperability and safe exports."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from PyQt5.QtCore import QObject, pyqtSignal
except ImportError:
    raise unittest.SkipTest('optional PyQt5 runtime is not installed')

from daos.capability_runtime.recipes import FORMAT, load_recipe

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('hf_device_apps', ROOT/'ui/on-device/apps_controller.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReadOnlyCamera(QObject):
    changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.sensorMode = 'sim-small'
        self.analogueGain = 2.0
        self.exposureUs = 12500
        self.lastRequestId = 'aef2812c-0438-456b-a08c-edaa6f1082ac'
        self.captureState = 'unknown'
        self.connectionState = 'ready'
        self.backendLabel = 'HF-Sim - native C++ authority'
        self.dngPath = ''
        self.capture_calls = 0

    def shutter(self):
        self.capture_calls += 1
        raise AssertionError('apps may not take photographs')

    def acquire(self, *args, **kwargs):
        raise AssertionError('apps may not obtain camera control')


class DeviceAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.camera = ReadOnlyCamera()
        self.apps = module.CameraAppsController(self.camera, self.root/'camera')

    def tearDown(self):
        self.apps = self.camera = None
        self.temp.cleanup()

    def save(self, name='Exposure bracket', exposures='5000,10000,20000'):
        self.apps.saveBracketRecipe(name, exposures)
        self.assertEqual('', self.apps.errorText)
        self.assertEqual(1, len(self.apps.recipes))
        return self.apps.recipes[0]['id']

    def test_recipe_round_trip_uses_existing_portable_format_without_camera_calls(self):
        self.camera.sensorMode = 'sim-full'
        identity = self.save()
        path = self.root/'camera/apps/recipes'/(identity+'.recipe.json')
        recipe = load_recipe(path)
        self.assertEqual({'format', 'name', 'plan'}, set(recipe))
        self.assertEqual(FORMAT, recipe['format'])
        self.assertEqual([5000, 10000, 20000], recipe['plan']['exposures_us'])
        self.assertEqual('sim-full', recipe['plan']['sensor_mode'])
        self.assertEqual(2.0, recipe['plan']['requested_analogue_gain'])
        self.assertEqual(identity, self.apps.selectedRecipeId)
        self.assertEqual(recipe, json.loads(self.apps.selectedRecipeJson))
        self.assertEqual(0, self.camera.capture_calls)
        self.assertEqual('unknown', self.camera.captureState)
        self.assertNotIn(self.camera.lastRequestId, path.read_text(encoding='utf-8'))

    def test_invalid_recipe_inputs_do_not_create_files_or_modify_request(self):
        request = self.camera.lastRequestId
        for text in ('', '1', '99', '30000001', 'NaN', '1000,', '1e4', '-1000', '1000.0', '1000,'*101, '9'*4097):
            with self.subTest(exposures=text[:20]):
                self.apps.saveBracketRecipe('Bracket', text)
                self.assertTrue(self.apps.errorText)
                self.assertEqual([], self.apps.recipes)
        for name in ('', ' leading', 'trailing ', 'x'*81, 'bad\nname'):
            with self.subTest(name=name[:20]):
                self.apps.saveBracketRecipe(name, '1000,2000')
                self.assertTrue(self.apps.errorText)
        self.assertEqual([], list((self.root/'camera/apps/recipes').iterdir()))
        self.assertEqual(request, self.camera.lastRequestId)
        self.assertEqual(0, self.camera.capture_calls)

    def test_new_instance_loads_saved_recipe_and_copies_exposure_arrays(self):
        identity = self.save()
        returned = self.apps.recipes
        returned[0]['exposuresUs'].append(7777)
        self.assertEqual(3, len(self.apps.recipes[0]['exposuresUs']))
        second = module.CameraAppsController(self.camera, self.root/'camera')
        self.assertEqual(identity, second.recipes[0]['id'])
        second.selectRecipe(identity)
        self.assertEqual(self.apps.selectedRecipeJson, second.selectedRecipeJson)

    def test_recipe_export_is_new_file_and_never_overwrites(self):
        self.save()
        destination = Path(self.apps.exportDirectory)/'bracket.json'
        self.apps.exportRecipe(str(destination))
        original = destination.read_bytes()
        self.assertEqual('', self.apps.errorText)
        self.assertEqual(json.loads(self.apps.selectedRecipeJson), load_recipe(destination))
        self.apps.exportRecipe(str(destination))
        self.assertIn('already exists', self.apps.errorText)
        self.assertEqual(original, destination.read_bytes())
        self.assertEqual(0, self.camera.capture_calls)

    def test_notices_keep_their_app_scope_during_read_only_refresh(self):
        identity = self.save()
        self.assertEqual('recipes', self.apps.messageScope)
        self.apps.selectRecipe(identity)
        self.assertEqual('recipes', self.apps.messageScope)
        self.apps.exportRecipe(str(Path(self.apps.exportDirectory)/'recipe.json'))
        self.assertEqual('recipes', self.apps.messageScope)
        recipe_notice = self.apps.message
        self.apps.refreshDiagnostics()
        self.assertEqual('recipes', self.apps.messageScope)
        self.assertEqual(recipe_notice, self.apps.message)
        self.apps.exportDiagnostics(str(Path(self.apps.exportDirectory)/'diagnostics.json'))
        self.assertEqual('diagnostics', self.apps.messageScope)
        self.assertIn('diagnostic snapshot exported', self.apps.message)
        self.apps.refreshRecipes()
        self.assertEqual('recipes', self.apps.messageScope)
        self.assertEqual(0, self.camera.capture_calls)

    def test_failed_exports_and_recipe_validation_scope_their_errors(self):
        self.apps.saveBracketRecipe('Invalid', '99')
        self.assertEqual('recipes', self.apps.messageScope)
        self.assertTrue(self.apps.errorText)
        self.apps.exportDiagnostics('relative.json')
        self.assertEqual('diagnostics', self.apps.messageScope)
        self.assertTrue(self.apps.errorText)
        self.apps.selectRecipe('invalid')
        self.assertEqual('recipes', self.apps.messageScope)
        self.assertTrue(self.apps.errorText)
        self.assertEqual(0, self.camera.capture_calls)

    def test_invalid_export_destinations_are_rejected_without_creating_parents(self):
        self.save()
        missing = self.root/'not-created'/'bracket.json'
        for destination in ('relative.json', str(missing), str(self.root/'invalid.txt'),
                            str(self.root/'../escape.json'), str(self.root/'bad\nname.json')):
            with self.subTest(destination=destination):
                self.apps.exportRecipe(destination)
                self.assertTrue(self.apps.errorText)
        self.assertFalse(missing.parent.exists())

    def test_invalid_id_and_malformed_files_do_not_replace_valid_selection(self):
        identity = self.save()
        directory = self.root/'camera/apps/recipes'
        (directory/('f'*32+'.recipe.json')).write_text('{invalid', encoding='utf-8')
        (directory/('e'*32+'.recipe.json')).write_bytes(b' '*65537)
        (directory/'unrelated.txt').write_text('keep me', encoding='utf-8')
        self.apps.refreshRecipes()
        self.assertEqual(1, len(self.apps.recipes))
        self.assertIn('Skipped 2', self.apps.message)
        self.apps.selectRecipe('../escape')
        self.assertTrue(self.apps.errorText)
        self.assertEqual(identity, self.apps.selectedRecipeId)
        self.assertEqual('keep me', (directory/'unrelated.txt').read_text())

    def test_diagnostics_export_preserves_unknown_request_and_requested_parameter_scope(self):
        path = Path(self.apps.exportDirectory)/'diagnostics.json'
        self.apps.exportDiagnostics(str(path))
        self.assertEqual('', self.apps.errorText)
        value = json.loads(path.read_text(encoding='utf-8'))
        self.assertTrue(value['synthetic'])
        self.assertFalse(value['physical_hardware_validated'])
        self.assertEqual(self.camera.lastRequestId, value['camera']['request_id'])
        self.assertEqual('unknown', value['camera']['capture_status'])
        self.assertEqual(12500, value['camera']['requested_exposure_us'])
        self.assertNotIn('actual_exposure_us', value['camera'])
        self.assertIn('requested UI state', value['camera']['parameter_scope'])
        self.assertEqual(0, self.camera.capture_calls)
        original = path.read_bytes()
        self.apps.exportDiagnostics(str(path))
        self.assertTrue(self.apps.errorText)
        self.assertEqual(original, path.read_bytes())

    def test_camera_change_refreshes_read_only_views_and_storage_is_measured(self):
        self.camera.exposureUs = 20000
        self.camera.captureState = 'complete'
        self.camera.changed.emit()
        values = {row['label']:row['value'] for row in self.apps.diagnosticRows}
        self.assertEqual('20000 us', values['Requested exposure'])
        self.assertEqual('complete', values['Capture outcome'])
        storage = {row['label']:row['value'] for row in self.apps.storageRows}
        self.assertTrue(storage['Data filesystem free'].endswith('GiB'))
        self.assertIn('untested', storage['Storage validation'])
        self.assertIn('Not connected', ' '.join(row['value'] for row in self.apps.deviceRows))
        self.assertEqual(0, self.camera.capture_calls)

    def test_failed_publication_keeps_no_final_recipe_or_temporary_file(self):
        with patch.object(module.os, 'link', side_effect=OSError('injected link failure')):
            self.apps.saveBracketRecipe('Bracket', '5000,10000')
        self.assertIn('injected', self.apps.errorText)
        self.assertEqual([], list((self.root/'camera/apps/recipes').iterdir()))
        self.assertEqual([], self.apps.recipes)

    def test_store_limit_is_explicit_and_existing_recipe_is_preserved(self):
        with patch.object(module, 'MAX_RECIPES', 1):
            identity = self.save()
            self.apps.saveBracketRecipe('Second', '1000,2000')
            self.assertTrue(self.apps.errorText)
            self.assertEqual(identity, self.apps.recipes[0]['id'])
            self.assertEqual(1, len(list((self.root/'camera/apps/recipes').glob('*.recipe.json'))))

    def test_unavailable_app_store_does_not_break_read_only_diagnostics(self):
        directory = self.root/'bad-camera'
        directory.mkdir()
        (directory/'apps').write_text('foreign file', encoding='utf-8')
        apps = module.CameraAppsController(self.camera, directory)
        self.assertTrue(apps.errorText)
        self.assertTrue(apps.diagnosticRows)
        apps.saveBracketRecipe('Bracket', '1000,2000')
        self.assertTrue(apps.errorText)
        self.assertEqual('foreign file', (directory/'apps').read_text())

    def test_symbolic_link_recipe_and_export_parent_are_refused(self):
        self.save()
        source = Path(self.apps.exportDirectory)/'foreign.json'
        source.write_text(self.apps.selectedRecipeJson, encoding='utf-8')
        directory = self.root/'camera/apps/recipes'
        link = directory/('f'*32+'.recipe.json')
        parent_link = self.root/'linked-exports'
        try:
            link.symlink_to(source)
            parent_link.symlink_to(Path(self.apps.exportDirectory), target_is_directory=True)
        except OSError as error:
            self.skipTest('symlink creation unavailable on this host: ' + str(error))
        self.apps.refreshRecipes()
        self.assertEqual(1, len(self.apps.recipes))
        self.assertIn('Skipped 1', self.apps.message)
        self.apps.exportRecipe(str(parent_link/'escape.json'))
        self.assertTrue(self.apps.errorText)
        self.assertFalse((Path(self.apps.exportDirectory)/'escape.json').exists())

    def test_nonfinite_fake_ui_gain_is_not_exported_as_nonfinite_json(self):
        self.camera.analogueGain = float('nan')
        path = Path(self.apps.exportDirectory)/'diagnostics.json'
        self.apps.exportDiagnostics(str(path))
        self.assertEqual('', self.apps.errorText)
        value = json.loads(path.read_text(encoding='utf-8'))
        self.assertIsNone(value['camera']['requested_analogue_gain'])

    def test_network_export_spelling_is_rejected_before_filesystem_access(self):
        self.save()
        with patch.object(module, '_checked_directory', side_effect=AssertionError('must not access network')):
            for destination in ('\\\\unreachable-server\\share\\recipe.json', '//unreachable-server/share/recipe.json'):
                with self.subTest(destination=destination):
                    self.apps.exportRecipe(destination)
                    self.assertIn('network path', self.apps.errorText)


if __name__ == '__main__':
    unittest.main()
