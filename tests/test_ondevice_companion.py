"""Sibling helpers keep one exception identity across host import orders."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('PyQt5'), 'optional PyQt5 is unavailable')
class CompanionImportTests(unittest.TestCase):
    def run_isolated(self, body):
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen', QT_QUICK_BACKEND='software')
        prefix = "from pathlib import Path\nimport sys\nroot = Path.cwd()\nui = root / 'ui' / 'on-device'\n"
        result = subprocess.run(
            [sys.executable, '-X', 'utf8', '-c', prefix + textwrap.dedent(body)],
            cwd=ROOT, env=environment, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)

    def check_import_order(self, direct_first):
        self.run_isolated(f'''
            sys.path.insert(0, str(ui))
            if {direct_first!r}:
                import media_catalog
            from apps_controller import _companion
            companion = _companion('media_catalog')
            import media_catalog
            assert companion is media_catalog
            assert companion.CatalogError is media_catalog.CatalogError

            # A newly committed capture can precede asynchronous catalog sync.
            # Its existing legacy sidecar must remain readable in either order.
            import tempfile, uuid
            from media_library import MediaStore
            workspace = _companion('media_workspace')
            with tempfile.TemporaryDirectory() as directory:
                data = Path(directory)
                catalog = companion.MediaCatalog(data)
                catalog.active_library = str(uuid.uuid4())
                photo = str(uuid.uuid4())
                legacy = MediaStore(data / 'legacy')
                legacy.write(photo, {{'rating': 3}})
                saved = legacy.root / (photo + '.daos.json')
                before = saved.read_bytes()
                try:
                    catalog.sidecar(catalog.active_library, photo)
                except workspace.CatalogError:
                    pass
                else:
                    raise AssertionError('fixture photo was unexpectedly indexed')
                store = workspace.CatalogSidecarStore(legacy, catalog, lambda: None)
                assert store.read(photo)['rating'] == 3
                assert saved.read_bytes() == before
        ''')

    def test_direct_import_first_reuses_sibling_and_reads_unindexed_photo(self):
        self.check_import_order(True)

    def test_companion_import_first_aliases_sibling_and_reads_unindexed_photo(self):
        self.check_import_order(False)

    def test_same_named_module_from_another_file_is_not_reused(self):
        self.run_isolated('''
            sys.path.insert(0, str(ui))
            import types
            foreign = types.ModuleType('media_catalog')
            foreign.__file__ = str(root / 'another-directory' / 'media_catalog.py')
            sys.modules['media_catalog'] = foreign
            from apps_controller import _companion
            companion = _companion('media_catalog')
            assert companion is not foreign
            assert Path(companion.__file__).resolve() == (ui / 'media_catalog.py').resolve()
            assert sys.modules['media_catalog'] is foreign
            assert _companion('media_catalog') is companion
        ''')

    def test_absolute_file_import_keeps_sibling_loading_without_ui_search_path(self):
        self.run_isolated('''
            import importlib.util
            assert str(ui) not in sys.path
            spec = importlib.util.spec_from_file_location('private_apps_entry', ui / 'apps_controller.py')
            apps = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(apps)
            catalog = apps._companion('media_catalog')
            workspace = apps._companion('media_workspace')
            assert workspace.CatalogError is catalog.CatalogError
            assert Path(catalog.__file__).resolve() == (ui / 'media_catalog.py').resolve()
        ''')
