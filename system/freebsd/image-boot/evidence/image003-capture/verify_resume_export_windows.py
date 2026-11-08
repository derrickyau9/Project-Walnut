"""Verify retained capture and create-only export from extracted stopped images."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import sqlite3


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_journal(folder):
    db = folder / 'state/DaOS/camera/journal.sqlite3'
    before = sha(db)
    connection = sqlite3.connect(db.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    assert connection.execute('PRAGMA integrity_check').fetchall()[0][0] == 'ok'
    rows = [dict(row) for row in connection.execute('SELECT * FROM captures')]
    meta = dict(connection.execute('SELECT key,value FROM meta'))
    connection.close()
    assert sha(db) == before
    return rows, meta, before


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--parent', type=Path, required=True)
    p.add_argument('--child', type=Path, required=True)
    p.add_argument('--parent-validation', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    assert platform.system() == 'Windows' and not args.output.exists()
    validated = json.loads(args.parent_validation.read_text())
    assert validated['passed'] is True and validated['samples'] == 12288
    extraction = json.loads((args.child / 'extraction-receipt.json').read_text())
    assert extraction['clean_stop_verified'] and all(extraction['images_unchanged'].values())
    for item in extraction['extracted']:
        assert sha(args.child / item['output']) == item['sha256']
    parent_rows, parent_meta, parent_db = read_journal(args.parent)
    child_rows, child_meta, child_db = read_journal(args.child)
    assert len(parent_rows) == len(child_rows) == 1
    assert child_rows == parent_rows
    request_id = validated['request_id']
    assert child_rows[0]['request_id'] == request_id and child_rows[0]['status'] == 'complete'
    assert child_meta['settings'] == parent_meta['settings']
    assert child_meta['authority'] == parent_meta['authority'] == 'native'
    ui = json.loads((args.child / 'state/DaOS/ui/device-ui.json').read_text())
    assert ui['request_id'] == request_id
    photos = next(s for s in extraction['slices'] if s['partition']['label'] == 'DaOS-photos')
    assert len(photos['allocated_regular_paths']) == 1
    child_dng = args.child / 'photos' / (request_id + '.dng')
    parent_dng = args.parent / 'photos' / (request_id + '.dng')
    assert sha(child_dng) == sha(parent_dng) == validated['DNG_sha256']
    exports = [item for item in extraction['extracted']
               if item['partition'] == 'DaOS-state' and item['path'].lower().endswith('.dng')]
    assert exports, 'Actual stopped-child exported DNG is required'
    for item in exports:
        assert item['sha256'] == validated['DNG_sha256']
        assert (args.child / item['output']).read_bytes() == parent_dng.read_bytes()
    report = {'format': 'DaOS-Windows-resume-export-validation-v1', 'passed': True,
              'host_system': platform.system(), 'request_id': request_id,
              'parent_capture_rows': len(parent_rows), 'child_capture_rows': len(child_rows),
              'capture_rows_unchanged': True, 'no_additional_capture': True,
              'retained_UI_request_id': ui['request_id'], 'settings_unchanged': True,
              'requested_exposure_us': validated['requested_exposure_us'],
              'actual_exposure_us': validated['actual_exposure_us'],
              'parent_journal_sha256': parent_db, 'child_journal_sha256': child_db,
              'DNG_sha256': validated['DNG_sha256'], 'DNG_bytes': child_dng.stat().st_size,
              'retained_DNG_byte_identical': True, 'export_count': len(exports),
              'exports': exports, 'all_exports_byte_identical': True,
              'parent_independent_validation_sha256': sha(args.parent_validation),
              'child_extraction_receipt_sha256': sha(args.child / 'extraction-receipt.json'),
              'source_image_sha256': extraction['source_image_sha256'],
              'child_cow_sha256': extraction['parent_cow_sha256'],
              'readonly_backing_inputs_unchanged': extraction['images_unchanged']['readonly_backing_chain'],
              'script_sha256': sha(__file__), 'new_capture_submitted': False,
              'source_images_modified': False, 'physical_camera_validated': False}
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
        output.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
