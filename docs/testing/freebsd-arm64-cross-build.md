# DaOS ARM64 native application cross-build

This recipe runs on an actual amd64 FreeBSD15.1 build host. Its target is
**ARM64 FreeBSD**, with Clang's `aarch64-unknown-freebsd15.1` triple, the pinned
source-built ARM64 world as sysroot, and native LLVM LLD. It never imports the
earlier Linux/Debian ARM64 binaries or silently falls back to amd64 output.

```sh
python3 -B -m tools.freebsd_cross_build \
  --sysroot /var/daos-builds/daos-base-arm64-p6-001/stage \
  --base-evidence /var/daos-builds/daos-base-arm64-p6-001/build-state.json \
  --build-dir build/freebsd-arm64-native --jobs 8
```

The source-build evidence must be completed `DaOS-freebsd-base-build-v2`, name
the same private stage, target `arm64`/`aarch64`, and pin the selected
FreeBSD15.1-RELEASE-p6 commit. Actual shell, libc and kernel anchor hashes must
match that receipt; their ELF headers must describe ARM64 FreeBSD. The linker,
C++ runtime, CRT objects and required C/C++ headers must exist within the SDK.
The actual source-built profile places `libc++.so.1` and `libcxxrt.so.1` in
`/lib`; `/usr/lib/libc++.so` is a GNU `GROUP` linker script referencing those
target libraries through `/lib/libc++.so.1` and `/usr/lib/libcxxrt.so`. The
helper validates that exact bounded script, each target runtime's ARM64
FreeBSD ELF header, and contained resolution of the libcxxrt linker entry.
The explicit compiler/LLD sysroot applies to the script's absolute paths;
extra linker directives or alternate host paths are refused.
Contained SDK links are resolved and committed; links escaping into the live
host are refused. The receipt provides consistency, not a signature or an
ARM64 boot result. Only this pinned source-built profile is accepted; an
official binary sysroot would require its own separately verified profile.

Outputs must be in a new project `build/` child. Source, sysroot, tools, command
logs, notices and six resulting ELF64/FreeBSD-OSABI9/AArch64 artifacts are
committed in `freebsd-arm64-cross-build-evidence.json`. Inputs are rechecked
after compilation. Commands have finite output/time bounds and own process
groups. Existing or failed output directories are preserved, with no automatic
retry. No installed base, host services, root partitions or live SDK files are
modified.

The helper builds `hf-camerad`, `hf-native-worker` and the four native test
executables using the target SDK. **It does not execute CTest, Python/native
tests, the camera or Qt on the amd64 host.** The manifest records those target
checks as not run. Execute and validate them later in an actual ARM64 FreeBSD
guest; a source-built world or inspected ELF is not that guest validation.
Host unit tests use explicitly synthetic SDK/header fixtures only to exercise
refusal/consistency rules and cannot establish a successful target build.

The camera remains synthetic. Project rights/provenance and third-party
notices are retained; cross-compilation does not complete upstream DaOS
framework integration, physical sensor support, optics or manufacturing.
