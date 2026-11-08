"""Contained source-handoff tests: privacy boundaries, identity and tampering."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools import source_bundle as bundle


class SourceBundleTests(unittest.TestCase):
    def test_text_evidence_logs_redact_only_the_bundled_copy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.root(tmp)
            name='docs/testing/artifacts/r12-logs/probe.log'
            log=root/name
            log.parent.mkdir(parents=True)
            original=('probe source='+str(root/'ui/on-device/shell_model.py')+'\n').encode()
            log.write_bytes(original)
            output=Path(tmp)/'logs.zip'
            result=bundle.create(output,root)
            self.assertTrue(result['verified'])
            self.assertEqual(1,result['redacted_evidence_copies'])
            self.assertEqual(original,log.read_bytes())
            with zipfile.ZipFile(output) as archive:
                copied=archive.read(bundle.PREFIX+name)
                self.assertIn(b'<SOURCE_ROOT>',copied)
                self.assertNotIn(str(root).encode(),copied)
                manifest=json.loads(archive.read(bundle.PREFIX+bundle.MANIFEST))
                entry=next(item for item in manifest['files'] if item['path']==name)
                self.assertEqual(bundle.digest(original),entry['source_sha256'])
                self.assertEqual(bundle.digest(copied),entry['sha256'])
            # Log suffix alone must not permit rewriting source or hiding keys.
            (root/'camera/private.log').write_bytes(original)
            with self.assertRaisesRegex(ValueError,'personal workspace prefix'):
                bundle.create(Path(tmp)/'outside-evidence.zip',root)
            (root/'camera/private.log').unlink()
            log.write_bytes(b'-----BEGIN '+b'PRIVATE KEY-----\nexample')
            with self.assertRaisesRegex(ValueError,'credential-shaped'):
                bundle.create(Path(tmp)/'secret-log.zip',root)

    def test_round8_native_and_workflow_evidence_are_copied_with_original_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.root(tmp)
            names=['camera/native/evidence/round8/verification.json','daos/capability_runtime/evidence/round8-workflow.json',
                   'system/update-model/evidence/bound-r9/verification.json',
                   'system/freebsd/arm-vm/evidence/controlled-reboot/verification.json',
                   'system/freebsd/image-boot/evidence/first/verification.json']
            originals={}
            for name in names:
                path=root/name; path.parent.mkdir(parents=True,exist_ok=True)
                data=json.dumps({'source':str(root/'camera/source.cpp')}).encode()
                path.write_bytes(data); originals[name]=data
            output=Path(tmp)/'r8.zip'; result=bundle.create(output,root)
            self.assertEqual(result['redacted_evidence_copies'],len(names))
            with zipfile.ZipFile(output) as archive:
                manifest=json.loads(archive.read(bundle.PREFIX+bundle.MANIFEST))
                entries={item['path']:item for item in manifest['files']}
                for name in names:
                    self.assertEqual((root/name).read_bytes(),originals[name])
                    self.assertEqual(entries[name]['source_sha256'],bundle.digest(originals[name]))
                    self.assertIn('<SOURCE_ROOT>',archive.read(bundle.PREFIX+name).decode())

    def root(self, directory):
        root = Path(directory) / 'source'
        root.mkdir()
        (root / 'README.md').write_text('HF-Sim source\n')
        (root / 'camera').mkdir()
        (root / 'camera/core.py').write_text('answer = 42\n')
        return root

    def runtime_inventory_data(self, name, extra=None, size=8 * 1024**2 + 1):
        document = {'format': ('DaOS-freebsd-runtime-v2' if name.endswith('/runtime.json')
                               else 'DaOS-freebsd-runtime-offline-validation-v1'),
                    'architecture': 'arm64', 'entries': [], 'omissions': [],
                    'packages': [], 'base_dependency_manifest': [], 'padding': ''}
        document.update(extra or {})
        initial = json.dumps(document).encode()
        document['padding'] = 'x' * (size - len(initial))
        return json.dumps(document).encode()

    def test_large_declared_runtime_inventories_redact_copies_without_changing_sources(self):
        names = ['system/freebsd/image/evidence/runtime-004/runtime.json',
                 'system/freebsd/image/evidence/runtime-004/offline-validation.json']
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            originals = {}
            home = '/home/' + 'operator/hf-task-runtime-004/rootfs'
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                original = self.runtime_inventory_data(name, {str(root): [str(root / 'build/runtime'), home]})
                # JSON-escaped private paths must still be inspected structurally.
                original = original.replace(b'/', b'\\/')
                path.write_bytes(original)
                originals[name] = original
            output = Path(tmp) / 'inventories.zip'
            result = bundle.create(output, root)
            self.assertEqual(2, result['redacted_evidence_copies'])
            self.assertTrue(result['verified'])
            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read(bundle.PREFIX + bundle.MANIFEST))
                records = {item['path']: item for item in manifest['files']}
                for name, original in originals.items():
                    copied = archive.read(bundle.PREFIX + name)
                    document = json.loads(copied)
                    self.assertGreater(len(original), 8 * 1024**2)
                    self.assertEqual(original, (root / name).read_bytes())
                    self.assertEqual(bundle.digest(original), records[name]['source_sha256'])
                    self.assertEqual(bundle.digest(copied), records[name]['sha256'])
                    self.assertEqual(['<SOURCE_ROOT>' + str(Path('/build/runtime')),
                                      '<TASK_RUNTIME_ROOT>/hf-task-runtime-004/rootfs'],
                                     document['<SOURCE_ROOT>'])
                    self.assertEqual(json.loads(original)['padding'], document['padding'])

    def test_large_runtime_inventory_privacy_bound_is_exact_and_path_scoped(self):
        name = 'system/freebsd/image/evidence/runtime-004/runtime.json'
        maximum = self.runtime_inventory_data(name, size=32 * 1024**2)
        self.assertEqual(32 * 1024**2, len(maximum))
        self.assertEqual('DaOS-freebsd-runtime-v2', bundle.evidence_document(maximum, Path(name))['format'])
        for relative in [name, 'system/freebsd/image/evidence/runtime-004/offline-validation.json']:
            with self.subTest(oversized=relative):
                with self.assertRaisesRegex(ValueError, 'privacy inspection bound'):
                    bundle.evidence_document(maximum + b' ', Path(relative))
        smaller = self.runtime_inventory_data(name)
        for relative in ['camera/record.json',
                         'system/freebsd/image/evidence/runtime-004/adjacent.json',
                         'system/freebsd/image/evidence/runtime-005/runtime.json',
                         'system/freebsd/image/evidence/runtime-004/Runtime.json',
                         'system/freebsd/image/evidence/runtime-004/nested/../runtime.json']:
            with self.subTest(relative=relative):
                with self.assertRaisesRegex(ValueError, 'privacy inspection bound'):
                    bundle.evidence_document(smaller, Path(relative))

    def test_large_runtime_inventory_still_requires_declared_format_and_full_json(self):
        name = 'system/freebsd/image/evidence/runtime-004/runtime.json'
        for extra in [{'format': 'unrelated'}, {'architecture': 'amd64'}, {'entries': 'opaque'}]:
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(ValueError, 'declared runtime inventory'):
                    bundle.evidence_document(self.runtime_inventory_data(name, extra), Path(name))
        original = self.runtime_inventory_data(name)
        with self.assertRaises(ValueError):
            bundle.evidence_document(original[:-1], Path(name))
        with self.assertRaisesRegex(ValueError, 'duplicate evidence JSON key'):
            bundle.evidence_document(original[:-1] + b',"format":"duplicate"}', Path(name))

    def test_large_runtime_inventory_still_refuses_redaction_collisions_and_secrets(self):
        name = 'system/freebsd/image/evidence/runtime-004/runtime.json'
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            path = root / name
            path.parent.mkdir(parents=True)
            for extra, error in [({str(root): 1, '<SOURCE_ROOT>': 2}, 'collision'),
                                 ({'secret': '-----BEGIN ' + 'PRIVATE KEY-----'}, 'credential-shaped')]:
                original = self.runtime_inventory_data(name, extra)
                path.write_bytes(original)
                output = Path(tmp) / 'refused.zip'
                with self.subTest(error=error):
                    with self.assertRaisesRegex(ValueError, error):
                        bundle.create(output, root)
                    self.assertEqual(original, path.read_bytes())
                    self.assertFalse(output.exists())
            # A correctly hashed archive cannot use the larger allowance to
            # conceal an escaped task-home path from verifier privacy checks.
            data = self.runtime_inventory_data(name, {'path': '/home/' + 'operator/hf-task-runtime-004/rootfs'})
            data = data.replace(b'/', b'\\/')
            item = {'path': name, 'bytes': len(data), 'sha256': bundle.digest(data),
                    'origin': 'fixture', 'rights': 'fixture'}
            output = Path(tmp) / 'private.zip'
            with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(bundle.PREFIX + name, data)
                archive.writestr(bundle.PREFIX + bundle.MANIFEST,
                                 json.dumps({'format': 'hf-source-bundle-v1', 'files': [item]}))
            with self.assertRaisesRegex(ValueError, 'task-home'):
                bundle.verify(output)

    def test_boundary_exclusions_redaction_and_unchanged_hardware(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            for name in ['build', '.aws', 'personal', 'camera/__pycache__', 'docs/testing/artifacts',
                         'hardware/imports/example', 'system/linux/evidence']:
                (root / name).mkdir(parents=True, exist_ok=True)
            for name in ['build/token.txt', '.aws/config', 'personal/tax.txt', 'camera/__pycache__/x.pyc',
                         'camera/state.sqlite3', 'camera/.env', 'camera/device-ui.json',
                         'camera/device-ui-unknown-test.bak', 'camera/.service.lock',
                         'camera/.workflow.lock', 'camera/journal.sqlite3-journal',
                         'camera/uncommitted.dng.partial', 'docs/testing/artifacts/browser.png']:
                (root / name).write_bytes(b'not source')
            original = b'original prototype archive bytes'
            (root / 'hardware/imports/example/original.zip').write_bytes(original)
            evidence = root / 'system/linux/evidence/build.json'
            evidence.write_text(json.dumps({'path': str(root / 'build/native'), 'metadata': 'safe'}))
            before = evidence.read_bytes()
            output = Path(tmp) / 'snapshot.zip'
            result = bundle.create(output, root)
            self.assertEqual(result['redacted_evidence_copies'], 1)
            self.assertEqual(evidence.read_bytes(), before)
            with zipfile.ZipFile(output) as archive:
                names = archive.namelist()
                self.assertEqual(len(names), 5)
                self.assertEqual(archive.read(bundle.PREFIX + 'hardware/imports/example/original.zip'), original)
                document = json.loads(archive.read(bundle.PREFIX + 'system/linux/evidence/build.json'))
                self.assertTrue(document['path'].startswith('<SOURCE_ROOT>'))
                manifest = json.loads(archive.read(bundle.PREFIX + bundle.MANIFEST))
                item = next(x for x in manifest['files'] if x['path'].endswith('build.json'))
                self.assertEqual(item['source_sha256'], bundle.digest(before))

    def test_deterministic_and_exclusive_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            first, second = Path(tmp) / 'a.zip', Path(tmp) / 'b.zip'
            bundle.create(first, root)
            os.utime(root / 'README.md', (1, 1))
            bundle.create(second, root)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            before = first.read_bytes()
            with self.assertRaisesRegex(ValueError, 'existing'):
                bundle.create(first, root)
            self.assertEqual(first.read_bytes(), before)
            target = Path(tmp) / 'racing.zip'
            real_link = os.link
            def racing_link(source, destination):
                target.write_bytes(b'other owner')
                return real_link(source, destination)
            with patch.object(bundle.os, 'link', side_effect=racing_link):
                with self.assertRaises(FileExistsError):
                    bundle.create(target, root)
            self.assertEqual(target.read_bytes(), b'other owner')
            self.assertFalse(list(Path(tmp).glob('.hf-source-*.zip')))

    def test_secret_and_included_destination_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            (root / 'camera/private.txt').write_text('-----BEGIN ' + 'PRIVATE KEY-----\nexample')
            output = Path(tmp) / 'refused.zip'
            with self.assertRaisesRegex(ValueError, 'credential-shaped'):
                bundle.create(output, root)
            self.assertFalse(output.exists())
            with self.assertRaisesRegex(ValueError, 'included source'):
                bundle.create(root / 'camera/snapshot.zip', root)
            self.assertFalse((root / 'camera/snapshot.zip').exists())

    def test_verifier_rejects_unlisted_tampering_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            source = Path(tmp) / 'source.zip'
            bundle.create(source, root)
            with zipfile.ZipFile(source) as archive:
                entries = [(item.filename, archive.read(item.filename)) for item in archive.infolist()]
            for variant in ('changed', 'unlisted', 'traversal'):
                output = Path(tmp) / (variant + '.zip')
                with zipfile.ZipFile(output, 'w') as archive:
                    for name, data in entries:
                        archive.writestr(name, b'changed' if variant == 'changed' and name.endswith('README.md') else data)
                    if variant == 'unlisted':
                        archive.writestr(bundle.PREFIX + 'unlisted.txt', b'extra')
                    if variant == 'traversal':
                        archive.writestr(bundle.PREFIX + '../escape.txt', b'extra')
                with self.assertRaises(ValueError):
                    bundle.verify(output)

    def test_source_links_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            with patch.object(bundle, 'is_link', side_effect=lambda p: p.name == 'core.py'):
                with self.assertRaisesRegex(ValueError, 'nonregular'):
                    bundle.create(Path(tmp) / 'refused.zip', root)

    def test_evidence_private_keys_redacted_and_collisions_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            evidence = root / 'system/linux/evidence/build.json'
            evidence.parent.mkdir(parents=True)
            private = str(root / 'private/file')
            evidence.write_text(json.dumps({private: {'source': private}}))
            output = Path(tmp) / 'redacted.zip'
            bundle.create(output, root)
            with zipfile.ZipFile(output) as archive:
                result = json.loads(archive.read(bundle.PREFIX + 'system/linux/evidence/build.json'))
                key = next(iter(result))
                self.assertTrue(key.startswith('<SOURCE_ROOT>'))
                self.assertEqual(result[key]['source'], key)
            evidence.write_text(json.dumps({str(root): 1, '<SOURCE_ROOT>': 2}))
            with self.assertRaisesRegex(ValueError, 'collision'):
                bundle.create(Path(tmp) / 'refused.zip', root)

    def test_arm64_and_guest_evidence_copies_redact_paths_without_changing_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=self.root(tmp)
            for platform in ('arm64','qemu-virt'):
                evidence=root/'system'/platform/'evidence'/'run.json'
                evidence.parent.mkdir(parents=True)
                evidence.write_text(json.dumps({str(root):str(root/'build/program')}))
                original=evidence.read_bytes()
                copied,changed=bundle.evidence_copy(original,evidence.relative_to(root),root)
                self.assertTrue(changed)
                parsed=json.loads(copied)
                self.assertEqual(list(parsed),['<SOURCE_ROOT>'])
                self.assertTrue(parsed['<SOURCE_ROOT>'].startswith('<SOURCE_ROOT>'))
                self.assertEqual(evidence.read_bytes(),original)

    def test_verifier_rejects_hashed_private_paths_and_missing_rights(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, name in enumerate(['camera/.aws/credentials', 'outside.txt', 'camera/CON.txt', 'camera/core.py']):
                data = b'example'
                item = {'path': name, 'bytes': len(data), 'sha256': bundle.digest(data)}
                if index != 3:
                    item.update({'origin': 'test fixture', 'rights': 'test fixture'})
                output = Path(tmp) / (str(index) + '.zip')
                document = {'format': 'hf-source-bundle-v1', 'files': [item]}
                with zipfile.ZipFile(output, 'w') as archive:
                    archive.writestr(bundle.PREFIX + name, data)
                    archive.writestr(bundle.PREFIX + bundle.MANIFEST, json.dumps(document))
                with self.assertRaises(ValueError):
                    bundle.verify(output)


    def test_round7_evidence_families_redact_only_copies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            home = '/home/' + 'operator/hf-task-rootfs-r7-final'
            paths = ['system/' + name + '/evidence' for name in ('arm64-rootfs', 'arm64-image', 'targets')]
            paths.append('ui/on-device/evidence-round7')
            originals = {}
            for folder in paths:
                directory = root / folder
                directory.mkdir(parents=True)
                for suffix in ('.json', '.txt'):
                    path = directory / ('run' + suffix)
                    data = (json.dumps({home: [home + '/rootfs', str(root / 'build/image')]}).encode()
                            if suffix == '.json' else ('run ' + home + '/rootfs and ' + str(root / 'build/image')).encode())
                    path.write_bytes(data)
                    originals[path.relative_to(root).as_posix()] = data
            imported = root / 'hardware/imports/example/original.json'
            imported.parent.mkdir(parents=True)
            imported.write_text(json.dumps({'original': home}), encoding='utf-8')
            source_before = (root / 'camera/core.py').read_bytes()
            output = Path(tmp) / 'r7.zip'
            result = bundle.create(output, root)
            self.assertEqual(8, result['redacted_evidence_copies'])
            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read(bundle.PREFIX + bundle.MANIFEST))
                records = {item['path']: item for item in manifest['files']}
                for name, original in originals.items():
                    copied = archive.read(bundle.PREFIX + name)
                    self.assertNotIn(home.encode(), copied)
                    self.assertIn(b'<TASK_RUNTIME_ROOT>/hf-task-rootfs-r7-final', copied)
                    self.assertIn(b'<SOURCE_ROOT>', copied)
                    self.assertEqual(bundle.digest(original), records[name]['source_sha256'])
                    self.assertEqual(original, (root / name).read_bytes())
                self.assertEqual(imported.read_bytes(), archive.read(bundle.PREFIX + imported.relative_to(root).as_posix()))
                self.assertEqual(source_before, archive.read(bundle.PREFIX + 'camera/core.py'))

    def test_redaction_respects_task_path_and_source_sibling_boundaries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            home = '/home/' + 'operator/hf-task-rootfs-r7-final'
            unrelated = [str(root) + '-unrelated/file', '/var' + home + '/rootfs',
                         '/home/' + 'operator/hf-taskfoo/file', '/home/' + 'operator/photos/file']
            uri = 'file://' + ('/' if not root.as_posix().startswith('/') else '') + root.as_posix() + '/file'
            raw = json.dumps({'values': [str(root), str(root / 'file'), 'run ' + str(root) + ' next',
                                        uri, home, home + '/rootfs', 'file://' + home + '/rootfs',
                                        home + ': failed', 'file://' + home + '?query=1', *unrelated]}).encode()
            with patch.dict(os.environ, {'TEMP': '/neutral-temporary', 'TMP': '/neutral-temporary'}):
                copied, changed = bundle.evidence_copy(raw, Path('system/targets/evidence/run.json'), root)
            self.assertTrue(changed)
            values = json.loads(copied)['values']
            self.assertEqual('<SOURCE_ROOT>', values[0])
            self.assertEqual('run <SOURCE_ROOT> next', values[2])
            self.assertIn('<SOURCE_ROOT>', values[3])
            self.assertEqual('<TASK_RUNTIME_ROOT>/hf-task-rootfs-r7-final', values[4])
            self.assertIn('<TASK_RUNTIME_ROOT>/hf-task-rootfs-r7-final/rootfs', values[6])
            self.assertEqual('<TASK_RUNTIME_ROOT>/hf-task-rootfs-r7-final: failed', values[7])
            self.assertEqual('file://<TASK_RUNTIME_ROOT>/hf-task-rootfs-r7-final?query=1', values[8])
            self.assertEqual(unrelated, values[9:])
            for relative in ('camera/core.py', 'system/targets/evidence/readme.md',
                             'system/arm64-rootfs/metadata/InRelease'):
                self.assertEqual((raw, False), bundle.evidence_copy(raw, Path(relative), root))

    def test_round7_redaction_refuses_key_loss(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            home = '/home/' + 'operator/hf-task-rootfs-r7'
            other = '/home/' + 'another/hf-task-rootfs-r7'
            relative = Path('system/arm64-rootfs/evidence/run.json')
            with self.assertRaisesRegex(ValueError, 'collision'):
                bundle.evidence_copy(json.dumps({home: 1, other: 2}).encode(), relative, root)
            with self.assertRaisesRegex(ValueError, 'duplicate evidence'):
                bundle.evidence_copy(json.dumps({'path': home})[:-1].encode() + b',"path":"other"}', relative, root)

    def test_upstream_metadata_identity_and_misplaced_runtime_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            metadata = root / 'system/arm64-rootfs/metadata'
            metadata.mkdir(parents=True)
            originals = {'InRelease': b'fixture signed upstream release metadata\n',
                         'Packages.xz': b'\xfd7zXZ\x00fixture upstream package index'}
            for name, data in originals.items():
                (metadata / name).write_bytes(data)
            generated = ['runtime.deb', 'QtCore.abi3.so', 'libQt5Core.so.5.15.15', 'kernel.img',
                         'data.ext4', 'bootstrap.cpio', 'bootstrap.cpio.gz', 'hf-camerad.elf',
                         'rootfs.tar', 'screen.png']
            for name in generated:
                (metadata.parent / name).write_bytes(b'generated fixture')
            imported = root / 'hardware/imports/example/original.img'
            imported.parent.mkdir(parents=True)
            imported.write_bytes(b'original imported bytes')
            output = Path(tmp) / 'metadata.zip'
            bundle.create(output, root)
            with zipfile.ZipFile(output) as archive:
                manifest = json.loads(archive.read(bundle.PREFIX + bundle.MANIFEST))
                records = {item['path']: item for item in manifest['files']}
                for name, data in originals.items():
                    relative = 'system/arm64-rootfs/metadata/' + name
                    self.assertEqual(data, archive.read(bundle.PREFIX + relative))
                    self.assertEqual(data, (metadata / name).read_bytes())
                    self.assertIn('official Debian', records[relative]['origin'])
                    self.assertIn('upstream Debian', records[relative]['rights'])
                    self.assertNotIn('source_sha256', records[relative])
                self.assertTrue(all('system/arm64-rootfs/' + name not in records for name in generated))
                self.assertEqual(imported.read_bytes(), archive.read(bundle.PREFIX + imported.relative_to(root).as_posix()))
            for name in generated:
                with self.subTest(name=name):
                    self.assertIsNotNone(bundle.exclusion(Path('system/arm64-image') / name))

    def test_unredacted_task_home_fails_without_modifying_authored_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            path = root / 'README.md'
            original = ('example ' + '/home/' + 'operator/hf-task-rootfs-r7/rootfs').encode()
            path.write_bytes(original)
            output = Path(tmp) / 'refused.zip'
            with self.assertRaisesRegex(ValueError, 'task-home'):
                bundle.create(output, root)
            self.assertFalse(output.exists())
            self.assertEqual(original, path.read_bytes())
            name = 'system/targets/evidence/private.json'
            data = json.dumps({'path': '/home/' + 'operator/hf-task-rootfs-r7/rootfs'}).replace('/', r'\/').encode()
            item = {'path': name, 'bytes': len(data), 'sha256': bundle.digest(data),
                    'origin': 'fixture', 'rights': 'fixture'}
            with zipfile.ZipFile(output, 'w') as archive:
                archive.writestr(bundle.PREFIX + name, data)
                archive.writestr(bundle.PREFIX + bundle.MANIFEST,
                                 json.dumps({'format': 'hf-source-bundle-v1', 'files': [item]}))
            with self.assertRaisesRegex(ValueError, 'task-home'):
                bundle.verify(output)

    def test_debian_runtime_inventory_matches_all_binary_pins_and_original_notices(self):
        root = Path(__file__).resolve().parents[1]
        inventory = json.loads((root / 'licenses/INVENTORY.json').read_text(encoding='utf-8'))['debian_arm64_qt_runtime']
        lock_path = root / inventory['package_lock']
        lock = json.loads(lock_path.read_text(encoding='utf-8'))
        entries = {item['path']: item for item in json.loads((root / inventory['notice_manifest']).read_text(encoding='utf-8'))['entries']}
        self.assertEqual(170, inventory['binary_package_count'])
        self.assertEqual(bundle.digest(lock_path.read_bytes()), inventory['package_lock_sha256'])
        self.assertFalse(inventory['binary_packages_in_source_snapshot'])
        self.assertTrue(inventory['generated_runtime_contains_upstream_binaries'])
        self.assertFalse(inventory['upstream_binary_packages_rebuilt_from_source'])
        records = {item['name']: item for item in inventory['components']}
        self.assertEqual({item['name'] for item in lock['packages']}, set(records))
        for package in lock['packages']:
            item = records[package['name']]
            self.assertEqual(package['version'], item['version'])
            self.assertEqual(package['architecture'], item['architecture'])
            self.assertEqual(package['sha256'], item['binary_package_sha256'])
            self.assertEqual(package['url'], item['binary_package_url'])
            notice = item['original_notice']
            self.assertEqual('/usr/share/doc/' + item['name'] + '/copyright', notice['runtime_path'])
            resolved = entries[notice['resolved_runtime_path'].lstrip('/')]
            self.assertEqual('file', resolved['type'])
            self.assertEqual(resolved['sha256'], notice['sha256'])
        for metadata in inventory['upstream_metadata_in_source_snapshot']:
            self.assertEqual(metadata['sha256'], bundle.digest((root / metadata['path']).read_bytes()))


    def test_source_json_privacy_is_inspected_without_rewriting_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self.root(tmp)
            path = root / 'camera/record.json'
            home = '/home/' + 'operator/hf-task-rootfs-r7/rootfs'
            output = Path(tmp) / 'refused.zip'
            for original in (json.dumps({'path': home}).replace('/', r'\/').encode(),
                             b'{malformed JSON', b' ' * (8 * 1024**2 + 1)):
                with self.subTest(size=len(original)):
                    path.write_bytes(original)
                    with self.assertRaises(ValueError):
                        bundle.create(output, root)
                    self.assertEqual(original, path.read_bytes())
                    self.assertFalse(output.exists())
            original = b'{ "source" : "public metadata", "count": 2 }\n'
            path.write_bytes(original)
            bundle.create(output, root)
            with zipfile.ZipFile(output) as archive:
                self.assertEqual(original, archive.read(bundle.PREFIX + 'camera/record.json'))


if __name__ == '__main__':
    unittest.main()
