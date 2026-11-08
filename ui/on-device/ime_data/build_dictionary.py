"""Rebuild DaOS's offline index from the pinned Apache-2.0 Rime source.

Run with Python 3.11+: python ui/on-device/ime_data/build_dictionary.py
The checked-in source is preferred. --fetch downloads exactly the pinned commit.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import urllib.request

COMMIT = '0c6861ef7420ee780270ca6d993d18d4101049d0'
ROOT = Path(__file__).resolve().parent
REPOSITORY = 'https://github.com/rime/rime-pinyin-simp'


def build(fetch=False):
    ROOT.mkdir(parents=True, exist_ok=True)
    if fetch:
        for source, destination in [('pinyin_simp.dict.yaml', 'pinyin_simp.dict.yaml'),
                                    ('LICENSE', 'LICENSE.Apache-2.0'), ('AUTHORS', 'AUTHORS')]:
            data = urllib.request.urlopen(
                'https://raw.githubusercontent.com/rime/rime-pinyin-simp/' + COMMIT + '/' + source,
                timeout=30).read()
            (ROOT / destination).write_bytes(data)
    source = ROOT / 'pinyin_simp.dict.yaml'
    entries = {}
    for line in source.read_text(encoding='utf-8').splitlines():
        if '\t' not in line or line.startswith('#'):
            continue
        parts = line.split('\t')
        if len(parts) != 3 or not re.fullmatch('[a-z ]+', parts[1]):
            continue
        text, spelling, weight = parts[0], parts[1], int(parts[2])
        marker = (spelling.replace(' ', ''), text, spelling)
        entries[marker] = max(weight, entries.get(marker, 0))
    # Project-maintained photographic vocabulary supplements, never replaces,
    # the complete 65k-entry upstream dictionary.
    overlay = ROOT / 'camera_vocabulary.tsv'
    for line in overlay.read_text(encoding='utf-8').splitlines():
        if not line or line.startswith('#'):
            continue
        text, spelling, weight = line.split('\t')
        entries[(spelling.replace(' ', ''), text, spelling)] = int(weight)
    destination = ROOT / 'pinyin_simp.sqlite3'
    temporary = destination.with_suffix('.sqlite3.new')
    if temporary.exists():
        temporary.unlink()
    connection = sqlite3.connect(temporary)
    connection.execute('CREATE TABLE entries (key TEXT NOT NULL,text TEXT NOT NULL,'
                       'spelling TEXT NOT NULL,weight INTEGER NOT NULL)')
    connection.executemany('INSERT INTO entries VALUES (?,?,?,?)',
                           [(*key, weight) for key, weight in sorted(entries.items())])
    connection.execute('CREATE INDEX by_key_weight ON entries(key,weight DESC,text)')
    connection.execute('CREATE TABLE metadata (name TEXT PRIMARY KEY,value TEXT NOT NULL)')
    connection.executemany('INSERT INTO metadata VALUES (?,?)',
                           [('entry_count', str(len(entries))), ('source_commit', COMMIT)])
    connection.commit()
    connection.execute('VACUUM')
    connection.close()
    temporary.replace(destination)
    manifest = {'repository': REPOSITORY, 'commit': COMMIT, 'license': 'Apache-2.0',
                'upstream_source': 'pinyin_simp.dict.yaml',
                'upstream_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                'local_additions': 'camera_vocabulary.tsv', 'entry_count': len(entries),
                'index_sha256': hashlib.sha256(destination.read_bytes()).hexdigest(),
                'runtime_dependencies': ['Python sqlite3 (standard library)'],
                'modification': 'Converted tab-separated Rime entries into a read-only SQLite index.'}
    (ROOT / 'SOURCE.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'entries': len(entries), 'bytes': destination.stat().st_size,
                      'path': str(destination)}, ensure_ascii=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true')
    build(parser.parse_args().fetch)
