import shutil
import unittest
from unittest import mock

from tools.rootfs_deps import _dpkg_compare, parse_index, resolve


def package(name, version='1', architecture='arm64', **fields):
    return {'Package': name, 'Version': version, 'Architecture': architecture, **fields}


def simple_compare(first, operator, second):
    first, second = tuple(map(int, first.split('.'))), tuple(map(int, second.split('.')))
    return {'=': first == second, '>=': first >= second, '>>': first > second,
            '<=': first <= second, '<<': first < second}[operator]


class RootfsDependencyTests(unittest.TestCase):
    def records(self, *packages):
        return {record['Package']: record for record in packages}

    def test_control_folding_and_provenance_fields(self):
        records = parse_index('Package: root\nVersion: 1:2.0-1\nArchitecture: arm64\nDepends: alpha,\n beta:any (>= 2)\nDescription: ignored\n wrapped description\nFilename: pool/r/root.deb\nSHA256: abc\nSize: 123\n\n')
        self.assertEqual(records['root']['Depends'], 'alpha, beta:any (>= 2)')
        self.assertEqual(records['root']['Filename'], 'pool/r/root.deb')
        self.assertNotIn('Description', records['root'])

    def test_ambiguous_duplicate_or_malformed_control_is_refused(self):
        for text in (' Package: root\n', 'Package: root\nVersion: 1\n',
                     'Package: root\nVersion: 1\nArchitecture: arm64\nVersion: 2\n',
                     'Package: root\nVersion: 1\nArchitecture: arm64\nFilename: a.deb\n\nPackage: root\nVersion: 1\nArchitecture: arm64\nFilename: b.deb\n'):
            with self.subTest(text=text), self.assertRaises(ValueError): parse_index(text)

    def test_multiple_versions_select_newest_independent_of_index_order(self):
        old = 'Package: root\nVersion: 1\nArchitecture: all\nFilename: old.deb\n\n'
        new = 'Package: root\nVersion: 2\nArchitecture: arm64\nFilename: new.deb\n\n'
        with mock.patch('tools.rootfs_deps._dpkg_compare', side_effect=simple_compare):
            self.assertEqual(parse_index(old + new), parse_index(new + old))
            self.assertEqual(parse_index(old + new)['root']['Filename'], 'new.deb')

    def test_equal_version_target_candidate_wins_all_and_foreign_is_not_selected(self):
        generic = 'Package: root\nVersion: 1\nArchitecture: all\nFilename: all.deb\n\n'
        target = 'Package: root\nVersion: 1\nArchitecture: arm64\nFilename: arm64.deb\n\n'
        foreign = 'Package: root\nVersion: 9\nArchitecture: amd64\nFilename: host.deb\n\n'
        self.assertEqual(parse_index(generic + target + foreign)['root']['Filename'], 'arm64.deb')
        self.assertEqual(parse_index(foreign + target + generic)['root']['Filename'], 'arm64.deb')

    def test_predepends_cycles_and_all_architecture_are_complete(self):
        records = self.records(package('root', **{'Pre-Depends': 'alpha', 'Depends': 'data:any'}),
                               package('alpha', Depends='root'), package('data', architecture='all'))
        result = resolve(records, ['root'])
        self.assertEqual([item['Package'] for item in result['packages']], ['alpha', 'data', 'root'])
        self.assertTrue(any(edge['kind'] == 'Pre-Depends' for edge in result['edges']))
        self.assertEqual(len(result['edges']), 4)

    def test_unsatisfied_first_alternative_backtracks_to_complete_branch(self):
        records = self.records(package('root', Depends='bad | good'), package('bad', Depends='missing'), package('good'))
        result = resolve(records, ['root'])
        self.assertEqual([item['Package'] for item in result['packages']], ['good', 'root'])

    def test_over_budget_branch_backtracks_to_smaller_alternative(self):
        records = self.records(package('root', Depends='bad | good'),
                               package('bad', Depends='extra'), package('extra'), package('good'))
        result = resolve(records, ['root'], max_packages=2)
        self.assertEqual([item['Package'] for item in result['packages']], ['good', 'root'])

    def test_later_conflict_backtracks_across_prior_alternative(self):
        records = self.records(package('root', Depends='bad | good, tail'),
                               package('bad', Conflicts='tail'), package('good'), package('tail'))
        result = resolve(records, ['root'])
        self.assertEqual([item['Package'] for item in result['packages']], ['good', 'root', 'tail'])

    def test_versioned_virtual_provider_and_constraints(self):
        records = self.records(package('root', Depends='virtual-api (>= 2)'),
                               package('aa-old', Provides='virtual-api (= 1)'),
                               package('zz-new', Provides='virtual-api (= 2)'))
        with mock.patch('tools.rootfs_deps._dpkg_compare', side_effect=simple_compare):
            result = resolve(records, ['root'])
        self.assertEqual([item['Package'] for item in result['packages']], ['root', 'zz-new'])

    def test_unversioned_provider_cannot_satisfy_versioned_dependency(self):
        records = self.records(package('root', Depends='virtual-api (>= 1)'), package('provider', Provides='virtual-api'))
        with self.assertRaises(ValueError): resolve(records, ['root'])

    def test_one_package_can_provide_multiple_compatibility_versions(self):
        records = self.records(package('root', Depends='compat-api (= 9), compat-api (= 13)'),
                               package('provider', Provides='compat-api (= 9), compat-api (= 10), compat-api (= 13)'))
        with mock.patch('tools.rootfs_deps._dpkg_compare', side_effect=simple_compare):
            result = resolve(records, ['root'])
        self.assertEqual([item['Package'] for item in result['packages']], ['provider', 'root'])

    def test_target_qualifiers_do_not_satisfy_from_host_packages(self):
        records = self.records(package('root', Depends='host-only:amd64 | target:native, data:any'),
                               package('host-only', architecture='amd64'), package('target'), package('data', architecture='all'))
        result = resolve(records, ['root'])
        self.assertEqual([item['Package'] for item in result['packages']], ['data', 'root', 'target'])
        with self.assertRaises(ValueError): resolve(records, ['host-only'])

    def test_existing_provider_is_reused_deterministically(self):
        records = self.records(package('root', Depends='virtual-api'), package('aa-extra', Provides='virtual-api'),
                               package('zz-selected', Provides='virtual-api'))
        result = resolve(records, ['zz-selected', 'root'])
        self.assertEqual([item['Package'] for item in result['packages']], ['root', 'zz-selected'])
        self.assertEqual(result, resolve(dict(reversed(list(records.items()))), ['zz-selected', 'root']))

    def test_transitional_provider_does_not_conflict_with_itself(self):
        result = resolve(self.records(package('newlib', Provides='oldlib', Conflicts='oldlib')), ['newlib'])
        self.assertEqual(result['packages'][0]['Package'], 'newlib')

    def test_missing_unsupported_or_over_budget_closure_is_refused(self):
        with self.assertRaises(ValueError): resolve(self.records(package('root', Depends='missing')), ['root'])
        with self.assertRaises(ValueError): resolve(self.records(package('root', Depends='data [arm64]'), package('data')), ['root'])
        with self.assertRaises(ValueError): resolve(self.records(package('root', Depends='data'), package('data')), ['root'], max_packages=1)
        with self.assertRaises(ValueError): resolve(self.records(package('root')), ['root'], architecture='amd64')

    @unittest.skipUnless(shutil.which('dpkg'), 'actual Debian version comparator not installed')
    def test_real_dpkg_epoch_revision_and_tilde_ordering(self):
        self.assertTrue(_dpkg_compare('1:1.0-1', '>>', '99.0'))
        self.assertTrue(_dpkg_compare('1.0~rc1-1', '<<', '1.0-1'))
        self.assertTrue(_dpkg_compare('1.0-2', '>=', '1.0-1'))
        self.assertFalse(_dpkg_compare('1.0-1', '>>', '1.0-2'))


if __name__ == '__main__':
    unittest.main()
