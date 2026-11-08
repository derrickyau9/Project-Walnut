#!/usr/bin/env python3
"""Verify the downloaded NetBSD release metadata, without fetching source sets."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

here = Path(__file__).resolve().parent
manifest = here / "NetBSD-11.1_hashes.asc"
key = here / "security-officer@netbsd.org.asc"
checksums = here / "NetBSD-11.1-source-sets-SHA512"
with tempfile.TemporaryDirectory(prefix="daos-netbsd-key-") as tmp:
    base = ["gpg", "--homedir", tmp, "--batch"]
    subprocess.run(base + ["--import", str(key)], check=True, capture_output=True)
    result = subprocess.run(base + ["--status-fd", "1", "--verify", str(manifest)],
                            check=True, capture_output=True, text=True)
    fingerprint = re.search(r"\[GNUPG:\] VALIDSIG ([0-9A-F]+)", result.stdout).group(1)
    print(result.stderr.strip())
    print(result.stdout.strip())

expected = dict(re.findall(r"SHA512 \(([^)]+)\) = ([0-9a-f]{128})", checksums.read_text()))
signed_text = manifest.read_text()
for name, digest in expected.items():
    pattern = r"SHA512 \((?:\./)?NetBSD-11\.1/source/sets/" + re.escape(name) + r"\) = " + digest
    if not re.search(pattern, signed_text):
        raise RuntimeError(f"Official SHA512 entry absent from signed release manifest: {name}")
    print(f"Signed source hash matches: {name} {digest}")

receipt = {
    "format": "DaOS-upstream-metadata-verification-v1",
    "signature_verified": True,
    "signing_fingerprint": fingerprint,
    "key_trust_basis": "Official HTTPS release announcement links; fingerprint not independently attested.",
    "source_archive_bytes_downloaded_or_verified": False,
    "source_hashes_match_signed_manifest": True,
    "metadata_sha512": {p.name: hashlib.sha512(p.read_bytes()).hexdigest()
                         for p in (manifest, key, checksums)},
    "archives": expected,
}
(here / "NetBSD-11.1-metadata-verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
