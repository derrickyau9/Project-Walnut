"""Verify vendored native dependency bytes against the local source manifest."""
import hashlib
import json
from pathlib import Path


def verify(root=None):
    root = Path(root) if root is not None else Path(__file__).resolve().parents[1] / 'camera/native/third_party'
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    checked = []
    for item in manifest['files']:
        path = (root / item['path']).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('invalid or missing native dependency path')
        if hashlib.sha256(path.read_bytes()).hexdigest() != item['sha256']:
            raise ValueError('native dependency checksum mismatch: ' + item['path'])
        checked.append(item['path'])
    return {'verified': checked, 'sqlite_version': manifest['sqlite']['version'],
            'nlohmann_json_version': manifest['nlohmann_json']['version']}


if __name__ == '__main__':
    print(json.dumps(verify(), indent=2))
