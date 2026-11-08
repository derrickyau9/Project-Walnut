"""Read-only, offline HF-Sim journal/output diagnostics; never repair or replay."""
from __future__ import annotations

import argparse
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid

from camera.calibration import CalibrationError, validate_package
from camera.calibration.store import _read as read_calibration_package

_CAPTURE_STATES = {'accepted', 'capturing', 'complete', 'failed', 'cancelled', 'unknown'}
_UNRESOLVED = {'accepted', 'capturing', 'unknown'}
_JOB_STATES = {'pending', 'running', 'paused', 'completed', 'cancelled', 'failed', 'unknown'}
_STEP_STATES = {'submitting', 'accepted', 'capturing', 'completed', 'cancelled', 'failed', 'unknown'}
_JOB_ID = re.compile(r'[A-Za-z0-9._-]{1,100}\Z')
_CAVEAT = ('Database copies and output files are separate observations. A live service or an '
           'uncoordinated writer can change them during inspection; findings are not a '
           'completion, hardware-validation, or safe-retry guarantee.')


def _uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def _links(path):
    """Check every existing component, including Windows junction/reparse points."""
    for component in (*reversed(path.parents), path):
        try:
            info = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            return True
    return False


def _issue(issues, code, *, severity='error', **context):
    # Messages from records, SQLite, exceptions, and DNG descriptions are never echoed.
    issues.append({'code': code, 'severity': severity, **context})


def _signature(path):
    try:
        info = path.stat()
        return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    except FileNotFoundError:
        return None


@contextmanager
def _probe_lock(path):
    """Use CameraService's OS locking protocol without creating/writing its file."""
    stream, held = None, False
    try:
        if _links(path):
            yield 'unsafe_path'
            return
        try:
            stream = path.open('rb')
        except FileNotFoundError:
            yield 'missing'
            return
        except OSError:
            yield 'unavailable'
            return
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = True
        except OSError:
            yield 'held_or_unavailable'
            return
        yield 'acquired'
    finally:
        if stream is not None:
            if held:
                try:
                    if os.name == 'nt':
                        stream.seek(0)
                        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            stream.close()


def _database(path, queries, issues, scope):
    """Read source bytes only. SQLite may create sidecars solely in our temp dir."""
    report = {'state': 'unavailable', 'integrity': 'not_checked', 'snapshot': 'not_taken'}
    rows = {name: [] for name in queries}
    components = [path, Path(str(path) + '-wal'), Path(str(path) + '-shm'),
                  Path(str(path) + '-journal')]
    before = copied = None
    try:
        if any(_links(component) for component in components):
            _issue(issues, 'unsafe_journal_path', scope=scope)
            return report, rows
        if not path.is_file():
            _issue(issues, 'journal_missing', scope=scope)
            return report, rows
        before = [_signature(component) for component in components]
        with tempfile.TemporaryDirectory(prefix='hf-diagnostics-') as temporary:
            target = Path(temporary) / 'snapshot.sqlite3'
            shutil.copyfile(path, target)
            for suffix in ('-wal', '-journal'):
                source = Path(str(path) + suffix)
                if source.is_file():
                    shutil.copyfile(source, Path(str(target) + suffix))
            copied = [_signature(component) for component in components]
            with closing(sqlite3.connect(target.as_uri() + '?mode=ro', uri=True, timeout=0)) as db:
                db.execute('PRAGMA query_only=ON')
                db.execute('BEGIN')
                integrity = db.execute('PRAGMA integrity_check').fetchall()
                report['integrity'] = 'ok' if integrity == [('ok',)] else 'failed'
                if report['integrity'] != 'ok':
                    _issue(issues, 'sqlite_integrity_failed', scope=scope)
                else:
                    for name, query in queries.items():
                        rows[name] = db.execute(query).fetchall()
                    report['state'] = 'ok'
    except sqlite3.Error as exc:
        if getattr(exc, 'sqlite_errorcode', None) == getattr(sqlite3, 'SQLITE_READONLY_ROLLBACK', 776):
            report['integrity'] = 'recovery_required'
            _issue(issues, 'sqlite_recovery_required', scope=scope)
        else:
            report['integrity'] = 'failed'
            _issue(issues, 'sqlite_unreadable_or_schema_invalid', scope=scope)
    except (OSError, ValueError):
        _issue(issues, 'journal_unreadable', scope=scope)
    finally:
        if before is not None:
            try:
                after = [_signature(component) for component in components]
                report['snapshot'] = 'stable' if before == copied == after else 'changed'
            except OSError:
                report['snapshot'] = 'changed'
            if report['snapshot'] == 'changed':
                # A mixed live copy cannot establish source corruption or orphanhood.
                report['state'] = 'inconclusive'
                report['integrity'] = 'inconclusive'
                for name in rows:
                    rows[name] = []
                issues[:] = [item for item in issues if not (item.get('scope') == scope
                             and item['code'].startswith('sqlite_'))]
                _issue(issues, 'journal_changed_during_snapshot', severity='warning', scope=scope)
    return report, rows


