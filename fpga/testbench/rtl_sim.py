"""Compile and execute the logical capture RTL with actual Icarus Verilog tools."""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
RTL = ROOT / 'fpga' / 'rtl' / 'hf_capture_stream.sv'


class ToolUnavailable(RuntimeError):
    pass


class RtlSimulationError(RuntimeError):
    def __init__(self, message, result):
        self.result = result
        super().__init__(message + '\n' + result.get('stdout', '') + result.get('stderr', ''))


def discover_tools():
    """Use explicit HF_IVERILOG/HF_VVP, PATH, then this session's Temp runtime."""
    suffix = '.exe' if os.name == 'nt' else ''
    portable = Path(tempfile.gettempdir()) / 'hf-iverilog-runtime' / 'ucrt64' / 'bin'
    found = {}
    for key, variable in [('iverilog', 'HF_IVERILOG'), ('vvp', 'HF_VVP')]:
        override = os.environ.get(variable)
        candidate = override or shutil.which(key)
        if not candidate:
            sibling = Path(found['iverilog']).parent / (key + suffix) if 'iverilog' in found else portable / (key + suffix)
            candidate = str(sibling)
        path = Path(candidate).expanduser().resolve()
        if not path.is_file():
            raise ToolUnavailable(f'{key} unavailable; set {variable} to an actual executable')
        found[key] = str(path)
    return found


def toolchain_provenance(tools):
    """Bind optional package metadata to the selected files, never a global cache.

    A supported manifest is next to a selected executable or at most two
    directories above it (for example runtime/ucrt64/bin). Both executables must
    resolve to paths declared under that manifest root and match their declared
    SHA-256 values. This verifies the selected compiler/runtime files only; it
    does not revalidate every transitive dependency in a package inventory.
    """
    if set(tools) != {'iverilog', 'vvp'}:
        raise ValueError('both selected compiler and runtime are required')
    selected = {name: Path(path).resolve() for name, path in tools.items()}
    hashes = {name: hashlib.sha256(path.read_bytes()).hexdigest()
              for name, path in selected.items()}
    result = {'tool_executable_sha256': hashes,
              'toolchain_manifest_status': 'no_matching_manifest_for_selected_executables'}
    candidates = []
    for executable in selected.values():
        for parent in list(executable.parents)[:3]:
            candidate = parent / 'toolchain-manifest.json'
            if candidate not in candidates:
                candidates.append(candidate)
    rejected = []
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            raw = candidate.read_bytes()
            manifest = json.loads(raw.decode('utf-8-sig'))
            if not isinstance(manifest, dict) or not isinstance(manifest.get('packages'), list):
                raise ValueError('package inventory missing')
            manifest_root = candidate.parent.resolve()
            verified = {}
            for name, executable in selected.items():
                if not executable.is_relative_to(manifest_root):
                    raise ValueError('selected executable is outside manifest root')
                matches = []
                for package in manifest['packages']:
                    for entry in package.get('files', []):
                        declared = entry.get('path')
                        digest = entry.get('sha256')
                        if not isinstance(declared, str) or not isinstance(digest, str):
                            continue
                        relative = Path(declared.replace('\\', '/'))
                        if relative.is_absolute() or '..' in relative.parts or ':' in declared:
                            continue
                        location = (manifest_root / relative).resolve()
                        if location == executable and location.is_relative_to(manifest_root):
                            if not re.fullmatch(r'[0-9a-fA-F]{64}', digest) or hashes[name] != digest.lower():
                                raise ValueError('selected executable SHA-256 mismatch')
                            matches.append({'path': str(executable), 'sha256': hashes[name],
                                            'manifest_relative_path': declared,
                                            'package': package.get('name')})
                if len(matches) != 1:
                    raise ValueError('selected executable must have one matching declared path')
                verified[name] = matches[0]
            result.update(toolchain_manifest=manifest,
                          toolchain_manifest_status='selected_executables_verified',
                          toolchain_manifest_verification={
                              'manifest_path': str(candidate.resolve()),
                              'manifest_sha256': hashlib.sha256(raw).hexdigest(),
                              'scope': 'selected compiler/runtime executable files only',
                              'executables': verified})
            return result
        except (OSError, ValueError, TypeError, AttributeError) as error:
            rejected.append({'manifest_path': str(candidate), 'reason': str(error)})
    if rejected:
        result['toolchain_manifest_rejected'] = rejected
    return result


