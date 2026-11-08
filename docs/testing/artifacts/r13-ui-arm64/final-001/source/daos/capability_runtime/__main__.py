"""Execute and inspect journaled HF-Sim workflows and portable recipes."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import signal
import sys
import threading

from camera.camerad.service import CameraService, CameraError
from .bracket import BracketRunner, WorkflowError, WorkflowBusy, normalize_plan
from .recipes import FORMAT, load_recipe, normalize_recipe, save_recipe


@contextmanager
def _exclusive(path: Path):
    """Lock this CLI data directory for the lifetime of a run (OS releases on exit)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as lock:
        lock.seek(0)
        if lock.read(1) == b'':
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise WorkflowBusy('another workflow CLI owns this data directory') from exc
        try:
            yield
        finally:
            lock.seek(0)
            if os.name == 'nt':
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _plan_arguments(parser, *, exposures_required=False, exposures_target=None):
    target = parser if exposures_target is None else exposures_target
    target.add_argument('--exposures-us', nargs='+', type=int, required=exposures_required, metavar='US')
    parser.add_argument('--gain', type=float)
    parser.add_argument('--mode', choices=['sim-small', 'sim-full'])
    parser.add_argument('--kind', choices=['bracket', 'interval', 'dark', 'flat'])
    parser.add_argument('--interval-ms', type=int, help='minimum completion-to-next-shot delay for interval jobs')


