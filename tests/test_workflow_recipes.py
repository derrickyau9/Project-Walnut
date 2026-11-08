"""Portable recipe boundaries and actual offline workflow execution."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from daos.capability_runtime.bracket import WorkflowError, normalize_plan
from daos.capability_runtime.recipes import (
    FORMAT, MAX_RECIPE_BYTES, RecipeError, normalize_recipe, loads_recipe,
    load_recipe, dumps_recipe, save_recipe,
)

ROOT = Path(__file__).resolve().parents[1]


class RecipeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        self.recipe = {'format': FORMAT, 'name': 'Portable bracket',
                       'plan': {'exposures_us': [5000, 10000]}}

    def cli(self, *arguments, data=None, timeout=30):
        command = [sys.executable, '-B', '-m', 'daos.capability_runtime',
                   '--data-dir', str(data or self.root / 'camera-data'), *arguments]
        return subprocess.run(command, text=True, capture_output=True, timeout=timeout)

    def test_all_kinds_round_trip_defaults_and_independent_copies(self):
        for kind in ('bracket', 'interval', 'dark', 'flat'):
            value = copy.deepcopy(self.recipe)
            value['plan']['kind'] = kind
            if kind == 'interval':
                value['plan']['interval_ms'] = 0
            normalized = normalize_recipe(value)
            decoded = loads_recipe(dumps_recipe(value))
            self.assertEqual(normalized, decoded)
            self.assertEqual('sim-small', decoded['plan']['sensor_mode'])
            self.assertEqual(1.0, decoded['plan']['requested_analogue_gain'])
            self.assertEqual('dng', decoded['plan']['output_format'])
            self.assertEqual(kind != 'bracket', 'kind' in decoded['plan'])
            value['plan']['exposures_us'][0] = 300
            self.assertEqual(5000, decoded['plan']['exposures_us'][0])

    def test_pure_plan_normalizer_preserves_legacy_shape_and_strict_boundary(self):
        source = {'exposures_us': [5000], 'kind': 'bracket', 'legacy_extra': 'ignored'}
        normalized = normalize_plan(source)
        self.assertEqual({'exposures_us': [5000], 'sensor_mode': 'sim-small',
                          'requested_analogue_gain': 1.0, 'output_format': 'dng'}, normalized)
        source['exposures_us'][0] = 10000
        self.assertEqual([5000], normalized['exposures_us'])
        with self.assertRaises(WorkflowError):
            normalize_plan(source, strict=True)
        with self.assertRaises(WorkflowError):
            normalize_plan({'exposures_us': [5000.0]})

    def test_integral_json_numbers_normalize_consistently_with_browser(self):
        text = ('{"format":"' + FORMAT + '","name":"Interval",'
                '"plan":{"kind":"interval","exposures_us":[5e3,10000.0],"interval_ms":0.0}}')
        normalized = loads_recipe(text)
        self.assertEqual([5000, 10000], normalized['plan']['exposures_us'])
        self.assertTrue(all(type(value) is int for value in normalized['plan']['exposures_us']))
        self.assertIs(type(normalized['plan']['interval_ms']), int)

    def test_rejects_credentials_jobs_paths_results_and_unknown_fields(self):
        for field in ('token', 'control_epoch', 'job_id', 'request_id', 'file_path', 'result', 'unknown'):
            for location in ('recipe', 'plan'):
                value = copy.deepcopy(self.recipe)
                target = value if location == 'recipe' else value['plan']
                target[field] = 'private-data-must-not-be-exported'
                with self.subTest(field=field, location=location):
                    with self.assertRaises(RecipeError):
                        dumps_recipe(value)
        value = copy.deepcopy(self.recipe)
        value['format'] = 'hasslefree.workflow-recipe.v2'
        with self.assertRaises(RecipeError):
            normalize_recipe(value)

    def test_json_is_bounded_duplicate_free_and_finite(self):
        raw = dumps_recipe(self.recipe)
        cases = ['{', '[]', raw + '{}', ' ' * (MAX_RECIPE_BYTES + 1),
                 '{"format":"x","format":"y","name":"a","plan":{}}',
                 '{"format":"' + FORMAT + '","name":"a","plan":{"exposures_us":[5000],"exposures_us":[10000]}}',
                 raw.replace('5000', 'NaN'), raw.replace('5000', 'Infinity'),
                 raw.replace('5000', '-Infinity'), b'\xff', '\ufeff' + raw,
                 '{"n":' + '9' * 5000 + '}']
        for value in cases:
            with self.subTest(prefix=repr(value)[:45]):
                with self.assertRaises(RecipeError):
                    loads_recipe(value)
        with self.assertRaises(RecipeError):
            loads_recipe(1)

    def test_name_and_numeric_plan_validation(self):
        for name in ('', ' leading', 'trailing ', 'a' * 81, 'line\nbreak', '\0', '\ud800', '\u00a0'):
            with self.subTest(name=repr(name)):
                with self.assertRaises(RecipeError):
                    normalize_recipe({**self.recipe, 'name': name})
        for changes in ({'exposures_us': []}, {'exposures_us': [True]}, {'exposures_us': [5000.5]},
                        {'exposures_us': [99]}, {'exposures_us': [30000001]}, {'exposures_us': [5000] * 101},
                        {'sensor_mode': 'hardware'}, {'sensor_mode': []}, {'kind': []},
                        {'requested_analogue_gain': True}, {'requested_analogue_gain': float('nan')},
                        {'requested_analogue_gain': 17}, {'output_format': 'jpeg'},
                        {'interval_ms': 1000}, {'kind': 'interval', 'interval_ms': True},
                        {'kind': 'interval', 'interval_ms': 86400001}):
            value = copy.deepcopy(self.recipe)
            value['plan'].update(changes)
            with self.subTest(fields=tuple(changes)):
                with self.assertRaises(RecipeError):
                    normalize_recipe(value)
        # Printable Unicode labels survive a UTF-8 portable file unchanged.
        unicode_name = '\u4e09\u5f20\u66dd\u5149'
        self.assertEqual(unicode_name, loads_recipe(dumps_recipe({**self.recipe, 'name': unicode_name}))['name'])

    def test_file_round_trip_create_only_concurrent_publication(self):
        target = self.root / 'recipes' / 'bracket.json'
        path = save_recipe(self.recipe, target)
        self.assertEqual(normalize_recipe(self.recipe), load_recipe(path))
        before = path.read_bytes()
        with self.assertRaises(RecipeError):
            save_recipe({**self.recipe, 'name': 'Other'}, target)
        self.assertEqual(before, target.read_bytes())
        race_target = self.root / 'race.json'
        def save():
            try:
                save_recipe(self.recipe, race_target)
                return 'created'
            except RecipeError:
                return 'refused'
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda unused: save(), range(2)))
        self.assertCountEqual(['created', 'refused'], results)
        self.assertEqual(normalize_recipe(self.recipe), load_recipe(race_target))
        self.assertEqual([], list(self.root.rglob('.hf-recipe-*.tmp')))

    def test_invalid_export_or_publication_failure_has_no_partial_output(self):
        destination = self.root / 'not-created' / 'invalid.json'
        with self.assertRaises(RecipeError):
            save_recipe({**self.recipe, 'token': 'private'}, destination)
        self.assertFalse(destination.parent.exists())
        destination = self.root / 'failure.json'
        with patch('daos.capability_runtime.recipes.os.link', side_effect=PermissionError('injected')):
            with self.assertRaises(RecipeError):
                save_recipe(self.recipe, destination)
        self.assertFalse(destination.exists())
        self.assertEqual([], list(self.root.glob('.hf-recipe-*.tmp')))
        with self.assertRaises(RecipeError):
            load_recipe(self.root)
        with self.assertRaises(RecipeError):
            save_recipe(self.recipe, None)

    def test_cli_export_is_camera_free_and_recipe_run_reconciles_same_job(self):
        target = self.root / 'bracket.json'
        exported = self.cli('recipe-export', '--name', 'CLI bracket', '--output', str(target),
                            '--exposures-us', '5000', '10000')
        self.assertEqual(0, exported.returncode, exported.stderr)
        self.assertFalse((self.root / 'camera-data').exists())
        portable = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual({'format', 'name', 'plan'}, set(portable))
        executed = self.cli('run', '--job', 'recipe-job', '--recipe', str(target))
        self.assertEqual(0, executed.returncode, executed.stderr + executed.stdout)
        result = json.loads(executed.stdout)
        self.assertEqual('completed', result['status'])
        ids = [step['request_id'] for step in result['steps']]
        self.assertEqual(2, len(set(ids)))
        self.assertEqual([5000, 10000], [step['result']['actual_exposure_us'] for step in result['steps']])
        again = self.cli('run', '--job', 'recipe-job', '--recipe', str(target))
        self.assertEqual(0, again.returncode, again.stderr)
        self.assertEqual(ids, [step['request_id'] for step in json.loads(again.stdout)['steps']])
        self.assertEqual(2, len(list((self.root / 'camera-data/simulator/photos').glob('*.dng'))))
        fresh = self.cli('run', '--job', 'deliberate-new-job', '--recipe', str(target))
        self.assertEqual(0, fresh.returncode, fresh.stderr)
        fresh_ids = [step['request_id'] for step in json.loads(fresh.stdout)['steps']]
        self.assertTrue(set(ids).isdisjoint(fresh_ids))

    def test_cli_recipe_overrides_malformed_input_and_overwrite_are_refused_before_camera(self):
        target = save_recipe(self.recipe, self.root / 'recipe.json')
        for override in (['--gain', '2'], ['--mode', 'sim-full'], ['--kind', 'dark'],
                         ['--interval-ms', '100'], ['--exposures-us', '20000']):
            completed = self.cli('run', '--job', 'must-not-submit', '--recipe', str(target), *override)
            self.assertEqual(2, completed.returncode, completed.stderr)
            self.assertFalse((self.root / 'camera-data').exists())
        target.write_text('{', encoding='utf-8')
        completed = self.cli('run', '--job', 'must-not-submit', '--recipe', str(target))
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertFalse((self.root / 'camera-data').exists())
        before = target.read_bytes()
        completed = self.cli('recipe-export', '--name', 'Do not overwrite', '--output', str(target),
                             '--exposures-us', '5000')
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertEqual(before, target.read_bytes())

    def test_cli_dark_flat_and_interval_recipes_execute_the_saved_plan(self):
        for kind in ('dark', 'flat', 'interval'):
            value = copy.deepcopy(self.recipe)
            value['plan']['kind'] = kind
            value['plan']['exposures_us'] = [5000]
            if kind == 'interval': value['plan']['interval_ms'] = 0
            target = save_recipe(value, self.root / (kind + '.json'))
            executed = self.cli('run', '--job', kind + '-recipe', '--recipe', str(target), data=self.root / kind)
            self.assertEqual(0, executed.returncode, executed.stderr + executed.stdout)
            result = json.loads(executed.stdout)
            self.assertEqual('completed', result['status'])
            self.assertEqual('scene' if kind == 'interval' else kind, result['steps'][0]['result']['simulation_scene'])

    def test_native_recipe_run_uses_copied_authority_when_available(self):
        source = os.environ.get('HF_NATIVE_DAEMON')
        if source is None:
            for candidate in (ROOT / 'build/native/Release/hf-camerad.exe', ROOT / 'build/native/hf-camerad'):
                if candidate.is_file():
                    source = str(candidate)
                    break
        if source is None or not Path(source).is_file():
            self.skipTest('native authority is not built')
        native = self.root / ('hf-camerad.exe' if os.name == 'nt' else 'hf-camerad')
        shutil.copyfile(source, native)
        if os.name != 'nt': native.chmod(0o700)
        target = save_recipe(self.recipe, self.root / 'native-recipe.json')
        completed = self.cli('--authority', 'native', '--native-daemon', str(native),
                             'run', '--job', 'native-recipe', '--recipe', str(target), data=self.root / 'native')
        self.assertEqual(0, completed.returncode, completed.stderr + completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual('completed', result['status'])
        self.assertEqual(2, len({step['request_id'] for step in result['steps']}))
        self.assertTrue(all(Path(step['result']['file_path']).is_file() for step in result['steps']))


if __name__ == '__main__':
    unittest.main()
