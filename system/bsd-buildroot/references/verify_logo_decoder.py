#!/usr/bin/env python3
"""Run NetBSD STBI against the actual supplied logo using a host compiler."""
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

here = Path(__file__).resolve().parent
asset_dir = here.parents[2] / "assets" / "boot-logo"
provenance = json.loads((asset_dir / "provenance.json").read_text())
with tempfile.TemporaryDirectory(prefix="daos-netbsd-logo-") as tmp:
    binary = Path(tmp) / "decode-logo"
    pixels = Path(tmp) / "logo.rgba"
    subprocess.run(["cc", "-O2", str(here / "decode_logo.c"), "-o", str(binary), "-lm"], check=True)
    result = subprocess.run([str(binary), str(asset_dir / "DaOS-logo.png"), str(pixels)],
                            check=True, capture_output=True, text=True)
    receipt = json.loads(result.stdout)
    digest = hashlib.sha256(pixels.read_bytes()).hexdigest()
    assert digest == provenance["output"]["decoded_RGBA_pixel_sha256"]

receipt.update({
    "format": "DaOS-netbsd-boot-logo-decoder-check-v1",
    "test_scope": "Host execution of upstream STBI decoder, not a target kernel or boot test.",
    "decoded_RGBA_pixel_sha256": digest,
    "matches_reviewed_asset_pixels": True,
    "conversion_required": False,
    "recommended_framebuffer_depth": 32,
    "kernel_boot_verified": False,
    "physical_board_boot_verified": False,
})
(here / "NetBSD-logo-decoder-verification.json").write_text(json.dumps(receipt, indent=2) + "\n")
print(json.dumps(receipt, indent=2))
