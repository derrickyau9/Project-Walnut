#!/usr/local/bin/python3.12
"""Fixed, authenticated emergency ADB policy, independent of /var settings.

Invoked only by the early USB supervisor. The provisioned RSA AUTH keys are
still mandatory. Recovery grants those keys a root shell even if the normal
developer-mode marker is absent, disabled or on a damaged state filesystem.
No command-line switches, network listener or key-enrollment path are exposed.
"""
import importlib
import importlib.util
from pathlib import Path
import sys


def main():
    directory=Path('/usr/local/libexec/daos-adb')
    package='daos_early_authenticated_adb'
    spec=importlib.util.spec_from_file_location(package,directory/'__init__.py',submodule_search_locations=[str(directory)])
    module=importlib.util.module_from_spec(spec);sys.modules[package]=module;spec.loader.exec_module(module)
    server=importlib.import_module(package+'.server')
    # Only the ordinary setting policy changes. Connection.authenticate and
    # load_authorized_keys stay untouched in the existing audited daemon.
    server.developer_access=lambda: True
    sys.argv=[sys.argv[0],'--stdio','--authorized-keys','/etc/daos/adb_keys',
              '--shell','/rescue/sh','--cwd','/','--sync-root','/']
    server.main()


if __name__=='__main__':main()
