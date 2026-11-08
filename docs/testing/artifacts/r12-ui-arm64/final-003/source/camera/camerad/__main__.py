"""Independent basic simulated capture CLI; no DaOS workflow dependency."""
import argparse
import json
import time
import uuid

from .service import CameraError, CameraService, TERMINAL


def main(argv=None):
    parser = argparse.ArgumentParser(description='Independent HF-Sim basic capture (synthetic pixels only)')
    parser.add_argument('--data-dir', default='var/hf-local')
    parser.add_argument('--backend', choices=['python', 'native'], default='python')
    parser.add_argument('--native-worker', help='Explicit path to the compiled synthetic capture worker')
    parser.add_argument('--authority', choices=['python', 'native'], default='python')
    parser.add_argument('--native-daemon', help='Explicit compiled hf-camerad executable path')
    sub = parser.add_subparsers(dest='action', required=True)
    capture = sub.add_parser('capture')
    capture.add_argument('--request-id', default=None)
    capture.add_argument('--exposure-us', type=int, help='default: persisted device exposure')
    capture.add_argument('--gain', type=float, help='default: persisted device gain')
    capture.add_argument('--mode', choices=['sim-small', 'sim-full'], help='default: persisted device mode')
    capture.add_argument('--fault', choices=['delay', 'storage_full', 'disconnect', 'corrupt_frame'])
    query = sub.add_parser('query')
    query.add_argument('request_id')
    sub.add_parser('capabilities', help='show synthetic device capabilities without acquiring control')
    sub.add_parser('status', help='show camera state and the latest request, including uncertain outcomes')
    listing = sub.add_parser('list', help='page through completed retained captures without taking a photograph')
    listing.add_argument('--limit', type=int, default=20)
    listing.add_argument('--cursor')
    asset = sub.add_parser('asset', help='verify and describe a committed DNG without exposing pixel bytes')
    asset.add_argument('request_id')
    export = sub.add_parser('export', help='copy a verified committed DNG to a new local file')
    export.add_argument('request_id')
    export.add_argument('--output', required=True, help='new file in an existing local directory; never replaced')
    configure = sub.add_parser('configure',help='durably change the device baseline without taking a photograph')
    configure.add_argument('--exposure-us',type=int)
    configure.add_argument('--gain',type=float)
    configure.add_argument('--mode',choices=['sim-small','sim-full'])
    configure.add_argument('--scene',choices=['scene','dark','flat'])
    binding = sub.add_parser('bind-calibration',help='select a verified immutable synthetic binding artifact; pixels remain unchanged')
    binding.add_argument('artifact')
    sub.add_parser('clear-calibration',help='explicitly select uncalibrated state for future captures')
    args = parser.parse_args(argv)
    if args.authority == 'native' and (args.backend != 'python' or args.native_worker is not None):
        parser.error('--authority native owns its capture worker; --backend/--native-worker select only the Python authority backend')
    if args.native_daemon is not None and args.authority != 'native':
        parser.error('--native-daemon requires --authority native')
    if args.native_worker is not None and args.backend != 'native':
        parser.error('--native-worker requires --backend native')
    changes={}
    if args.action=='configure':
        changes={name:value for name,value in [('requested_exposure_us',args.exposure_us),('requested_analogue_gain',args.gain),
            ('sensor_mode',args.mode),('simulation_scene',args.scene)] if value is not None}
        if not changes: parser.error('configure requires at least one setting')
    service, lease = None, None
    try:
        prepared_binding=None
        if args.action=='bind-calibration':
            from camera.calibration import read_binding
            prepared_binding=read_binding(args.artifact)
        if args.authority == 'native':
            from camera.camerad.native_client import NativeClient
            service = NativeClient(args.data_dir, command=[args.native_daemon] if args.native_daemon else None)
        else:
            service = CameraService(args.data_dir, backend=args.backend, native_worker=args.native_worker)
        if args.action == 'query':
            record = service.query_capture(args.request_id)
            print(json.dumps(record, allow_nan=False), flush=True)
            return 0 if record else 2
        if args.action in ('capabilities', 'status', 'list', 'asset', 'export'):
            if args.action == 'capabilities':
                result = service.capabilities()
            elif args.action == 'status':
                result = service.get_state()
            elif args.action == 'list':
                result = service.list_captures(limit=args.limit, cursor=args.cursor)
            elif args.action == 'asset':
                result = service.get_capture_asset(args.request_id)
            else:
                result = service.export_capture(args.request_id, args.output)
            print(json.dumps(result, allow_nan=False), flush=True)
            return 0
        if args.action in ('configure','bind-calibration','clear-calibration'):
            lease=service.acquire('basic-settings-cli')
            if args.action=='configure': result=service.configure(lease['token'],lease['epoch'],changes)
            elif args.action=='bind-calibration': result=service.bind_calibration(lease['token'],lease['epoch'],prepared_binding)
            else: result=service.clear_calibration(lease['token'],lease['epoch'])
            print(json.dumps(result,allow_nan=False),flush=True)
            return 0
        rid = args.request_id or str(uuid.uuid4())
        lease = service.acquire('basic-capture-cli')
        baseline=service.get_state()['settings']
        # Emit identity before submission. A caller must query this ID after
        # losing stdout or process interruption, never generate a blind retry.
        print(json.dumps({'request_id': rid, 'phase': 'submitting', 'simulated': True}), flush=True)
        record = service.capture({'request_id': rid, 'token': lease['token'],
                                  'control_epoch': lease['epoch'], 'sensor_mode': args.mode if args.mode is not None else baseline['sensor_mode'],
                                  'requested_exposure_us': args.exposure_us if args.exposure_us is not None else baseline['requested_exposure_us'],
                                  'requested_analogue_gain': args.gain if args.gain is not None else baseline['requested_analogue_gain'], 'output_format': 'dng',
                                  'simulation_scene':baseline.get('simulation_scene','scene'),
                                  'fault': args.fault})
        heartbeat_at = time.monotonic()
        try:
            while record['status'] not in TERMINAL:
                time.sleep(.05)
                if time.monotonic() - heartbeat_at >= 5:
                    service.heartbeat(lease['token'], lease['epoch'])
                    heartbeat_at = time.monotonic()
                record = service.query_capture(rid)
        except KeyboardInterrupt:
            service.cancel_capture(lease['token'], lease['epoch'], rid)
            end = time.monotonic() + 5
            while time.monotonic() < end:
                record = service.query_capture(rid)
                if record['status'] in TERMINAL:
                    break
                time.sleep(.05)
            if record['status'] not in TERMINAL:
                record = {'request_id': rid, 'status': 'unknown', 'error': 'cancellation not confirmed; query the original ID after restart'}
        print(json.dumps(record, allow_nan=False), flush=True)
        return 0 if record['status'] == 'complete' else 3
    except (CameraError, ValueError) as exc:
        print(json.dumps({'error': {'code': getattr(exc, 'code', 'invalid_request'), 'message': str(exc)}}), flush=True)
        return 2
    finally:
        if service is not None:
            if lease is not None:
                try:
                    service.release(lease['token'], lease['epoch'])
                except CameraError:
                    pass
            try:
                service.close()
            except CameraError:
                # Accepted work with unconfirmed cancellation remains in the
                # journal. A later restart marks it unknown, with no replay.
                if hasattr(service, 'abort'):
                    service.abort()


if __name__ == '__main__':
    raise SystemExit(main())