def run_iverilog(tb_path, *, top, parameters=None, plusargs=(), output_dir=None, timeout=60):
    """Return actual compile/run evidence, or raise with captured diagnostics.

    Sources: this project's capture module plus tb_path. Testbench runtime input
    paths may be supplied in plusargs. No shell is used. Temporary output is
    cleaned only when output_dir was not supplied by the caller.
    """
    if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', top):
        raise ValueError('invalid top module name')
    tb_path = Path(tb_path).resolve()
    if not tb_path.is_file():
        raise FileNotFoundError(tb_path)
    parameters = dict(parameters or {})
    for name, value in parameters.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*', name) or type(value) is not int:
            raise ValueError('parameters require identifier names and integer values')
    if any(not isinstance(arg, str) or not arg.startswith('+') for arg in plusargs):
        raise ValueError('runtime arguments must be +plusargs strings')
    tools = discover_tools()
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join([str(Path(tools[k]).parent) for k in ['iverilog', 'vvp']] + [env.get('PATH', '')])
    temporary = tempfile.TemporaryDirectory(prefix='hf-rtl-') if output_dir is None else None
    directory = Path(temporary.name) if temporary else Path(output_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    try:
        artifact = directory / (top + '.vvp')
        compile_command = [tools['iverilog'], '-g2012', '-Wall', '-s', top, '-o', str(artifact)]
        compile_command += [f'-P{top}.{name}={value}' for name, value in parameters.items()]
        compile_command += [str(RTL), str(tb_path)]
        compiled = subprocess.run(compile_command, cwd=ROOT, env=env, text=True,
                                  capture_output=True, timeout=timeout)
        result = {'tool_paths': tools, 'parameters': parameters, 'compile_command': compile_command,
                  'compile_returncode': compiled.returncode, 'compile_stdout': compiled.stdout,
                  'compile_stderr': compiled.stderr, 'stdout': compiled.stdout, 'stderr': compiled.stderr}
        if compiled.returncode:
            raise RtlSimulationError('Icarus compilation failed', result)
        command = [tools['vvp'], str(artifact), *plusargs]
        simulated = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                   capture_output=True, timeout=timeout)
        result.update(run_command=command, run_returncode=simulated.returncode,
                      stdout=simulated.stdout, stderr=simulated.stderr)
        if simulated.returncode:
            raise RtlSimulationError('RTL simulation failed', result)
        versions = {}
        for name, executable in tools.items():
            version = subprocess.run([executable, '-V'], cwd=ROOT, env=env, text=True,
                                     capture_output=True, timeout=10)
            versions[name] = next((line for line in (version.stdout + version.stderr).splitlines() if line.strip()), '')
        result['tool_versions'] = versions
        result.update(toolchain_provenance(tools))
        return result
    finally:
        if temporary:
            temporary.cleanup()


def run_matrix():
    configurations = [(4, 3, 1), (4, 3, 3), (4, 3, 4), (1, 1, 1), (1, 4, 3), (4, 1, 3)]
    results = []
    for width, height, depth in configurations:
        result = run_iverilog(ROOT / 'fpga/testbench/tb_capture_stream.sv', top='tb_capture_stream',
                              parameters={'FRAME_WIDTH': width, 'FRAME_HEIGHT': height, 'FIFO_DEPTH': depth})
        if 'HF_RTL_PASS' not in result['stdout']:
            raise RtlSimulationError('testbench pass marker missing', result)
        results.append(result)
    return results


if __name__ == '__main__':
    results = run_matrix()
    for result in results:
        print(result['stdout'].splitlines()[0])
    toolchain_manifest = results[0].get('toolchain_manifest')
    for result in results:
        result.pop('toolchain_manifest', None)
    evidence = {'status': 'PASS actual Icarus RTL simulation', 'verified_at_utc': datetime.now(timezone.utc).isoformat(),
                'source_sha256': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in [RTL, ROOT / 'fpga/testbench/tb_capture_stream.sv', Path(__file__).resolve()]},
                'toolchain_manifest': toolchain_manifest, 'results': results,
                'limits': ['Logical decoded-pixel stream only', 'No sensor PHY, RAW packing, CRC decoding, DMA, CDC, board timing or bitstream validation']}
    destination = ROOT / 'fpga/testbench/rtl-simulation-evidence.json'
    destination.write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    print('Evidence:', destination)
