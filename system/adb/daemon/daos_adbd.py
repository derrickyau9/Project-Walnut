#!/usr/bin/env python3
"""Standalone entry point; its sibling directory contains the daemon package."""
from pathlib import Path
import importlib
import importlib.util
import sys

directory = Path(__file__).resolve().parent
package = 'daos_adb_device_runtime'
spec = importlib.util.spec_from_file_location(package, directory / '__init__.py',
                                            submodule_search_locations=[str(directory)])
module = importlib.util.module_from_spec(spec)
sys.modules[package] = module
spec.loader.exec_module(module)
main = importlib.import_module(package + '.server').main

if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError) as error:
        print('DaOS ADB device daemon: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
