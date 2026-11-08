"""Deterministic declared Debian Depends/Pre-Depends closure for ARM64/all.

Input authentication is the caller's responsibility. This module never queries
installed packages, downloads files or invokes apt. Debian version ordering is
delegated to the installed dpkg comparator, without invoking a shell.
"""
from __future__ import annotations

from functools import lru_cache
import re
import shutil
import subprocess


FIELDS = frozenset({'Package', 'Version', 'Architecture', 'Depends', 'Pre-Depends',
                    'Provides', 'Filename', 'SHA256', 'Size', 'Installed-Size',
                    'Essential', 'Conflicts', 'Breaks', 'Replaces', 'Multi-Arch', 'Source'})
NAME = r'[a-z0-9][a-z0-9+.-]*'
VERSION = r'(?:[0-9]+:)?[0-9][A-Za-z0-9.+:~\-]*'
ATOM = re.compile(r'^(' + NAME + r')(?::([a-z0-9-]+))?(?:\s*\(\s*(<<|<=|=|>=|>>)\s*(' + VERSION + r')\s*\))?$')


def parse_index(text: str):
    """Parse Debian control paragraphs, retaining only closure/provenance fields.

    Debian indexes can contain several versions of a package. Select the newest
    target-compatible candidate using Debian ordering, independent of file order.
    Contradictory records for the same version/architecture remain an error.
    """
    if not isinstance(text, str):
        raise TypeError('package index text must be decoded text')
    result, current, seen, previous = {}, {}, set(), None

    def finish():
        nonlocal current, seen, previous
        if seen:
            for key in ('Package', 'Version', 'Architecture'):
                if not current.get(key):
                    raise ValueError('package paragraph lacks ' + key)
            name = current['Package']
            if not re.fullmatch(NAME, name) or not re.fullmatch(VERSION, current['Version']):
                raise ValueError('invalid package name or version')
            if not re.fullmatch(r'[a-z0-9-]+', current['Architecture']):
                raise ValueError('invalid package architecture')
            previous_candidate = result.get(name)
            if previous_candidate is None or previous_candidate == current:
                result[name] = current
            elif previous_candidate['Version'] == current['Version']:
                if previous_candidate['Architecture'] == current['Architecture']:
                    raise ValueError('ambiguous duplicate package candidate: ' + name)
                # A target-specific binary wins an otherwise equal-version all
                # candidate. Foreign records never displace target candidates.
                rank = lambda record: 2 if record['Architecture'] == 'arm64' else 1 if record['Architecture'] == 'all' else 0
                if rank(current) > rank(previous_candidate):
                    result[name] = current
                elif rank(current) == rank(previous_candidate):
                    raise ValueError('ambiguous duplicate package architecture: ' + name)
            else:
                previous_usable = previous_candidate['Architecture'] in ('arm64', 'all')
                current_usable = current['Architecture'] in ('arm64', 'all')
                if previous_usable != current_usable:
                    if current_usable:
                        result[name] = current
                elif _dpkg_compare(current['Version'], '>>', previous_candidate['Version']):
                    result[name] = current
                elif not _dpkg_compare(current['Version'], '<<', previous_candidate['Version']):
                    raise ValueError('ambiguous equivalent package versions: ' + name)
        current, seen, previous = {}, set(), None

    for line in text.splitlines():
        if not line:
            finish()
            continue
        if line[0] in ' \t':
            if previous is None:
                raise ValueError('control continuation has no field')
            if previous in FIELDS:
                current[previous] += ' ' + line.strip()
            continue
        matched = re.fullmatch(r'([A-Za-z0-9][A-Za-z0-9-]*):[ \t]*(.*)', line)
        if not matched:
            raise ValueError('invalid package control field')
        key, value = matched.groups()
        if key in seen:
            raise ValueError('duplicate package control field: ' + key)
        seen.add(key)
        previous = key
        if key in FIELDS:
            current[key] = value.strip()
    finish()
    return result


def _atom(text):
    matched = ATOM.fullmatch(text.strip())
    if not matched:
        raise ValueError('unsupported binary dependency relation: ' + text)
    return matched.groups()


@lru_cache(maxsize=8192)
def _groups(value):
    if not value.strip():
        return ()
    groups = []
    for group in value.split(','):
        alternatives = tuple((part.strip(), _atom(part)) for part in group.split('|'))
        groups.append((group.strip(), alternatives))
    return tuple(groups)


@lru_cache(maxsize=65536)
def _dpkg_compare(version, operator, required):
    executable = shutil.which('dpkg')
    if not executable:
        raise RuntimeError('installed dpkg is required for Debian version ordering')
    if not re.fullmatch(VERSION, version) or not re.fullmatch(VERSION, required):
        raise ValueError('invalid Debian version operand')
    result = subprocess.run([executable, '--compare-versions', version, operator, required],
                            capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1):
        raise ValueError('dpkg refused a Debian version comparison')
    return result.returncode == 0