def _explicit_plan(args):
    kind = args.kind or 'bracket'
    plan = {'exposures_us': args.exposures_us, 'requested_analogue_gain': 1.0 if args.gain is None else args.gain,
            'sensor_mode': args.mode or 'sim-small', 'kind': kind}
    if kind == 'interval':
        plan['interval_ms'] = 1000 if args.interval_ms is None else args.interval_ms
    return normalize_plan(plan)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Run a journaled workflow with HF-Sim (not physical hardware).')
    parser.add_argument('--data-dir', default='var/hf-workflow', help='dedicated CLI data directory (default: var/hf-workflow)')
    parser.add_argument('--backend', choices=['python', 'native'], default='python')
    parser.add_argument('--native-worker', help='Explicit compiled synthetic capture worker path')
    parser.add_argument('--authority', choices=['python', 'native'], default='python')
    parser.add_argument('--native-daemon', help='Explicit compiled hf-camerad executable path')
    sub = parser.add_subparsers(dest='command', required=True)
    run = sub.add_parser('run', help='start or reconcile a workflow')
    run.add_argument('--job', required=True, help='persistent job ID; reuse the same ID and plan to reconcile')
    source = run.add_mutually_exclusive_group(required=True)
    source.add_argument('--recipe', type=Path, help='bounded portable workflow recipe JSON file')
    _plan_arguments(run, exposures_target=source)
    inspect = sub.add_parser('inspect', help='print saved workflow journal and results')
    inspect.add_argument('--job', required=True)
    for action, help_text in [('resume', 'continue the saved plan and reconcile original request IDs'),
                              ('pause', 'persist pause intent without starting a camera'),
                              ('cancel', 'cancel remaining work and reconcile any original pending request')]:
        command = sub.add_parser(action, help=help_text)
        command.add_argument('--job', required=True)
    listing = sub.add_parser('list', help='list saved jobs without starting a camera')
    listing.add_argument('--limit', type=int, default=50)
    export = sub.add_parser('recipe-export', help='create a portable recipe file without opening a camera')
    export.add_argument('--name', required=True, help='1-80 printable characters')
    export.add_argument('--output', type=Path, required=True, help='new recipe file; existing entries are never overwritten')
    _plan_arguments(export, exposures_required=True)
    args = parser.parse_args(argv)
    if args.command == 'list' and not 1 <= args.limit <= 100:
        parser.error('--limit must be from 1 through 100')
    if args.command == 'run' and args.recipe is not None:
        if any(getattr(args, field) is not None for field in ('gain', 'mode', 'kind', 'interval_ms')):
            parser.error('--recipe cannot be combined with gain, mode, kind or interval overrides')
    try:
        if args.command == 'recipe-export':
            recipe = normalize_recipe({'format': FORMAT, 'name': args.name, 'plan': _explicit_plan(args)})
            save_recipe(recipe, args.output)
            print(json.dumps(recipe, indent=2, ensure_ascii=True, allow_nan=False))
            return 0
        plan = None
        if args.command == 'run':
            plan = load_recipe(args.recipe)['plan'] if args.recipe is not None else _explicit_plan(args)
        root = Path(args.data_dir).resolve()
        journal = root / 'workflow.sqlite3'
        if args.command != 'run' and not journal.exists():
            if args.command == 'list':
                print('[]'); return 0
            print('No workflow journal found at ' + str(journal), file=sys.stderr)
            return 1
        runner = BracketRunner(None, journal)
        if args.command == 'list':
            if not 1 <= args.limit <= 100: raise WorkflowError('list limit must be from 1 through 100')
            with runner._db() as db:
                rows = db.execute('SELECT job_id,status,error FROM jobs ORDER BY rowid DESC LIMIT ?', (args.limit,)).fetchall()
            print(json.dumps([dict(job_id=job, status=status, error=error) for job,status,error in rows], indent=2)); return 0
        if args.command == 'inspect':
            result = runner.inspect(args.job)
        elif args.command == 'pause':
            job = runner.inspect(args.job)
            if job['status'] not in {'pending','running','paused'}: raise WorkflowError('only an active or paused workflow can be paused')
            runner.set_desired(args.job, 'pause')
            result = dict(runner.inspect(args.job), desired='pause')
            print(json.dumps(result, indent=2)); return 0
        else:
            # Claim before changing controls or opening an authority. A cancel
            # command may signal the existing runner instead of competing with it.
            ownership = runner.execution()
            try: ownership.__enter__()
            except WorkflowBusy:
                if args.command != 'cancel': raise
                job = runner.inspect(args.job)
                if job['status'] in {'completed','failed','cancelled'}: raise WorkflowError('workflow is terminal; inspect it first')
                runner.set_desired(args.job, 'cancel')
                print(json.dumps(dict(runner.inspect(args.job), desired='cancel'), indent=2)); return 0
            try:
              with _exclusive(root / '.workflow.lock'):
                if args.command == 'run': runner.prepare(args.job, plan)
                runner.recover_terminal_reservation(args.job)
                job = runner.inspect(args.job)
                if job['status'] in {'completed','failed','cancelled'}:
                    result = job
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                    return {'completed':0, 'cancelled':130}.get(result['status'], 1)
                if args.command != 'run': plan = job['plan']
                desired = 'cancel' if args.command == 'cancel' else 'run'
                if runner.desired(args.job) != desired: runner.set_desired(args.job, desired)
                if args.authority == 'native':
                    from camera.camerad.native_client import NativeClient
                    service = NativeClient(root / 'simulator', command=[args.native_daemon] if args.native_daemon else None)
                else:
                    service = CameraService(root / 'simulator', backend=args.backend, native_worker=args.native_worker)
                runner.service = service
                cancelled = threading.Event()
                previous_handler = signal.getsignal(signal.SIGINT)
                def on_interrupt(signum, frame):
                    cancelled.set()
                    print('Cancellation requested; waiting for the simulator to report a final status.', file=sys.stderr)
                signal.signal(signal.SIGINT, on_interrupt)
                try:
                    result = runner._run(args.job, plan, cancelled)
                finally:
                    signal.signal(signal.SIGINT, previous_handler)
                    try:
                        service.close()
                    except CameraError as exc:
                        print('Simulator did not close cleanly: ' + str(exc), file=sys.stderr)
                        if hasattr(service, 'abort'):
                            service.abort()
            finally:
                ownership.__exit__(None, None, None)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return {'completed':0, 'cancelled':130, 'unknown':2}.get(result['status'], 1)
    except (WorkflowError, CameraError, OSError, ValueError) as exc:
        print('Workflow error: ' + str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
