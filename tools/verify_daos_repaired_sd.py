"""Read-only recheck of the identified card against recovery 005; never writes SD."""
import ctypes
import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RECOVERY = ROOT / 'build/DaOS_WiFi_Recovery_005'
spec = importlib.util.spec_from_file_location('recovery', ROOT / 'tools/repair_daos_sd.py')
recovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(recovery)


def main():
    output = ROOT / 'build/DaOS_WiFi_005_Diagnostic/card-current-readonly.json'
    record = {'started': datetime.now(timezone.utc).isoformat(), 'sd_bytes_written': 0}
    handle = None
    try:
        record['target'] = recovery.sd.identity()
        expected = json.loads((RECOVERY / 'apply-status.json').read_text())['readback_sha256']
        handle = recovery.sd.open_device(r'\\.\PhysicalDrive11', 0x80000000)
        length = ctypes.c_longlong()
        recovery.sd.ioctl(handle, 0x0007405C, ctypes.byref(length), 8)
        if length.value != recovery.sd.CARD_SIZE:
            raise ValueError('Opened card size changed')
        digest = hashlib.sha256()
        total = 0
        while total < recovery.SIZE:
            data = recovery.read(handle, min(recovery.BLOCK, recovery.SIZE-total))
            digest.update(data)
            total += len(data)
        record.update(bytes_read=total, sha256=digest.hexdigest(), expected_sha256=expected)
        record['status'] = 'matches-repaired-card' if digest.hexdigest() == expected else 'card-changed'
    except BaseException as error:
        record.update(status='failed', error=str(error))
        raise
    finally:
        if handle is not None:
            recovery.sd.k.CloseHandle(handle)
        record['completed'] = datetime.now(timezone.utc).isoformat()
        output.write_text(json.dumps(record, indent=2)+'\n')
        print(json.dumps(record))


if __name__ == '__main__':
    main()
