import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest
import uuid

from camera.camerad.service import CameraService, DEFAULTS


@unittest.skipUnless(importlib.util.find_spec('jsonschema'), 'optional JSON Schema validator unavailable')
class ContractTests(unittest.TestCase):
    def test_retained_page_and_asset_metadata_contracts(self):
        from jsonschema import Draft202012Validator, FormatChecker
        root = Path(__file__).resolve().parents[1] / 'daos' / 'camera-contracts' / 'v1'
        page = Draft202012Validator(json.loads((root / 'capture-list.schema.json').read_text()), format_checker=FormatChecker())
        asset = Draft202012Validator(json.loads((root / 'capture-asset.schema.json').read_text()), format_checker=FormatChecker())
        for validator in (page, asset):
            validator.check_schema(validator.schema)
        with tempfile.TemporaryDirectory() as tmp:
            service = CameraService(tmp)
            try:
                lease = service.acquire('retained-contract-test')
                request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'], control_epoch=lease['epoch'])
                service.capture(request)
                end = time.monotonic() + 10
                while service.active and time.monotonic() < end:
                    time.sleep(.01)
                result = service.list_captures(limit=1)
                page.validate(result)
                self.assertEqual(result['items'][0]['request_id'], request['request_id'])
                descriptor = service.get_capture_asset(request['request_id'])
                asset.validate(descriptor)
                self.assertEqual(descriptor['filename'], request['request_id'] + '.dng')
                self.assertEqual(descriptor['byte_count'], Path(service.query_capture(request['request_id'])['file_path']).stat().st_size)
            finally:
                service.close()

    def test_versioned_request_and_live_service_records(self):
        from jsonschema import Draft202012Validator, FormatChecker
        root = Path(__file__).resolve().parents[1] / 'daos' / 'camera-contracts' / 'v1'
        validators = [Draft202012Validator(json.loads((root / name).read_text()), format_checker=FormatChecker())
                      for name in ('capture-request.schema.json', 'capture-record.schema.json')]
        for validator in validators:
            validator.check_schema(validator.schema)
        with tempfile.TemporaryDirectory() as tmp:
            service = CameraService(tmp)
            try:
                lease = service.acquire('schema-test')
                request = dict(DEFAULTS, request_id=str(uuid.uuid4()), token=lease['token'], control_epoch=lease['epoch'])
                validators[0].validate(request)
                validators[1].validate(service.capture(request))
                end = time.monotonic() + 10
                while service.active and time.monotonic() < end:
                    time.sleep(.01)
                record = service.query_capture(request['request_id'])
                self.assertEqual(record['status'], 'complete')
                validators[1].validate(record)
                bad = dict(request, request_id=request['request_id'].upper())
                self.assertTrue(list(validators[0].iter_errors(bad)))
            finally:
                service.close()


if __name__ == '__main__':
    unittest.main()
