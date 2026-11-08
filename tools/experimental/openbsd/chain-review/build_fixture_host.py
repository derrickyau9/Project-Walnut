"""Copy reviewed fixture trees to the existing isolated task builder."""
import hashlib
import json
from pathlib import Path
import sys
import uuid

base = Path(__file__).resolve().parent
workspace = base.parents[2]
sys.path.insert(0, str(workspace))
from tools import freebsd_vm as vm

VM = Path("/home/dy09/hf-task-freebsd-r10/vm-001")
out = base / "efi-chain-fixture-001"
guest = "/tmp/daos-openbsd-chain-" + uuid.uuid4().hex
records = []
def record(result):
    records.append(result)
    (out / "native-transport.json").write_text(json.dumps(records, indent=2) + "\n")
    assert result.get("returncode", 0) == 0, result
    return result
record(vm.ssh_command(VM, "mkdir -m 700 " + guest, timeout=30))
record(vm.copy_to(VM, out / "trees.tar.gz", guest + "/trees.tar.gz", timeout=90))
record(vm.copy_to(VM, base / "build_fixture_native.py", guest + "/build.py", timeout=30))
digest = hashlib.sha256((out / "trees.tar.gz").read_bytes()).hexdigest()
result = record(vm.ssh_command(VM, "/usr/local/bin/python3.12 -I -B " + guest + "/build.py " + guest + " " + digest, timeout=180))
for name in ("chain.img", "build-commands.json"):
    record(vm.copy_from(VM, guest + "/" + name, out / name, timeout=120))
(out / "guest-directory.txt").write_text(guest + "\n")
print(result["stdout"])
