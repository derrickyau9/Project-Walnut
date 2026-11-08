# FPGA logical capture simulation and hardware work package

The [decoded-pixel RTL](rtl/README.md) and self-checking testbenches execute with
actual Icarus Verilog. They implement a bounded FIFO, valid/ready backpressure,
frame IDs, line/pixel counters, sticky error status and a handoff checksum.
No SLVS-EC receiver, sensor initializer, DMA engine, bitstream or board support
is supplied here. No mapped FPGA hardware has been validated. Device electrical
parameters and protocol behavior must come from documents the project may use
and publish; nothing here substitutes for them.

## Implemented logical boundary and remaining receiver boundary

The logical simulation uses one clock, decoded unsigned pixels in 16-bit
containers, frame/line markers and explicit loss/abort signals. Its full
[contract](rtl/README.md) defines ordering, reset, provisional output and held
completion ownership. A real receiver adapter must separately define physical
packing, clock/reset domains, metadata and error association. Do not assume an
unthrottleable sensor can honor backpressure: size
and verify buffering for its actual burst rate and worst downstream stall.

The intended downstream record associates one frame ID with frame timestamps,
actual effective sensor settings, dimensions, format, integrity status and
counters. Actual settings must originate from an evidenced device readback or
documented application timing; do not copy requested values and call them actual.
Invalid, partial or overflowed frames must never silently become successful RAWs.

## Executable software verification

```powershell
python fpga/testbench/rtl_sim.py
python -m unittest discover -s tests -p test_fpga_rtl.py -v
python -m unittest discover -s tests -p test_fpga_dng.py -v
```

Six real RTL simulations exercise depth-one/non-power-of-two FIFOs, backpressure,
held status, reset, abort, malformed frames, protocol violations and explicit
loss. A separate test transfers deterministic Bayer pixels through the RTL,
checks every emitted beat and the FNV-1a-64 completion fingerprint, writes a
synthetic DNG only for a good completion and reads it independently. Corrupt
completion is rejected; provisional pixels alone do not authorize saving.

Select actual tools with `HF_IVERILOG` and `HF_VVP`. On Windows, a reproducible
isolated runtime can be fetched without an installer or global PATH changes:

```powershell
python tools/fetch_iverilog.py --dest "$env:TEMP\hf-iverilog-runtime" --lock fpga/testbench/toolchain-manifest.json
```

The helper needs optional `zstandard` from `requirements-test.txt`. Preserved
official MSYS2 archive URLs and SHA-256 values are in the lock file. The runner
finds the verified Temp runtime, explicit environment paths, or PATH. Missing
tools produce a test skip, which is not simulation evidence. Current actual
results are in [rtl-simulation-evidence.json](testbench/rtl-simulation-evidence.json).
There is no synthesis, CDC, timing, electrical or sensor-link acceptance implied
by these simulations. Linux development can use a locally installed Icarus.

## Verification before hardware acceptance

1. Produce source and redistribution-rights record for receiver and initializer.
2. Generate independent protocol test vectors for reset, lock loss, alignment,
   malformed packets, truncated lines, frame boundaries and RAW packing.
3. Simulate the adapter with randomized stalls, FIFO overflow and reset at each
   stage, checking every input pixel and every emitted error with a scoreboard.
4. Check all clock crossings and reset release; constrain and review timing on
   the selected FPGA, then test long-running data integrity on the board.
5. Check DMA mapping, cache synchronization and ownership with Linux drivers.

Run `python hardware/tools/bandwidth.py --help` for a preliminary payload and
single-burst estimate. It does not validate any sensor or implementation.

`testbench/frame_oracle.py` is an executable Python oracle for already-decoded
sample lines. It reports dimension, range, content and interrupted-frame errors;
it does not simulate RTL, SLVS-EC, timing, DMA or an actual sensor. Run its own
checks with `python -m unittest discover -s fpga/testbench -p "test_*.py" -v`.
