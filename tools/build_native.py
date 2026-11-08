"""Configure, build and test the C++17 synthetic capture worker and daemon.

Uses CMake on PATH, or Visual Studio's bundled CMake on Windows. Run from any
directory: python tools/build_native.py. No package installation is performed.
"""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def find_cmake():
    executable = shutil.which("cmake")
    if executable:
        return Path(executable)
    if os.name == "nt":
        program_files = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
        installations = []
        vswhere = program_files / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if vswhere.is_file():
            found = subprocess.run(
                [str(vswhere), "-latest", "-products", "*", "-property", "installationPath"],
                check=False, capture_output=True, text=True,
            )
            if found.returncode == 0 and found.stdout.strip():
                installations.append(Path(found.stdout.strip()))
        for edition in ("BuildTools", "Community", "Professional", "Enterprise"):
            installations.append(program_files / "Microsoft Visual Studio" / "2022" / edition)
        for installation in installations:
            candidate = installation / "Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe"
            if candidate.is_file():
                return candidate
    raise RuntimeError("CMake was not found. Install CMake and a C++17 compiler, or Visual Studio C++ Build Tools.")


def run(command, cwd=None):
    print("+ " + subprocess.list2cmdline([str(part) for part in command]), flush=True)
    subprocess.run([str(part) for part in command], check=True, cwd=cwd)


def ensure_build_type(configure, build, configuration):
    """Single-configuration generators use a cache variable, not --config.

    Inspect the actual configured cache so this also works for Windows Ninja,
    Makefiles, and caller-selected generators without guessing from the OS.
    """
    cache = {}
    for line in (Path(build) / 'CMakeCache.txt').read_text(encoding='utf-8').splitlines():
        if not line or line.startswith(('#', '//')) or '=' not in line:
            continue
        key, value = line.split('=', 1)
        cache[key.split(':', 1)[0]] = value
    if not cache.get('CMAKE_CONFIGURATION_TYPES') and cache.get('CMAKE_BUILD_TYPE') != configuration:
        run([*configure, '-DCMAKE_BUILD_TYPE=' + configuration])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--config", default="Release", choices=("Debug", "Release", "RelWithDebInfo", "MinSizeRel"))
    parser.add_argument("--no-worker", action="store_true", help="build and test only the capture library")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    build = (args.build_dir or root / "build/native").resolve()
    cmake = find_cmake()
    configure = [cmake, "-S", root / "camera/native", "-B", build,
                 "-DHF_BUILD_WORKER=" + ("OFF" if args.no_worker else "ON"),
                 "-DHF_BUILD_DAEMON=" + ("OFF" if args.no_worker else "ON")]
    if os.name != "nt":
        configure.append("-DCMAKE_BUILD_TYPE=" + args.config)
    if (os.name == "nt" and not (build / "CMakeCache.txt").exists()
            and not os.environ.get("CMAKE_GENERATOR")):
        configure.extend(["-G", "Visual Studio 17 2022", "-A", "x64"])
    run(configure)
    ensure_build_type(configure, build, args.config)
    run([cmake, "--build", build, "--config", args.config, "--parallel"])
    ctest = cmake.with_name("ctest.exe" if os.name == "nt" else "ctest")
    if not ctest.is_file():
        discovered = shutil.which("ctest")
        if not discovered:
            raise RuntimeError("CTest was not found alongside CMake or on PATH")
        ctest = Path(discovered)
    run([ctest, "-C", args.config, "--output-on-failure"], cwd=build)
    if not args.no_worker:
        filename = "hf-native-worker.exe" if os.name == "nt" else "hf-native-worker"
        candidates = (build / args.config / filename, build / filename)
        worker = next((candidate for candidate in candidates if candidate.is_file()), None)
        if not worker:
            raise RuntimeError("Build succeeded but the native worker executable was not found")
        print("HF_NATIVE_WORKER=" + str(worker), flush=True)
        daemon_name = "hf-camerad.exe" if os.name == "nt" else "hf-camerad"
        daemon_candidates = (build / args.config / daemon_name, build / daemon_name)
        daemon = next((candidate for candidate in daemon_candidates if candidate.is_file()), None)
        if not daemon:
            raise RuntimeError("Build succeeded but the native daemon executable was not found")
        print("HF_NATIVE_DAEMON=" + str(daemon), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print("Native build failed: " + str(error), file=sys.stderr)
        sys.exit(1)