def _satisfies(version, operator, required):
    if operator is None:
        return True
    return version is not None and _dpkg_compare(version, operator, required)


def _provided(record):
    result = {}
    for expression, alternatives in _groups(record.get('Provides', '')):
        if len(alternatives) != 1:
            raise ValueError('Provides does not support alternatives: ' + expression)
        _, (name, qualifier, operator, version) = alternatives[0]
        if qualifier is not None or operator not in (None, '='):
            raise ValueError('unsupported virtual Provides relation')
        # One binary may implement several compatibility API versions, e.g.
        # debhelper-compat (= 9), debhelper-compat (= 10), ... in Debian main.
        versions = result.setdefault(name, [])
        if version not in versions:
            versions.append(version)
    return result


def _record_matches(record, atom, architecture):
    name, qualifier, operator, required = atom
    if qualifier not in (None, 'any', 'native', architecture):
        return False
    if record.get('Architecture') not in (architecture, 'all'):
        return False
    if name == record['Package']:
        return _satisfies(record['Version'], operator, required)
    provided = _provided(record)
    return name in provided and any(_satisfies(version, operator, required) for version in provided[name])


def _conflict(new, selected, architecture):
    # A transitional replacement may provide the name it Conflicts with; Debian
    # does not consider a package's own virtual name a self-conflict.
    for other in selected.values():
        if other['Package'] == new['Package']:
            continue
        for first, second in ((new, other), (other, new)):
            for key in ('Conflicts', 'Breaks'):
                for _, alternatives in _groups(first.get(key, '')):
                    if any(_record_matches(second, atom, architecture) for _, atom in alternatives):
                        return True
    return False


def resolve(records, seeds, architecture='arm64', *, max_packages=512, max_states=100000):
    """Resolve a complete declared runtime closure with deterministic backtracking.

    Alternatives retain declaration order; candidates already selected are
    preferred, then an exact package name, then virtual providers by name. All
    packages come from the supplied authenticated index and target ARM64/all.
    Essential packages with no declared dependency are not implicitly added.
    """
    if architecture != 'arm64':
        raise ValueError('this runtime profile resolves ARM64/all only')
    if not isinstance(records, dict) or not isinstance(seeds, (list, tuple)) or not seeds:
        raise ValueError('package records and nonempty explicit seed list are required')
    if type(max_packages) is not int or not 1 <= max_packages <= 4096 or type(max_states) is not int or max_states <= 0:
        raise ValueError('invalid dependency resolver budget')
    providers = {}
    usable = {}
    for key, record in records.items():
        if not isinstance(record, dict) or record.get('Package') != key or not re.fullmatch(NAME, key):
            raise ValueError('invalid package record identity')
        if record.get('Architecture') not in (architecture, 'all'):
            continue
        if not re.fullmatch(VERSION, record.get('Version', '')):
            raise ValueError('invalid package record version')
        usable[key] = record
        for name in _provided(record):
            providers.setdefault(name, []).append(key)
    pending = []
    for seed in seeds:
        if not isinstance(seed, str) or not seed.strip():
            raise ValueError('seed relations must be nonempty strings')
        pending.extend(('<seed>', 'seed', expression, alternatives) for expression, alternatives in _groups(seed))
    stack = [(pending, {}, [])]
    states, last_failure = 0, None
    while stack:
        pending, selected, edges = stack.pop()
        states += 1
        if states > max_states:
            raise RuntimeError('dependency search exceeded its explicit state budget')
        if not pending:
            return {'packages': [dict(selected[name]) for name in sorted(selected)], 'edges': edges}
        origin, kind, expression, alternatives = pending[0]
        remaining = pending[1:]
        options = []
        for alternative, atom in alternatives:
            name = atom[0]
            candidate_names = set(providers.get(name, ()))
            if name in usable:
                candidate_names.add(name)
            order = sorted(candidate_names, key=lambda candidate: (candidate not in selected, candidate != name, candidate))
            for candidate in order:
                record = usable[candidate]
                if _record_matches(record, atom, architecture):
                    options.append((alternative, record))
        if not options:
            last_failure = origin + ': ' + expression
        for alternative, record in reversed(options):
            name = record['Package']
            if _conflict(record, selected, architecture):
                last_failure = origin + ': ' + expression + ' (conflict)'
                continue
            next_selected = selected
            next_pending = remaining
            if name not in selected:
                if len(selected) >= max_packages:
                    last_failure = origin + ': ' + expression + ' (package budget)'
                    continue
                next_selected = dict(selected); next_selected[name] = record
                dependencies = []
                for field in ('Pre-Depends', 'Depends'):
                    dependencies.extend((name, field, relation, choices)
                                        for relation, choices in _groups(record.get(field, '')))
                next_pending = dependencies + remaining
            edge = {'from': origin, 'kind': kind, 'expression': expression, 'alternative': alternative,
                    'selected': name, 'version': record['Version']}
            stack.append((next_pending, next_selected, edges + [edge]))
    raise ValueError('no complete ARM64 dependency closure; unsatisfied ' + str(last_failure))
