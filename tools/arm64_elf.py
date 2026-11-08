"""Small ELF64 inspection for evidence, not an executable loader or trust check."""
import struct
from pathlib import Path


def inspect_arm64_elf(path, *, require_static=True):
    path = Path(path)
    with path.open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:4] != b'\x7fELF' or header[4:7] != bytes((2, 1, 1)):
            raise ValueError('expected ELF64 little-endian version 1')
        fields = struct.unpack('<16sHHIQQQIHHHHHH', header)
        _, kind, machine, version, entry, phoff, _, _, ehsize, phentsize, phnum, _, _, _ = fields
        if machine != 183 or kind not in (2, 3) or version != 1 or ehsize != 64 or phentsize != 56 or not 1 <= phnum <= 128:
            raise ValueError('expected a bounded AArch64 executable ELF header')
        if phoff < 64 or phoff + phnum * phentsize > path.stat().st_size:
            raise ValueError('program headers exceed the executable file')
        stream.seek(phoff)
        programs = [struct.unpack('<IIQQQQQQ', stream.read(56)) for _ in range(phnum)]
        interpreter = any(program[0] == 3 for program in programs)
        needed = False
        for program in programs:
            if program[0] == 2:
                offset, length = program[2], program[5]
                if length % 16 or length > 65536 or offset + length > path.stat().st_size:
                    raise ValueError('invalid dynamic section extent')
                stream.seek(offset)
                for _ in range(length // 16):
                    tag, _ = struct.unpack('<qQ', stream.read(16))
                    if tag == 1:
                        needed = True
                    if tag == 0:
                        break
        if require_static and (interpreter or needed):
            raise ValueError('ARM64 static evidence requires no interpreter or needed shared libraries')
        if not any(program[0] == 1 and program[5] > 0 for program in programs):
            raise ValueError('executable has no loadable segment')
    return {'elf_class': 64, 'endianness': 'little', 'machine': machine, 'machine_name': 'EM_AARCH64',
            'type': kind, 'entry': entry, 'program_headers': phnum, 'has_interpreter': interpreter,
            'static_interpreter_absent': not interpreter, 'has_needed_libraries': needed}
