"""Derive Pi4 DTBs with dtc and verify that only three USB properties changed."""
from __future__ import annotations
import hashlib
from pathlib import Path
import re
import struct
import subprocess

def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def parse_fdt(data):
    if len(data) < 40 or len(data) > 16*1024*1024: raise ValueError('invalid DTB size')
    magic, total, off_struct, off_strings, off_reserve, version, compatible, _, strings_size, struct_size = struct.unpack_from('>10I', data)
    if magic != 0xd00dfeed or total != len(data) or version < 17 or compatible > 17:
        raise ValueError('unsupported FDT header')
    for offset, size in ((off_struct, struct_size), (off_strings, strings_size)):
        if offset < 40 or size > len(data)-offset: raise ValueError('FDT extent outside blob')
    strings = data[off_strings:off_strings+strings_size]
    reserve, pos = [], off_reserve
    while True:
        if pos < 40 or pos+16 > len(data): raise ValueError('FDT reserve map outside blob')
        address, size = struct.unpack_from('>QQ', data, pos); pos += 16
        if not address and not size: break
        reserve.append((address, size))
    nodes, stack, pos, limit = {}, [], off_struct, off_struct+struct_size
    while pos+4 <= limit:
        token, = struct.unpack_from('>I', data, pos); pos += 4
        if token == 1:
            end = data.index(0, pos, limit); name = data[pos:end].decode('ascii'); pos = (end+4)&~3
            if not stack and name: raise ValueError('FDT root is not empty')
            stack.append(name); path = '/'+ '/'.join(stack[1:])
            if path in nodes: raise ValueError('duplicate FDT node')
            nodes[path] = {}
        elif token == 2:
            if not stack: raise ValueError('unbalanced FDT')
            stack.pop()
        elif token == 3:
            if not stack or pos+8 > limit: raise ValueError('FDT property outside node')
            size, offset = struct.unpack_from('>II', data, pos); pos += 8
            if offset >= len(strings) or size > limit-pos: raise ValueError('FDT property extent')
            name = strings[offset:strings.index(0, offset)].decode('ascii')
            path = '/'+ '/'.join(stack[1:])
            if name in nodes[path]: raise ValueError('duplicate FDT property')
            nodes[path][name] = data[pos:pos+size]; pos = (pos+size+3)&~3
        elif token == 4: continue
        elif token == 9:
            if stack: raise ValueError('unclosed FDT node')
            return nodes, reserve
        else: raise ValueError('invalid FDT token')
    raise ValueError('FDT END missing')

def string_list(value):
    if not value or value[-1] != 0: raise ValueError('FDT string list is not terminated')
    return value[:-1].decode('ascii').split('\0')

def usb_node(nodes):
    if 'raspberrypi,4-model-b' not in string_list(nodes['/'].get('compatible', b'')):
        raise ValueError('Raspberry Pi4 Model B DTB required')
    found = []
    for path, props in nodes.items():
        if path.rsplit('/', 1)[-1] not in {'usb@7e980000', 'usb@fe980000'}: continue
        compatible = string_list(props.get('compatible', b''))
        if not {'brcm,bcm2708-usb', 'brcm,bcm2835-usb', 'snps,dwc2', 'synopsys,designware-hs-otg2'}.intersection(compatible): continue
        found.append(path)
    if len(found) != 1: raise ValueError('expected exactly one Pi4 SoC DWC USB node')
    return found[0]

def patch_dts(text, path, compatibles):
    # dtc emits a canonical DTS. Tokenize strings/comments so braces in property
    # values cannot change the hierarchy used to select the exact node.
    tokens = list(re.finditer(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"|[{};=]|[^\s{};=]+', text, re.S))
    stack, selected, previous = [], None, None
    for token in tokens:
        value = token.group()
        if value.startswith(('/*', '//')): continue
        if value == '{':
            if previous is None: raise ValueError('bad DTS node')
            name = previous.group(); stack.append((name, token.end()))
        elif value == '}':
            current = '/' + '/'.join(name for name, _ in stack[1:])
            name, start = stack.pop()
            if current == path:
                if selected is not None: raise ValueError('ambiguous DTS node')
                selected = (start, token.start())
        previous = token
    if stack or selected is None: raise ValueError('DTS target node missing')
    start, end = selected
    body = text[start:end]
    # Only statements directly under this node may be replaced.
    ranges, depth, statement = [], 0, 0
    for token in re.finditer(r'"(?:\\.|[^"\\])*"|[{};]', body, re.S):
        value = token.group()
        if value == '{': depth += 1
        elif value == '}': depth -= 1; statement = token.end()
        elif value == ';' and depth == 0:
            fragment = body[statement:token.end()]
            if re.match(r'\s*(status|dr_mode|compatible)\s*=', fragment): ranges.append((statement, token.end()))
            statement = token.end()
    for begin, finish in reversed(ranges): body = body[:begin]+body[finish:]
    properties = '\n\t\tstatus = "okay";\n\t\tdr_mode = "peripheral";\n\t\tcompatible = '+', '.join('"'+v+'"' for v in compatibles)+';\n'
    return text[:start]+properties+body+text[end:]

def derive(source, destination, dtc='/usr/bin/dtc'):
    source, destination = Path(source), Path(destination)
    if source.is_symlink() or not source.is_file() or destination.exists(): raise ValueError('ordinary source and new DTB destination required')
    original_sha = sha(source); original, reserve = parse_fdt(source.read_bytes()); path = usb_node(original)
    compatibles = string_list(original[path]['compatible'])
    if 'snps,dwc2' not in compatibles: compatibles.append('snps,dwc2')
    result = subprocess.run([str(dtc), '-I', 'dtb', '-O', 'dts', str(source)], capture_output=True, check=True)
    edited = patch_dts(result.stdout.decode(), path, compatibles)
    destination.parent.mkdir(parents=True, exist_ok=True)
    dts = destination.with_suffix('.dts'); dts.write_text(edited)
    compile_result = subprocess.run([str(dtc), '-I', 'dts', '-O', 'dtb', '-o', str(destination), str(dts)], capture_output=True, check=True)
    derived, actual_reserve = parse_fdt(destination.read_bytes())
    expected = {name: dict(props) for name, props in original.items()}
    expected[path].update(status=b'okay\0', dr_mode=b'peripheral\0', compatible=('\0'.join(compatibles)+'\0').encode())
    if derived != expected or actual_reserve != reserve or sha(source) != original_sha:
        raise ValueError('DTB roundtrip changed data outside the three intended USB properties')
    log = destination.with_suffix('.dtc.log'); log.write_bytes(result.stderr+compile_result.stderr)
    return {'source': str(source), 'source_sha256': original_sha, 'derived_sha256': sha(destination),
            'source_bytes': source.stat().st_size, 'derived_bytes': destination.stat().st_size,
            'usb_node': path, 'status': 'okay', 'dr_mode': 'peripheral', 'compatible': compatibles,
            'other_properties_and_reservations_unchanged': True, 'dtc_diagnostics_sha256': sha(log),
            'physical_runtime_fdt_verified': False}
