"""Independently decode the extracted actual-image DNG on Windows; no capture."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import platform
import sqlite3
import sys
from fractions import Fraction


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--extracted', type=Path, required=True)
    p.add_argument('--decoder-dir', type=Path, required=True)
    p.add_argument('--oracle', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--request-id', required=True)
    p.add_argument('--exposure-us', type=int, required=True)
    args = p.parse_args()
    assert platform.system() == 'Windows'
    if args.output.exists():
        raise ValueError('New report required')
    sys.path.insert(0, str(args.decoder_dir))
    import numpy as np
    import tifffile
    import rawpy
    extraction = json.loads((args.extracted / 'extraction-receipt.json').read_text())
    assert all(extraction['images_unchanged'].values())
    for entry in extraction['extracted']:
        assert sha(args.extracted / entry['output']) == entry['sha256']
    db = args.extracted / 'state/DaOS/camera/journal.sqlite3'
    before = sha(db)
    connection = sqlite3.connect(db.resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    connection.row_factory = sqlite3.Row
    assert connection.execute('PRAGMA integrity_check').fetchall()[0][0] == 'ok'
    rows = [dict(row) for row in connection.execute('SELECT * FROM captures')]
    meta = dict(connection.execute('SELECT key,value FROM meta'))
    connection.close()
    assert before == sha(db) and len(rows) == 1
    row = rows[0]
    request, record = json.loads(row['request_json']), json.loads(row['record_json'])
    assert row['request_id'] == args.request_id and row['status'] == record['status'] == 'complete'
    assert request['requested_exposure_us'] == record['actual_exposure_us'] == args.exposure_us
    assert request['requested_analogue_gain'] == record['actual_analogue_gain'] == 1
    assert request['sensor_mode'] == 'sim-small' and record['sensor_id'] == 'HF-Sim'
    assert record['backend'] == record['authority'] == meta['authority'] == 'native'
    for field in ('request_id', 'capture_id', 'frame_id'):
        assert record[field] == args.request_id
    settings = json.loads(meta['settings'])['settings']
    assert settings['requested_exposure_us'] == args.exposure_us and settings['requested_analogue_gain'] == 1
    ui = json.loads((args.extracted / 'state/DaOS/ui/device-ui.json').read_text())
    assert ui['request_id'] == args.request_id
    photos = [entry for entry in extraction['slices'] if entry['partition']['label'] == 'DaOS-photos'][0]
    assert len(photos['allocated_regular_paths']) == 1
    dngs = list((args.extracted / 'photos').rglob('*.dng'))
    assert len(dngs) == 1 and dngs[0].name == args.request_id + '.dng'
    dng = dngs[0]
    with tifffile.TiffFile(dng) as image:
        page = image.pages[0]
        pixels = page.asarray()
        description = json.loads(page.tags[270].value)
        assert description['simulated'] is True
        capture = description['capture']
        assert capture['request_id'] == capture['capture_id'] == capture['frame_id'] == args.request_id
        assert capture['actual_exposure_us'] == capture['requested_exposure_us'] == args.exposure_us
        assert capture['actual_analogue_gain'] == capture['requested_analogue_gain'] == 1
        assert description['native']['frame_sequence'] == 1
        assert page.tags[258].value == 16 and page.tags[50717].value == 16383
        assert page.tags[33422].value == bytes((0, 1, 1, 2))
        assert Fraction(*page.tags[34665].value['ExposureTime']) == Fraction(args.exposure_us, 1000000)
    specification = importlib.util.spec_from_file_location('independent_python_bayer_oracle', args.oracle)
    oracle = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(oracle)
    expected = np.asarray(oracle.generate_bayer(128, 96, args.exposure_us, 1.0), dtype='<u2').reshape(96, 128)
    assert pixels.shape == expected.shape and pixels.size == 12288 and np.array_equal(pixels, expected)
    with rawpy.imread(str(dng)) as image:
        raw_pixels = image.raw_image.copy()
        assert raw_pixels.shape == expected.shape and np.array_equal(raw_pixels, expected)
        rgb = image.postprocess(no_auto_bright=True, use_camera_wb=True, output_bps=8)
    assert rgb.shape == (96, 128, 3) and sha(db) == before
    report = {'format': 'DaOS-Windows-independent-capture-validation-v1', 'passed': True,
              'scope': 'actual source-built ARM64 DaOS image capture, independently decoded on Windows',
              'host_system': platform.system(), 'python': platform.python_version(),
              'tifffile': tifffile.__version__, 'rawpy': rawpy.__version__, 'libraw': rawpy.libraw_version,
              'samples': int(pixels.size), 'all_tifffile_samples_equal_oracle': True,
              'all_LibRaw_samples_equal_oracle': True, 'rgb_shape': list(rgb.shape),
              'request_id': args.request_id, 'capture_rows': len(rows), 'status': row['status'],
              'requested_exposure_us': args.exposure_us, 'actual_exposure_us': record['actual_exposure_us'],
              'requested_analogue_gain': 1, 'actual_analogue_gain': record['actual_analogue_gain'],
              'native_frame_sequence': description['native']['frame_sequence'], 'settings': settings,
              'UI_retained_original_request': True, 'photo_files': len(photos['allocated_regular_paths']),
              'DNG_bytes': dng.stat().st_size, 'DNG_sha256': sha(dng), 'journal_sha256': before,
              'oracle_sha256': sha(args.oracle), 'oracle_pixels_sha256': hashlib.sha256(expected.tobytes()).hexdigest(),
              'validation_script_sha256': sha(__file__), 'extraction_receipt_sha256': sha(args.extracted / 'extraction-receipt.json'),
              'source_image_sha256': extraction['source_image_sha256'], 'parent_cow_sha256': extraction['parent_cow_sha256'],
              'new_capture_submitted': False, 'source_images_modified': False,
              'physical_camera_validated': False, 'measured_color_accuracy_validated': False}
    with args.output.open('x', encoding='utf-8') as output:
        json.dump(report, output, indent=2)
        output.write('\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
