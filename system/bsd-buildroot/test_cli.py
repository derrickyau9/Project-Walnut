"""CLI orchestration regressions with no real build, download, or VM launch."""
from __future__ import annotations

import argparse
import contextlib
import io
import json
from pathlib import Path, PureWindowsPath
import sys
import tempfile
import unittest
from unittest import mock

import daos
from backends import netbsd


class CLIOrchestration(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='daos-cli-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def main_environment(self, command):
        calls = []
        backend = argparse.Namespace(
            _workspace=lambda value: Path(value),
            fetch=lambda *a, **k: calls.append('fetch') or {},
            configure=lambda *a, **k: calls.append('configure') or {},
            build_base=lambda *a, **k: calls.append('build:' + k['stage']) or {})
        image = argparse.Namespace(build=lambda *a, **k: calls.append('image') or {})
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(sys, 'argv', ['daos.py', *command, '--workspace', str(self.root / 'work')]))
        stack.enter_context(mock.patch.object(daos, 'profile', return_value=(self.root / 'profile.json', {'name': 'fixture'}, self.root / 'sources.lock.json')))
        stack.enter_context(mock.patch.object(daos, 'doctor', return_value={}))
        stack.enter_context(mock.patch.object(daos, 'module', side_effect=lambda name: backend if name == 'netbsd' else image))
        stack.enter_context(mock.patch.object(daos, 'install_packages', side_effect=lambda *a, **k: calls.append('packages') or {}))
        stack.enter_context(mock.patch.object(daos, 'validate_package_set', side_effect=lambda *a, **k: calls.append('validate-packages') or {}))
        stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        return calls

    def test_all_orders_every_prerequisite_before_image(self):
        calls = self.main_environment(['all'])
        daos.main()
        self.assertEqual(calls, ['fetch', 'configure', 'build:all', 'packages', 'validate-packages', 'image'])

    def test_image_only_validates_selection_without_staging_packages(self):
        calls = self.main_environment(['image'])
        daos.main()
        self.assertEqual(calls, ['validate-packages', 'image'])

    def test_all_rejects_partial_build_stage_before_starting_work(self):
        calls = self.main_environment(['all', '--stage', 'tools'])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises((ValueError, SystemExit)):
                daos.main()
        self.assertEqual(calls, [])

    def test_windows_explicit_paths_are_converted_before_wsl(self):
        argv = ['daos.py', 'plan', '--profile', r'F:\Project HasselFree\custom\profile.json',
                '--workspace', '/home/dy09/daos-test']
        with mock.patch.object(sys, 'argv', argv), \
             mock.patch.object(daos, 'os', argparse.Namespace(name='nt')), \
             mock.patch.object(daos, 'HERE', PureWindowsPath(r'F:\Project HasselFree\system\bsd-buildroot')), \
             mock.patch.object(daos, 'windows_storage', return_value={}), \
             mock.patch.object(daos.subprocess, 'call', return_value=0) as launch:
            with self.assertRaises(SystemExit):
                daos.main()
        command = launch.call_args.args[0]
        self.assertIn('/mnt/f/Project HasselFree/custom/profile.json', command)
        self.assertIn('/home/dy09/daos-test', command)

    def package_fixture(self):
        here = self.root / 'project/system/bsd-buildroot'
        (here / 'packages/daos-ui').mkdir(parents=True)
        (here / 'packages/daos-core').mkdir()
        (here / 'packages/daos-ui/runtime.lock.json').write_text('{}')
        workspace = self.root / 'workspace'
        workspace.mkdir()
        (workspace / 'state').mkdir()
        lock = self.root / 'sources.lock.json'
        lock.write_text(json.dumps({'upstream': {'release': '11.1'}}))
        return here, workspace, lock

    def test_fresh_ui_package_staging_fetches_runtime_first(self):
        here, workspace, lock = self.package_fixture()
        calls = []

        def run(command):
            words = list(map(str, command))
            if 'daos-ui' in words[1]:
                action = words[2]
                calls.append(action)
                cache = Path(words[words.index('--cache') + 1])
                if action == 'fetch':
                    cache.mkdir(exist_ok=True)
                    (cache / 'verified-fixture').write_text('runtime')
                elif action == 'stage':
                    if not (cache / 'verified-fixture').is_file():
                        raise FileNotFoundError('Fresh all did not fetch the UI runtime')
                    Path(words[words.index('--manifest') + 1]).write_text('{}')
            else:
                Path(words[words.index('--manifest') + 1]).write_text('{}')

        with mock.patch.object(daos, 'HERE', here), mock.patch.object(daos, 'PROJECT', here.parent.parent), \
             mock.patch.object(daos, 'package_inputs', return_value={'fixture': True}), \
             mock.patch.object(daos, 'module', side_effect=lambda name: netbsd if name == 'netbsd' else argparse.Namespace(_packages=lambda base: (base / 'packages/overlay', {}, []))), \
             mock.patch.object(daos, 'run', side_effect=run):
            result = daos.install_packages(self.root / 'profile.json',
                                           {'name': 'fixture', 'packages': ['daos-core', 'daos-ui']},
                                           lock, workspace, 12)
        self.assertEqual(calls, ['fetch', 'stage'])
        self.assertTrue((workspace / 'packages/package-set.json').is_file())
        self.assertFalse(result['target_tests_executed'])

    def test_cached_overlay_is_revalidated_and_not_rebuilt_silently(self):
        here, workspace, lock = self.package_fixture()
        output = workspace / 'packages'
        output.mkdir()
        (output / 'package-set.json').write_text(json.dumps({'inputs': {'same': True}}))
        checker = mock.Mock(side_effect=ValueError('tampered overlay'))
        image = argparse.Namespace(_packages=checker)
        with mock.patch.object(daos, 'HERE', here), \
             mock.patch.object(daos, 'package_inputs', return_value={'same': True}), \
             mock.patch.object(daos, 'module', side_effect=lambda name: netbsd if name == 'netbsd' else image), mock.patch.object(daos, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'tampered overlay'):
                daos.install_packages(self.root / 'profile.json', {'packages': []}, lock, workspace, 12)
            run.assert_not_called()
            checker.assert_called_once_with(workspace)

    def test_changed_inputs_do_not_archive_a_corrupt_old_overlay(self):
        here, workspace, lock = self.package_fixture()
        output = workspace / 'packages'
        output.mkdir()
        (output / 'package-set.json').write_text(json.dumps({'inputs': {'before': True}}))
        checker = mock.Mock(side_effect=ValueError('old payload is corrupt'))
        with mock.patch.object(daos, 'HERE', here), \
             mock.patch.object(daos, 'package_inputs', return_value={'after': True}), \
             mock.patch.object(daos, 'module', side_effect=lambda name: netbsd if name == 'netbsd' else argparse.Namespace(_packages=checker)), \
             mock.patch.object(daos, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'old payload is corrupt'):
                daos.install_packages(self.root / 'profile.json', {'packages': []}, lock, workspace, 12)
            run.assert_not_called()
        self.assertFalse((output / 'history').exists())
        self.assertEqual(daos.read(output / 'package-set.json')['inputs'], {'before': True})

    def test_changed_inputs_archive_verified_overlay_and_preserve_build_attempt(self):
        here, workspace, lock = self.package_fixture()
        output = workspace / 'packages'
        (output / 'overlay').mkdir(parents=True)
        (output / 'overlay/old.txt').write_text('old payload')
        (output / 'manifests').mkdir()
        (output / 'manifests/old.json').write_text('{}')
        original_attempt = output / 'attempts/original-build'
        original_attempt.mkdir(parents=True)
        (original_attempt / 'object.o').write_text('original build object')
        old_selection = {'format': 'DaOS-BSD-package-set-v1', 'inputs': {'before': True},
                         'attempt': str(original_attempt)}
        (output / 'package-set.json').write_text(json.dumps(old_selection))
        held = {'value': False}

        @contextlib.contextmanager
        def locked(base):
            self.assertFalse(held['value'])
            held['value'] = True
            try:
                yield
            finally:
                held['value'] = False

        def inputs(*args):
            self.assertTrue(held['value'])
            return {'after': True}

        def run(command):
            self.assertTrue(held['value'])
            words = list(map(str, command))
            Path(words[words.index('--manifest') + 1]).write_text('{}')
            (Path(words[words.index('--destdir') + 1]) / 'new.txt').write_text('new payload')

        image = argparse.Namespace(_packages=lambda base: (base / 'packages/overlay', {}, []))
        with mock.patch.object(daos, 'HERE', here), mock.patch.object(daos, 'package_inputs', side_effect=inputs), \
             mock.patch.object(netbsd, '_locked', side_effect=locked) as lock_context, \
             mock.patch.object(daos, 'module', side_effect=lambda name: netbsd if name == 'netbsd' else image), \
             mock.patch.object(daos, 'run', side_effect=run):
            result = daos.install_packages(self.root / 'profile.json',
                                           {'name': 'fixture', 'packages': ['daos-core']}, lock, workspace, 12)
        history = Path(result['previous_revision'])
        self.assertEqual((history / 'overlay/old.txt').read_text(), 'old payload')
        self.assertEqual(daos.read(history / 'package-set.json'), old_selection)
        self.assertEqual(daos.read(history / 'history.json')['status'], 'success')
        self.assertEqual((original_attempt / 'object.o').read_text(), 'original build object')
        self.assertEqual((output / 'overlay/new.txt').read_text(), 'new payload')
        self.assertEqual(daos.read(output / 'package-set.json')['inputs'], {'after': True})
        self.assertFalse(held['value'])
        lock_context.assert_called_once_with(workspace)

    def test_independent_image_rejects_stale_selection_without_staging(self):
        here, workspace, lock = self.package_fixture()
        output = workspace / 'packages'
        output.mkdir()
        (output / 'package-set.json').write_text(json.dumps({'inputs': {'before': True}}))
        image = argparse.Namespace(_packages=mock.Mock())
        with mock.patch.object(daos, 'package_inputs', return_value={'after': True}), \
             mock.patch.object(daos, 'module', side_effect=lambda name: netbsd if name == 'netbsd' else image), \
             mock.patch.object(daos, 'run') as run:
            with self.assertRaisesRegex(ValueError, 'run packages before image'):
                daos.validate_package_set(self.root / 'profile.json', {}, lock, workspace)
            image._packages.assert_not_called()
            run.assert_not_called()

    def test_failed_final_manifest_validation_moves_payload_back_to_attempt(self):
        here, workspace, lock = self.package_fixture()
        image = argparse.Namespace(_packages=mock.Mock(side_effect=ValueError('target loader guard failed')))

        def run(command):
            words = list(map(str, command))
            Path(words[words.index('--manifest') + 1]).write_text('{}')
            (Path(words[words.index('--destdir') + 1]) / 'payload.txt').write_text('preserve failed payload')

        with mock.patch.object(daos, 'HERE', here), mock.patch.object(daos, 'package_inputs', return_value={'same': True}), \
             mock.patch.object(daos, 'module', side_effect=lambda name: netbsd if name == 'netbsd' else image), \
             mock.patch.object(daos, 'run', side_effect=run):
            with self.assertRaisesRegex(ValueError, 'target loader guard failed'):
                daos.install_packages(self.root / 'profile.json', {'name': 'fixture', 'packages': ['daos-core']}, lock, workspace, 12)
        output = workspace / 'packages'
        attempt = next((output / 'attempts').iterdir())
        self.assertEqual((attempt / 'overlay/payload.txt').read_text(), 'preserve failed payload')
        self.assertTrue((attempt / 'manifests/daos-core.json').is_file())
        self.assertEqual(daos.read(attempt / 'publish-failure.json')['status'], 'failed')
        self.assertFalse((output / 'overlay').exists())
        self.assertFalse((output / 'manifests').exists())
        self.assertFalse((output / 'package-set.json').exists())

    def test_package_inputs_bind_native_ui_and_recipe_contents(self):
        project = self.root / 'project'
        here = project / 'system/bsd-buildroot'
        (here / 'packages/daos-ui').mkdir(parents=True)
        coordinator = here / 'daos.py'
        coordinator.write_text('# coordinator fixture')
        recipe = here / 'packages/daos-ui/runtime.lock.json'
        recipe.write_text('{"runtime":1}')
        native = project / 'camera/native/example.cpp'
        native.parent.mkdir(parents=True)
        native.write_text('int version = 1;')
        qml = project / 'ui/Main.qml'
        qml.parent.mkdir()
        qml.write_text('Item { property int version: 1 }')
        evidence = project / 'camera/evidence-session/ignored.py'
        evidence.parent.mkdir()
        evidence.write_text('evidence = 1')
        (project / 'tools').mkdir()
        (project / 'tools/__init__.py').write_text('')
        (project / 'tools/diagnostics.py').write_text('VERSION = 1')
        profile = self.root / 'profile.json'
        profile.write_text('{}')
        lock = self.root / 'source-lock.json'
        lock.write_text('{}')
        base = self.root / 'work'
        (base / 'state').mkdir(parents=True)
        (base / 'state/distribution.json').write_text(json.dumps({'status': 'success', 'input_hash': 'fixture'}))
        backend = argparse.Namespace(_profile_inputs=lambda *a: {'input_hash': 'fixture'},
                                     _configured=lambda *a: None, _cached_artifacts=lambda *a: None)
        with mock.patch.object(daos, 'PROJECT', project), mock.patch.object(daos, 'HERE', here), \
             mock.patch.object(daos, 'module', return_value=backend):
            original = daos.package_inputs(profile, {}, lock, base)
            evidence.write_text('evidence = 2')
            self.assertEqual(original, daos.package_inputs(profile, {}, lock, base))
            for path in (native, qml, recipe):
                before = daos.package_inputs(profile, {}, lock, base)
                path.write_text(path.read_text() + '\nchanged')
                after = daos.package_inputs(profile, {}, lock, base)
                key = str(path.relative_to(project))
                self.assertNotEqual(before['source_sha256'][key], after['source_sha256'][key])
            before = daos.package_inputs(profile, {}, lock, base)
            coordinator.write_text('# coordinator changed')
            after = daos.package_inputs(profile, {}, lock, base)
            self.assertNotEqual(before['coordinator_sha256'], after['coordinator_sha256'])

    def test_revise_is_plan_only_unless_apply_is_explicit(self):
        for apply in (False, True):
            revision = argparse.Namespace(plan=mock.Mock(return_value={'revision_hash': 'reviewed-hash', 'executed': False}),
                                          apply=mock.Mock(return_value={'executed': True}))
            argv = ['daos.py', 'revise', '--workspace', str(self.root / 'work')]
            if apply:
                argv.append('--apply')
            with self.subTest(apply=apply), mock.patch.object(sys, 'argv', argv), \
                 mock.patch.object(daos, 'profile', return_value=(self.root / 'profile.json', {}, self.root / 'lock.json')), \
                 mock.patch.object(daos, 'module', return_value=revision), \
                 mock.patch.object(daos, 'doctor', side_effect=AssertionError('revision should not enter build preflight')), \
                 contextlib.redirect_stdout(io.StringIO()):
                daos.main()
            revision.plan.assert_called_once()
            if apply:
                revision.apply.assert_called_once_with(self.root / 'profile.json', str(self.root / 'work'), revision_hash='reviewed-hash')
            else:
                revision.apply.assert_not_called()


if __name__ == '__main__':
    unittest.main()