def _json_object(raw):
    value = json.loads(raw)
    if type(value) is not dict:
        raise ValueError('object required')
    return value


def _dng(path, expected_id):
    """Use the shared bounded reader; never emit arbitrary DNG metadata."""
    from camera.dng.inspect import inspect_dng
    result = {'state': 'invalid', 'check': 'basic_classic_tiff_dng'}
    try:
        structure = inspect_dng(path)
        result.update(state='valid', width=structure['width'], height=structure['height'],
                      request_identity=('matches' if structure['request_id'] == expected_id
                                        else 'mismatch_or_missing'))
    except (OSError, ValueError):
        pass
    return result


def _captures(rows, photos, issues):
    records = {}
    summaries = []
    for index, (rid, raw, status) in enumerate(rows):
        context = {'request_id': rid} if _uuid(rid) else {'record_index': index}
        if not _uuid(rid):
            _issue(issues, 'invalid_request_id', **context)
            continue
        if status not in _CAPTURE_STATES:
            _issue(issues, 'invalid_capture_status', **context)
            status = 'invalid'
        summary = {**context, 'status': status}
        try:
            record = _json_object(raw)
            if record.get('request_id') != rid or record.get('status') != status:
                _issue(issues, 'capture_record_mismatch', **context)
        except (ValueError, TypeError, RecursionError):
            record = {}
            _issue(issues, 'invalid_capture_record_json', **context)
        expected = photos / (rid + '.dng')
        recorded = record.get('file_path')
        if recorded is None:
            summary['recorded_path'] = 'absent'
            if status == 'complete':
                _issue(issues, 'complete_record_path_missing', **context)
        elif not isinstance(recorded, str):
            summary['recorded_path'] = 'invalid'
            _issue(issues, 'invalid_output_path', **context)
        else:
            try:
                path = Path(recorded)
                if not path.is_absolute():
                    path = photos.parent / path
                path = Path(os.path.abspath(path))
                if not path.is_relative_to(photos):
                    summary['recorded_path'] = 'outside_photos'
                    _issue(issues, 'output_path_outside_photos', **context)
                elif _links(path):
                    summary['recorded_path'] = 'unsafe_link'
                    _issue(issues, 'unsafe_output_path', **context)
                elif path != expected:
                    summary['recorded_path'] = 'unexpected_filename'
                    _issue(issues, 'output_path_request_mismatch', **context)
                else:
                    summary['recorded_path'] = 'expected'
            except (OSError, ValueError, RuntimeError):
                summary['recorded_path'] = 'invalid'
                _issue(issues, 'invalid_output_path', **context)
        if status in _UNRESOLVED:
            _issue(issues, 'unresolved_capture', severity='warning', **context)
        records[rid] = summary
        summaries.append(summary)
    return records, summaries


