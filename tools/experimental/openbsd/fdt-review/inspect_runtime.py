"""Bounded read-only FDT structure inspection, no device access."""
import hashlib
import json
from pathlib import Path
import struct

def parse(data):
    assert len(data) >= 40
    magic, total, off_struct, off_strings, off_reserve, version, last, bootcpu, strings_size, struct_size = struct.unpack_from(">10I", data)
    assert magic == 0xd00dfeed and total <= len(data) and version >= 17
    assert off_struct + struct_size <= total and off_strings + strings_size <= total
    strings = data[off_strings:off_strings + strings_size]
    nodes = {}
    stack = []
    cursor = off_struct
    while cursor < off_struct + struct_size:
        token = struct.unpack_from(">I", data, cursor)[0]
        cursor += 4
        if token == 1:
            end = data.index(b"\0", cursor)
            name = data[cursor:end].decode()
            cursor = (end + 4) & ~3
            stack.append(name)
            path = "/" + "/".join(stack[1:])
            assert path not in nodes
            nodes[path] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            length, nameoff = struct.unpack_from(">II", data, cursor)
            cursor += 8
            assert 0 <= nameoff < strings_size and cursor + length <= total
            name = strings[nameoff:strings.index(b"\0", nameoff)].decode()
            path = "/" + "/".join(stack[1:])
            assert name not in nodes[path]
            nodes[path][name] = data[cursor:cursor + length]
            cursor = (cursor + length + 3) & ~3
        elif token == 4:
            pass
        elif token == 9:
            break
        else:
            raise ValueError((token, cursor))
    reservations = []
    cursor = off_reserve
    while True:
        address, size = struct.unpack_from(">QQ", data, cursor)
        cursor += 16
        if address == 0 and size == 0:
            break
        reservations.append([address, size])
        assert len(reservations) < 64
    return {"header": {"total": total, "reserve_offset": off_reserve, "structure_offset": off_struct,
                       "strings_offset": off_strings, "version": version},
            "reservations": reservations, "nodes": nodes}

def display(value):
    if not value:
        return {"boolean": True}
    if value.endswith(b"\0") and all(v in (0, 9, 10, 13) or 32 <= v < 127 for v in value):
        return {"strings": value[:-1].decode().split("\0")}
    return {"hex": value.hex(), "cells": list(struct.unpack(">" + "I" * (len(value) // 4), value)) if len(value) % 4 == 0 else None}

def main():
    base = Path(__file__).resolve().parent
    source = base.parents[1] / "DaOS_USB_Recovery_001/wifi005-ram-analysis/physical-stable-runtime.dtb"
    data = source.read_bytes()
    assert len(data) == 61440
    assert hashlib.sha256(data).hexdigest() == "75fb0ed84d305b1d14a710b1554b26df09ce0ad740ecbb96f7d587fc4bcbe3c8"
    tree = parse(data)
    result = {"source": str(source), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
              "header": tree["header"], "reservations": tree["reservations"],
              "nodes": {name: {key: display(value) for key, value in props.items()} for name, props in tree["nodes"].items()}}
    (base / "stable-structure.json").write_text(json.dumps(result, indent=2) + "\n")
    relevant = [name for name in result["nodes"] if name in ("/", "/chosen", "/memory@0", "/aliases") or
                "mmc" in name or "wifi" in name or "framebuffer" in name or "pwrseq" in name or
                "sdio" in name or "reserved-memory" in name or "expander" in name]
    print(json.dumps({"header": result["header"], "reservations": result["reservations"],
                      "nodes": {name: result["nodes"][name] for name in relevant}}, indent=2))

if __name__ == "__main__":
    main()
