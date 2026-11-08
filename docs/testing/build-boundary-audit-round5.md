# Build and simulation boundary audit, round 5

Verified on 2026-11-05. This audit covered clean native builds, Linux packaging
and recovery tooling, the combined optional dependency requirements, CI setup,
and the FPGA simulator's tool provenance. No system service was enabled, no
artifact was published, and no physical camera or FPGA hardware was exercised.

| Verified gap | Final behavior and evidence |
| --- | --- |
| A Windows single-configuration CMake generator could ignore `--config Release` and retain Debug settings. | `build_native.py` inspects the configured cache and explicitly sets `CMAKE_BUILD_TYPE` for single-configuration generators. An actual Ninja/MSVC build with initial Debug settings retained Debug when the old guard was disabled, then selected Release with the fix. Native CTest passed both executions. Three configuration regression tests pass. |
| Linux packaging accepted changed native sources against an older tested binary and build report. | The direct Linux builder fingerprints 15 native source, header and build-recipe inputs before and after compilation/tests. Packaging requires those hashes to match the current checkout, and refuses missing legacy source hashes or changed inputs. A fresh build is required after a source change. |
| An invalid package ID was rejected only after creating a partial output tree. | Package identity is checked before directory creation. The regression confirms that no output directory is created. |
| A staged executable could change after initial artifact verification. | The copied executable is checked against the tested artifact hash before a bundle can be returned as verified. A replacement-during-copy regression is rejected. Native inputs are also rechecked after staging. |
| The RTL runner could attach an unrelated global Temp toolchain manifest to explicitly selected tools. | The FPGA owner fixed provenance attribution: both selected executable locations and SHA-256 values must match a nearby manifest. Unmatched metadata is omitted; actual selected binary hashes remain recorded. This audit independently passed all ten finalized RTL/provenance tests. |

The package tests cover normal staging, existing-output refusal, changed or
untested binaries, Windows PE rejection, stale/missing native input evidence,
invalid IDs and replacement during copying. All five pass. Package manifests
detect corruption; they are not signatures or authenticity attestations.

A new `build/linux-native-round5` directory was built from the frozen current
native sources using actual Debian WSL Linux, GCC/G++ 14.2.0, Python 3.13.5 and
glibc 2.41. `PYTHONPATH` and `PYTHONHOME` were unset and user site packages were
disabled for this run. It did not reuse the older round 4 native objects.

- Native executables passed 89 capture-core and 82 authority assertions.
- The Linux build helper passed 76 Python tests: native daemon 18, native client
  16, HTTP client 19, Linux health/package/recovery 20, and build configuration 3.
- The finalized HTTP security suite passed another 9 tests against the new Linux
  native executable. These 85 Python tests had zero failures, errors or skips.
- The packaged application passed a nonroot foreground HTTP capture, clean
  shutdown/restart with the same completed request ID, offline diagnostics with
  every camera file unchanged, real synthetic trial health confirmation, and
  persistent simulated fallback. The staged systemd user unit passed
  `systemd-analyze --user verify` with no stderr. No unit was installed or enabled.
- A separate clean extracted Windows verification reported CTest 2/2 passing and
  261 Python tests run: 259 passed and two POSIX-only checks skipped. Its actual
  optional decoder versions included NumPy 1.26.4, tifffile 2023.4.12, rawpy 0.28.0
  and LibRaw 0.22.2. This is separate evidence from the direct Linux/Ninja runs.

The preserved [round 5 build report](../../system/linux/evidence/round5-build-evidence.json)
contains the exact compile/link/test commands, native input hashes, compiler
identity and hashes of all four executables. The input map and artifact hashes
were compared to the current checkout again before preservation. The current
Linux `hf-camerad` SHA-256 is
`b3f5796c63f746b4bd78e02049196851eccac84b5584fed3dde3526ef975529f`.
The [deployment report](../../system/linux/evidence/round5-deployment-evidence.json)
records the nonroot smoke result. The
[compact audit summary](../../system/linux/evidence/round5-build-boundary-summary.json)
also preserves the CMake configuration reproduction and dependency resolver
summary. Round 4 reports remain historical and do not establish that an older
binary contains the round 5 storage ownership fixes.

The combined `requirements-test.txt` and `requirements-ui.txt` were resolved
together on CPython 3.12.7 Windows AMD64 using pip with `--dry-run`,
`--ignore-installed`, `--only-binary=:all:` and `--no-cache-dir`. Resolution
succeeded for 14 packages without installing anything. rawpy 0.28.0 declares
`numpy>=1.26.0`, satisfied by the pinned NumPy 1.26.4. PyQt5 5.15.10 resolved with
Qt5 5.15.2 and sip 12.20.0, satisfying its declared constraints. The resolver
result does not by itself prove an isolated runtime import or a complete clean
installation. Transitive versions are observed selections, not an offline wheel
lock. The complete optional setup uses Python 3.12; rawpy's metadata requires
Python 3.11 or newer. The standard-library runtime's Python 3.10 support should
not be read as a claim that every optional decoder pin works on every newer or
older interpreter.

The CI workflow selects Python 3.12, builds native targets before the full test
suite, installs Icarus on Linux, and uses preserved MSYS2 archive URLs/hashes on
Windows. The vendored SQLite 3.54.1 and JSON 3.12.0 manifest check passed all four
listed files. Remote GitHub Actions execution was not observed by this audit.
The Linux distribution/toolchain and CI runner images are not hermetic inputs;
these results establish a reproducible procedure and source/artifact traceability,
not identical binary bytes across arbitrary hosts.

The reviewed hardware boundary remains explicit. Native acquisition generates
synthetic Bayer samples, with synthetic actual-setting metadata. The RTL accepts
already decoded pixels and exercises a logical FIFO, backpressure and completion
contract with Icarus; it supplies no sensor PHY, SLVS-EC receiver, RAW packing,
DMA, CDC implementation, timing closure, synthesis result or usable bitstream.
Missing RTL tools cause an explicit skip, not simulation evidence. Recorded tool
provenance verifies the selected compiler/runtime executable files, not every
transitive DLL or package dependency.

Linux recovery is an offline host state machine. It verifies extracted bundle
integrity, persists trial budgets and reports a simulated selection. It does not
install a disk image, select a real boot partition, reboot hardware or establish
power-loss guarantees. Recognized bundle/camera ancestry is refused before state
creation; arbitrary unmarked owner storage still requires an explicitly separate
state directory. Native journal requests remain unreplayed after uncertainty.
No BSP, bootloader, board pin assignment, physical update path, service deployment
or hardware acceptance is implied by this audit.
