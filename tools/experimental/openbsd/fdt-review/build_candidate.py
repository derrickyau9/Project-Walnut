"""Build an offline, source-bound OpenBSD FDT candidate from stable firmware DT.

No host devices or production files are opened for writing. Power sequencing is
explicitly unresolved: stock bcm SDHC does not consume mmc-pwrseq bindings.
"""
import hashlib
import json
from pathlib import Path
import struct
from inspect_runtime import parse, display

BASE = Path(__file__).resolve().parent
SOURCE = BASE.parents[1] / "DaOS_USB_Recovery_001/wifi005-ram-analysis/physical-stable-runtime.dtb"
OUT = BASE / "candidate-002"
PROOF = "daos,openbsd-dtb-001"

def serialize(tree, padding=16384):
    strings = bytearray()
    offsets = {}
    for props in tree["nodes"].values():
        for name in props:
            if name not in offsets:
                offsets[name] = len(strings)
                strings.extend(name.encode() + b"\0")
    children = {path: [] for path in tree["nodes"]}
    for path in tree["nodes"]:
        if path != "/":
            parent = path.rsplit("/", 1)[0] or "/"
            children[parent].append(path)
    structure = bytearray()
    def node(path):
        name = "" if path == "/" else path.rsplit("/", 1)[1]
        structure.extend(struct.pack(">I", 1) + name.encode() + b"\0")
        structure.extend(b"\0" * (-len(structure) % 4))
        for key, value in tree["nodes"][path].items():
            structure.extend(struct.pack(">III", 3, len(value), offsets[key]) + value)
            structure.extend(b"\0" * (-len(structure) % 4))
        for child in children[path]:
            node(child)
        structure.extend(struct.pack(">I", 2))
    node("/")
    structure.extend(struct.pack(">I", 9))
    reserve = b"".join(struct.pack(">QQ", *entry) for entry in tree["reservations"] + [[0, 0]])
    off_structure = (40 + len(reserve) + 7) & ~7
    off_strings = off_structure + len(structure)
    total = (off_strings + len(strings) + padding + 7) & ~7
    header = struct.pack(">10I", 0xd00dfeed, total, off_structure, off_strings, 40, 17, 16, 0,
                         len(strings), len(structure))
    data = header + reserve + b"\0" * (off_structure - 40 - len(reserve)) + structure + strings
    return data + b"\0" * (total - len(data))

def main():
    original = SOURCE.read_bytes()
    assert len(original) == 61440
    assert hashlib.sha256(original).hexdigest() == "75fb0ed84d305b1d14a710b1554b26df09ce0ad740ecbb96f7d587fc4bcbe3c8"
    before = parse(original)
    # Deep copy only ordinary in-memory records; the original stays unchanged.
    tree = {"nodes": {path: dict(props) for path, props in before["nodes"].items()},
            "reservations": list(before["reservations"])}
    nodes = tree["nodes"]
    assert nodes["/soc/mmc@7e300000"]["status"] == b"okay\0"
    assert nodes["/soc/mmcnr@7e300000"]["status"] == b"disabled\0"
    assert nodes["/soc/mmcnr@7e300000"]["pinctrl-0"] == struct.pack(">I", 0x33)
    assert nodes["/soc/gpio@7e200000/sdio_pins"]["brcm,pins"] == struct.pack(">6I", *range(34, 40))
    nodes["/soc/mmc@7e300000"]["status"] = b"disabled\0"
    nodes["/soc/mmcnr@7e300000"]["status"] = b"okay\0"
    # Never reuse Linux root arguments, previous loader pointers or entropy.
    obsolete = ("bootargs", "smbios3-entrypoint", "fixup-applied", "kaslr-seed", "rng-seed",
                "linux,initrd-start", "linux,initrd-end", "freebsd,bootinfo", "freebsd,loader",
                "openbsd,uefi-system-table", "openbsd,uefi-mmap-start", "openbsd,uefi-mmap-size",
                "openbsd,uefi-mmap-desc-size", "openbsd,uefi-mmap-desc-ver")
    removed = {}
    for key in obsolete:
        if key in nodes["/chosen"]:
            removed[key] = display(nodes["/chosen"].pop(key))
    # An enabled node with no reg/interrupts cannot access hardware. The unknown
    # compatible appears in mainbus dmesg, proving the override reached kernel.
    nodes["/" + PROOF] = {"compatible": PROOF.encode() + b"\0", "status": b"okay\0"}
    candidate = serialize(tree)
    after = parse(candidate)
    assert after["nodes"] == nodes and after["reservations"] == before["reservations"]
    changes = {}
    for path in before["nodes"]:
        old, new = before["nodes"][path], after["nodes"][path]
        delta = {key: {"before": display(old[key]) if key in old else None,
                       "after": display(new[key]) if key in new else None}
                 for key in set(old) | set(new) if old.get(key) != new.get(key)}
        if delta:
            changes[path] = delta
    assert set(changes) == {"/soc/mmc@7e300000", "/soc/mmcnr@7e300000", "/chosen"}
    assert after["nodes"]["/"] == before["nodes"]["/"]
    assert after["nodes"]["/framebuffer@3ea83000"] == before["nodes"]["/framebuffer@3ea83000"]
    assert after["nodes"]["/scb/ethernet@7d580000"] == before["nodes"]["/scb/ethernet@7d580000"]
    assert after["nodes"]["/emmc2bus/mmc@7e340000"] == before["nodes"]["/emmc2bus/mmc@7e340000"]
    OUT.mkdir()
    # Stock ESP fstat's short-buffer bug is being tested separately; `d` is a
    # provisional short basename under the exclusive /DaOS-nextbsd directory.
    (OUT / "d").write_bytes(candidate)
    report = {"status": "OFFLINE_CANDIDATE_NOT_PHYSICAL_VALIDATION", "source_sha256": hashlib.sha256(original).hexdigest(),
              "candidate_sha256": hashlib.sha256(candidate).hexdigest(), "candidate_bytes": len(candidate),
              "changes": changes, "added_no_hardware_proof_node": PROOF,
              "board_compatible_model_serial_unchanged": True, "ethernet_mac_unchanged": True,
              "framebuffer_800x480_unchanged": True, "emmc2_sd_slot_unchanged": True,
              "header_reservations_preserved": before["reservations"], "runtime_props_removed": removed,
              "mmcnr_wiring": {"gpio": list(range(34, 40)), "alt_function": 7, "bus_width": 4, "non_removable": True},
              "power_gate": "UNPROVEN: stock bcm sdhc_fdt does not consume mmc-pwrseq; firmware WL_ON state must be proven",
              "expected_boot_conf_command": "machine dtb /DaOS-nextbsd/d", "machine_dtb_handler_qa_pending": True,
              "physical_device_access": False}
    (OUT / "candidate.json").write_text(json.dumps(report, indent=2) + "\n")
    assert SOURCE.read_bytes() == original
    print(json.dumps({"status": report["status"], "sha256": report["candidate_sha256"], "bytes": len(candidate),
                      "changed_nodes": list(changes), "proof": PROOF}))

if __name__ == "__main__":
    main()
