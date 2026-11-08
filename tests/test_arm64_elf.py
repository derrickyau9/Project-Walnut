from pathlib import Path
import struct
import tempfile
import unittest

from tools.arm64_elf import inspect_arm64_elf


def executable(*, machine=183, interpreter=False, needed=False):
    programs = [(1, 5, 0, 0x400000, 0x400000, 256, 256, 4096)]
    if interpreter: programs.append((3, 4, 240, 0, 0, 8, 8, 1))
    if needed: programs.append((2, 4, 256, 0, 0, 32, 32, 8))
    ident = b'\x7fELF' + bytes((2, 1, 1)) + bytes(9)
    header = struct.pack('<16sHHIQQQIHHHHHH', ident, 2, machine, 1, 0x400000, 64, 0, 0, 64, 56, len(programs), 0, 0, 0)
    data = header + b''.join(struct.pack('<IIQQQQQQ', *program) for program in programs)
    data = data.ljust(256, b'\0')
    if needed: data += struct.pack('<qQqQ', 1, 123, 0, 0)
    return data


class Arm64ElfTests(unittest.TestCase):
    def inspect(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'target'
            path.write_bytes(data)
            return inspect_arm64_elf(path)

    def test_genuine_machine_fields_required_not_a_reported_architecture_string(self):
        result = self.inspect(executable())
        self.assertEqual(result['machine'], 183)
        self.assertFalse(result['has_interpreter'])
        self.assertFalse(result['has_needed_libraries'])
        with self.assertRaises(ValueError): self.inspect(executable(machine=62))

    def test_dynamic_interpreter_or_needed_library_is_not_claimed_static(self):
        for data in (executable(interpreter=True), executable(needed=True)):
            with self.assertRaises(ValueError): self.inspect(data)

    def test_truncated_or_wrong_class_headers_are_rejected(self):
        with self.assertRaises(ValueError): self.inspect(b'\x7fELF')
        data = bytearray(executable()); data[4] = 1
        with self.assertRaises(ValueError): self.inspect(data)


if __name__ == '__main__':
    unittest.main()