def _files(photos, records, issues, *, journal_available=True):
    files, seen_final = [], set()
    try:
        if _links(photos):
            _issue(issues, 'unsafe_photos_path')
            return files
        if not photos.is_dir():
            _issue(issues, 'photos_directory_missing')
        else:
            for index, path in enumerate(sorted(photos.iterdir())):
                suffix = '.dng.partial' if path.name.endswith('.dng.partial') else '.dng' if path.name.endswith('.dng') else None
                rid = path.name[:-len(suffix)] if suffix else None
                if not _uuid(rid):
                    _issue(issues, 'unrecognized_output_entry', severity='warning', entry_index=index)
                    continue
                kind = 'partial' if suffix == '.dng.partial' else 'final'
                context = {'request_id': rid, 'kind': kind}
                relation = records.get(rid)
                entry = {**context, 'path': rid + suffix,
                         'journal_status': relation['status'] if relation else None,
                         'relation': ('recorded_request' if relation else 'orphan' if journal_available
                                      else 'journal_unavailable')}
                if _links(path) or not path.is_file():
                    _issue(issues, 'unsafe_output_entry', **context)
                    entry['state'] = 'not_read'
                else:
                    if kind == 'final':
                        seen_final.add(rid)
                        entry['dng'] = _dng(path, rid)
                        if entry['dng']['state'] != 'valid':
                            _issue(issues, 'invalid_dng_structure', **context)
                        elif entry['dng'].get('request_identity') != 'matches':
                            _issue(issues, 'dng_request_identity_unconfirmed', **context)
                    if relation is None and journal_available:
                        _issue(issues, 'orphan_' + kind + '_file', severity='warning', **context)
                    elif relation is not None and kind == 'final' and relation['status'] != 'complete':
                        _issue(issues, 'final_file_without_confirmed_completion', severity='warning', **context)
                    if kind == 'partial':
                        _issue(issues, 'partial_file', severity='warning', **context)
                files.append(entry)
        for rid, record in records.items():
            if record['status'] == 'complete' and rid not in seen_final:
                _issue(issues, 'missing_complete_file', request_id=rid)
    except OSError:
        _issue(issues, 'output_directory_unreadable')
    return files


def _workflow(path, records, issues):
    db, rows = _database(path, {
        'jobs': 'SELECT job_id,status FROM jobs ORDER BY job_id',
        'steps': 'SELECT job_id,step,request_id,status FROM steps ORDER BY job_id,step',
    }, issues, 'workflow')
    jobs, steps, known_jobs = [], [], set()
    for index, (job_id, status) in enumerate(rows['jobs']):
        if not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None:
            _issue(issues, 'invalid_workflow_job_id', job_index=index)
            continue
        known_jobs.add(job_id)
        if status not in _JOB_STATES:
            _issue(issues, 'invalid_workflow_job_status', job_id=job_id)
            status = 'invalid'
        jobs.append({'job_id': job_id, 'status': status})
        if status in {'pending', 'running', 'paused', 'unknown'}:
            _issue(issues, 'unresolved_workflow_job', severity='warning', job_id=job_id)
    for index, (job_id, step, rid, status) in enumerate(rows['steps']):
        if (not isinstance(job_id, str) or _JOB_ID.fullmatch(job_id) is None
                or type(step) is not int or step < 0 or not _uuid(rid)):
            _issue(issues, 'invalid_workflow_step_identity', step_index=index)
            continue
        context = {'job_id': job_id, 'step': step, 'request_id': rid}
        if status not in _STEP_STATES:
            _issue(issues, 'invalid_workflow_step_status', **context)
            status = 'invalid'
        capture = records.get(rid)
        camera_status = capture['status'] if capture else None
        steps.append({**context, 'status': status, 'camera_status': camera_status})
        if job_id not in known_jobs:
            _issue(issues, 'orphan_workflow_step', **context)
        if status in {'submitting', 'accepted', 'capturing', 'unknown'}:
            _issue(issues, 'unresolved_workflow_request', severity='warning', **context)
        if status == 'completed' and camera_status != 'complete':
            _issue(issues, 'workflow_completion_unconfirmed', **context)
        if camera_status == 'unknown':
            _issue(issues, 'workflow_capture_unknown', severity='warning', **context)
    return {'journal': db, 'jobs': jobs, 'steps': steps}


def _calibration(root, issues):
    report = {'state': 'checked', 'packages': []}
    try:
        if _links(root):
            _issue(issues, 'unsafe_calibration_path')
            report['state'] = 'unavailable'
            return report
        if not root.is_dir():
            _issue(issues, 'calibration_root_missing')
            report['state'] = 'unavailable'
            return report
        # Walk without following directory symlinks or reparse points.
        def traversal_error(error):
            raise error
        for current, dirs, names in os.walk(root, followlinks=False, onerror=traversal_error):
            folder = Path(current)
            for name in sorted(dirs[:]):
                if _links(folder / name):
                    dirs.remove(name)
                    _issue(issues, 'unsafe_calibration_entry')
            dirs.sort()
            for name in sorted(names):
                path = folder / name
                relative = path.relative_to(root)
                entry = {'entry_index': len(report['packages']), 'state': 'invalid'}
                report['packages'].append(entry)
                if _links(path) or not path.is_file():
                    _issue(issues, 'unsafe_calibration_entry', entry_index=entry['entry_index'])
                    continue
                if len(relative.parts) != 3 or path.suffix != '.json':
                    _issue(issues, 'unexpected_calibration_entry', severity='warning', entry_index=entry['entry_index'])
                    continue
                try:
                    sensor, package_id, _ = relative.parts
                    package = validate_package(read_calibration_package(path), expected_sensor_id=sensor)
                    if package['package_id'] != package_id or package['package_version'] != path.stem:
                        raise CalibrationError('identity_mismatch', 'path identity mismatch')
                    entry.update(state='valid', sensor_id=package['sensor_id'],
                                 package_id=package['package_id'], package_version=package['package_version'],
                                 status=package['status'], hardware_calibrated=False)
                except CalibrationError as exc:
                    _issue(issues, 'calibration_' + exc.code, entry_index=entry['entry_index'])
    except OSError:
        report['state'] = 'unavailable'
        _issue(issues, 'calibration_unreadable')
    return report


