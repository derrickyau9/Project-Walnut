# R8 native DNG and retained-file integrity audit

The native writer's serialized pixels, CFA, crop/ActiveArea, black/white levels,
synthetic matrix/neutral, actual Exif exposure and capture identities passed
independent TIFF and LibRaw reads. Verified defects were in publication ownership
and the narrow retained-DNG inspector. The fixes and focused checks below are
software-only; all image data and calibration remain synthetic and uncalibrated.

| Observed defect | Resulting behavior |
| --- | --- |
| A pre-existing `photos` symlink let native capture write outside its authority root and report `complete`; its own asset lookup then refused that file. | The native authority records the startup directory identity and rejects redirects, Windows reparse points and ordinary directory replacements at admission and write/publication boundaries. Cleanup also checks directory identity. |
| Replacing a closed UUID staging file with same-size invalid bytes could be published as a completed native capture. This was reproduced with a test-only libc `fclose` gate. | The writer supplies an internal receipt derived from its actual opened file descriptor. The authority verifies file identity, expected byte count, whole serialized-file checksum and stable file statistics before sync/publication. It syncs the verified handle, rechecks the snapshot before publication and verifies the published bytes before completion. Changed, replaced and incomplete staging entries are preserved for reconciliation. |
| The inspector ignored `CFAPlaneColor` and `CFALayout`, accepting a BGGR or staggered file as ordinary RGGB. LibRaw confirmed changed patterns and rendered output. | Pattern indices are interpreted through the RGB plane mapping. Equivalent plane permutations that still describe RGGB remain supported; rectangular layout defaults remain supported. Unsupported staggered layouts are rejected. |
| The inspector ignored a `LinearizationTable` mapping stored sample 100 to linear value 200. | Lookup tables are explicitly unsupported by this narrow linear Bayer display conversion and are rejected. Independent tifffile inspection confirms the mutation fixture's stored sample and table value. |
| JSON `1e+999` parsed as infinity while the capture envelope was reported valid. | Floating-point parsing rejects overflow as well as explicit non-finite constants. Invalid metadata returns no trusted request identity. |

The DNG tag meanings and RGB/rectangular defaults were checked against Adobe's
[DNG Specification 1.7.1.0](https://helpx.adobe.com/content/dam/help/en/camera-raw/digital-negative/jcr_content/root/content/flex/items/position/position-par/download_section_733958301/download-1/DNG_Spec_1_7_1_0.pdf),
printed pages 26–27. These checks preserve both project writers' documented
formats and the minimal big-endian/default-tag fixture.

## Focused verification

All selected runs passed without skips. Original output files are copied next
to this report; `verification.json` records their original paths, sizes and SHA256.

| Run | Result | Original copied log |
| --- | --- | --- |
| Final Windows native build and CTest | 2 targets passed | `native-windows-final-build.txt` |
| Final Linux direct-compiler native build | 89 core and 90 authority assertions passed | `native-linux-receipt-build.txt` |
| Inspector and retained-source regression | 15 tests passed | `inspector-retained-tests-final.txt` |
| Final Windows boundaries, including real native/reference NTFS junctions | 4 tests passed | `native-windows-boundaries-final.txt` |
| Final Linux startup/late symlink, directory replacement and output-conflict boundaries | 4 tests passed | `native-linux-boundaries-final.txt` |
| Linux test-only dynamic interposition at staging close | 6 tests passed | `native-staging-tests.txt` |
| Native exact pixels and independent TIFF tags | 2 tests passed | `native-tiff-tests-final.txt` |
| Independent LibRaw raw pixels/rendering and meaningful CFA mutations | 2 tests passed | `native-libraw-tests-final.txt` |

The six interposition cases cover normal completion, same-size foreign file
replacement, FIFO replacement, symlink replacement, in-place byte corruption,
and a competing final path. FIFOs are rejected without blocking; foreign links,
targets and bytes survive; tampered staging never produces a final capture.
The fixture changes only its own child process through `LD_PRELOAD`, with finite
gates and cleanup. Static ARM binaries cannot use this fixture and explicitly
skip it; this audit's six cases ran against the dynamic Linux host binary.

Tests are in `test_native_staging_integrity.py`, `test_native_daemon.py`,
`test_capture_junctions.py`, `test_native_worker.py`, `test_dng_inspect.py` and
`test_retained_captures.py`. The final Linux authority is
`build/linux-native-round8-receipt/hf-camerad`. Broad Windows/Linux and fresh ARM
target verification are separate parent-owned runs; they are not claimed here.

## Limits

The checksum is an accidental-integrity check, not cryptographic authentication.
Directory/file checks detect changes at defined boundaries; path-based final
publication/cleanup are not an atomic sandbox against a hostile process with the
same filesystem permissions. Unknown publication outcomes stay queryable and are
never automatically recaptured. Retained-file validation does not authenticate
sensor provenance or color calibration. No physical board, sensor, display, lens,
DMA, storage-controller power-loss or hardware timing behavior was exercised.
R7 archives and historical evidence remain unchanged.
