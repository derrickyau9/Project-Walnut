"""Obtain exact 8.0 sys source, verify release signature, extract only 7 files."""
import base64
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

HERE = Path(__file__).resolve().parent
DEST = HERE / "openbsd-8.0-source"
DEST.mkdir(exist_ok=True)
ROOT = "https://cdn.openbsd.org/pub/OpenBSD/8.0/"
for name in ("SHA256", "SHA256.sig", "openbsd-80-base.pub", "sys.tar.gz"):
    dst = DEST / name
    if dst.exists() and name == "sys.tar.gz":
        continue
    with urllib.request.urlopen(ROOT + name, timeout=60) as response, dst.open("wb") as stream:
        while block := response.read(1024 * 1024):
            stream.write(block)
key_data = (DEST / "openbsd-80-base.pub").read_bytes()
assert hashlib.sha256(key_data).hexdigest() == "fd6c45a7d0ff1755b56cb4964464ea8a8368523da0025e875c4cfe4e46c0fdd3"
key = base64.b64decode(key_data.splitlines()[1])
signed = (DEST / "SHA256.sig").read_bytes().splitlines(keepends=True)
signature = base64.b64decode(signed[1])
payload = b"".join(signed[2:])
assert len(key) == 42 and len(signature) == 74 and key[:10] == signature[:10]
Ed25519PublicKey.from_public_bytes(key[10:]).verify(signature[10:], payload)
assert payload == (DEST / "SHA256").read_bytes()
archive = DEST / "sys.tar.gz"
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
assert f"SHA256 (sys.tar.gz) = {digest}\n".encode() in payload
paths = ["sys/arch/arm64/stand/efiboot/efiboot.c", "sys/arch/arm64/stand/efiboot/efidev.c",
         "sys/arch/arm64/stand/efiboot/conf.c", "sys/arch/arm64/stand/efiboot/Makefile",
         "sys/stand/boot/boot.c", "sys/stand/boot/cmd.c", "sys/stand/boot/vars.c",
         "sys/arch/arm64/conf/RAMDISK", "sys/dev/fdt/simplefb.c"]
files = {}
with tarfile.open(archive) as tar:
    for name in paths:
        matches = [member for member in tar.getmembers() if member.name == name]
        assert len(matches) == 1 and matches[0].isfile(), (name, matches)
        data = tar.extractfile(matches[0]).read()
        (DEST / Path(name).name).write_bytes(data)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
result = {"status": "official-8.0-source-signature-and-exact-file-extraction-passed",
          "release_root": ROOT, "archive_bytes": archive.stat().st_size,
          "archive_sha256": digest, "signature_verified": True, "files": files,
          "physical_device_access": False}
(DEST / "VERIFIED.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps({"status": result["status"], "files": len(files), "archive_sha256": digest}))