def diagnose(data_dir, *, workflow_journal=None, calibration_root=None, offline_only=False):
    """Return deterministic allowlisted data; never construct a service or mutate input."""
    root = Path(os.path.abspath(os.fspath(data_dir)))
    # Accept both CameraService roots and the workflow CLI's parent data directory.
    if not (root / 'journal.sqlite3').exists() and (root / 'simulator' / 'journal.sqlite3').exists():
        root = root / 'simulator'
    issues = []
    report = {'schema_version': 1, 'read_only': True, 'hardware_validation': False,
              'data_dir': str(root), 'consistency': {'caveat': _CAVEAT},
              'camera_journal': {'state': 'not_checked'}, 'captures': [], 'files': [],
              'workflow': {'state': 'not_requested'}, 'calibration': {'state': 'not_requested'},
              'issues': issues}
    try:
        if _links(root):
            _issue(issues, 'unsafe_data_root')
            report['consistency']['service_lock'] = 'unsafe_path'
        else:
            with _probe_lock(root / '.service.lock') as lock_state:
                report['consistency']['service_lock'] = lock_state
                report['consistency']['observation'] = ('service_lock_held_during_scan'
                                                        if lock_state == 'acquired' else 'uncoordinated_snapshot')
                if lock_state != 'acquired':
                    _issue(issues, 'service_may_be_active', severity='warning')
                if offline_only and lock_state != 'acquired':
                    _issue(issues, 'offline_exclusion_unavailable')
                else:
                    photos = root / 'photos'
                    camera, rows = _database(root / 'journal.sqlite3', {
                        'captures': 'SELECT request_id,record_json,status FROM captures ORDER BY request_id',
                    }, issues, 'camera')
                    report['camera_journal'] = camera
                    records, report['captures'] = _captures(rows['captures'], photos, issues)
                    report['files'] = _files(photos, records, issues, journal_available=camera['state'] == 'ok')
                    workflow = Path(workflow_journal) if workflow_journal is not None else None
                    if workflow is None:
                        for candidate in (root / 'workflows.sqlite3', root / 'workflow.sqlite3',
                                          root.parent / 'workflow.sqlite3'):
                            if candidate.is_file():
                                workflow = candidate
                                break
                    if workflow is not None:
                        report['workflow'] = _workflow(workflow.absolute(), records, issues)
                    if calibration_root is not None:
                        report['calibration'] = _calibration(Path(calibration_root).absolute(), issues)
    except (OSError, ValueError, RuntimeError):
        _issue(issues, 'inspection_unavailable')
    issues.sort(key=lambda item: json.dumps(item, sort_keys=True))
    report['summary'] = {'errors': sum(i['severity'] == 'error' for i in issues),
                         'warnings': sum(i['severity'] == 'warning' for i in issues)}
    report['ok'] = not issues
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', required=True, help='existing CameraService or workflow CLI data directory')
    parser.add_argument('--workflow-journal', help='existing workflow journal (otherwise auto-detected beside the camera journal or in its parent)')
    parser.add_argument('--calibration-root', help='existing calibration package store; validate without creating it')
    parser.add_argument('--offline-only', action='store_true', help='inspect only when the existing camera service lock can be held')
    args = parser.parse_args(argv)
    report = diagnose(args.data_dir, workflow_journal=args.workflow_journal,
                      calibration_root=args.calibration_root, offline_only=args.offline_only)
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    return 0 if report['ok'] else 2 if report['summary']['errors'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
