# Logical decoded-pixel capture RTL

`hf_capture_stream.sv` is an executable simulation reference for an already-decoded pixel stream. It has been compiled and run with Icarus Verilog 13.0. It supplies a bounded FIFO and logical frame validation. It supplies no SLVS-EC receiver, sensor initializer, serial lane/RAW packing, packet CRC decoder, DMA, FPGA board support, clock crossing, timing closure or bitstream.

## Adapter boundary and clock

All ports belong to one logical `clk` domain. State reset is synchronous, active-high `rst`, which must cover at least one rising clock edge. A real adapter must solve synchronization, CDC, electrical levels and actual sensor timing separately. No physical pin or source clock is assigned by this module.

The logical input adopts row-major pixels, left-to-right within each line and top-to-bottom across lines. Each accepted beat carries one unsigned sample in a 16-bit container. `PIXEL_BITS` defaults to 14; upper container bits must be zero. This is a simulation bus convention, not an assertion about sensor byte order, serial packing, CFA, optical-black pixels or metadata layout. The FIFO forwards every accepted payload unchanged, including erroneous values.

| Input | Meaning |
| --- | --- |
| `s_valid`, `s_ready` | A beat transfers only on a rising edge with both high |
| `s_pixel[15:0]` | One already-decoded unsigned pixel container |
| `s_frame_id[31:0]` | Adapter-supplied logical ID; must remain constant within a frame |
| `s_sof` | First accepted pixel of a frame |
| `s_eol` | Last accepted pixel of each configured-width line |
| `s_eof` | Last accepted pixel of the configured-height frame, also carrying EOL |
| `s_error` | Error annotation for this accepted beat; not a CRC implementation |
| `source_loss` | Synchronous adapter loss/overflow indication, independent of handshake |
| `abort_frame` | Synchronous cancellation of the current incomplete frame and any held, unaccepted input offer |

A valid offer must retain all payload fields and `s_valid` until accepted, except explicit abort or reset cancels it. `s_valid && !s_ready` is ordinary backpressure and never implies overflow. A changed or withdrawn stalled offer is a protocol error. `abort_frame` intentionally clears held-offer tracking, so retracting that cancelled offer does not poison the next frame. Abort also prevents accepting a beat on that clock.

`source_loss` reports loss known by the adapter, including loss while the FIFO is stalled. During an active frame it invalidates that frame. With no active frame (including while a previous completion is held), it is conservatively retained for the next frame. A held completion is immutable. The adapter must associate a loss with the correct logical frame; this interface cannot discover unreported loss or prove that an unthrottleable sensor honors backpressure.

`FRAME_WIDTH` and `FRAME_HEIGHT` are positive compile-time logical dimensions up to 65535 each. `FIFO_DEPTH` is positive; depth one and non-power-of-two depths are supported. No hardware memory size, sensor burst rate, safe stall duration or real-time capacity is claimed from these parameters.

## Frame validation and bounded ownership

The first accepted beat starts a frame. Without SOF it starts an invalid orphan frame. A second SOF does not silently resynchronize or replace the active frame: it sets an error on that frame. Frame ID changes, misplaced EOL/EOF, wrong line/frame length, out-of-range samples and adapter errors remain sticky through completion. Errors arising on the final accepted clock are included in that completion.

A frame closes on EOF, explicit abort, or the configured pixel limit. Reaching the pixel limit without EOF closes invalid instead of accepting unbounded extra pixels. Early EOF and abort close invalid with observed counters. A source that stops before that limit and sends neither EOF nor abort remains incomplete; no guessed clock timeout or successful completion is invented. The adapter must abort interrupted acquisition explicitly.

The FIFO supports simultaneous dequeue/enqueue while full and preserves accepted order. `fifo_level` reports its current occupancy. Output `m_valid/m_ready` uses the same holding rule; its payload is `{m_frame_id,m_pixel,m_sof,m_eol,m_eof,m_error}`. **All output pixels are provisional.** Logical errors detected later can invalidate pixels that have already left the FIFO.

After closing, input acceptance stops while queued data drain. Completion becomes valid only after that frame's FIFO data have all transferred. It remains stable until `status_valid && status_ready`. Only a completion with `status_good=true`, matching frame identity and expected counters may authorize committing a successful capture. Invalid/overflowed/partial frames must be discarded by the consumer; a plausible preview or an emitted EOF alone is insufficient. This module performs no file commit and fabricates no actual exposure/gain settings.

Completion fields are `status_frame_id`, `status_pixel_count` (accepted pixels), `status_line_count` (accepted EOL markers), `status_error_mask`, `status_checksum64` and `status_good`. Good requires a valid completion and a zero error mask. IDs are not globally deduplicated: the surrounding system must define epochs and identity reuse.

Reset flushes the FIFO, frame state and pending completion; it emits no successful result for interrupted work. The consumer must invalidate provisional work from that reset epoch. Reset during capture or a held completion cannot be interpreted as successful acquisition. Previously accepted, completed records belong to the consumer's separate ownership policy.

| Error mask bit | Hex | Meaning |
| --- | --- | --- |
| 0 | 0001 | Initial SOF missing |
| 1 | 0002 | Unexpected additional SOF |
| 2 | 0004 | Frame ID changed |
| 3 | 0008 | Incorrect line length/EOL position |
| 4 | 0010 | Incorrect frame dimensions/count or aborted length |
| 5 | 0020 | Closing pixel lacks EOL |
| 6 | 0040 | Pixel outside configured bit range |
| 7 | 0080 | Accepted `s_error` annotation |
| 8 | 0100 | Explicit adapter loss/overflow |
| 9 | 0200 | Explicit abort |
| 10 | 0400 | Pixel limit reached without EOF |
| 11 | 0800 | Stalled-input holding rule violated |

`status_checksum64` is FNV-1a-64 over every accepted pixel container's low byte followed by its high byte, using offset 14695981039346656037 and prime 1099511628211 modulo 2^64. It initializes independently for each frame and captures the final accepted pixel on the EOF/length-limit clock. An aborted result includes accepted pixels only; its cancelled, unaccepted offer is excluded. Reset discards the partial checksum along with the frame. Invalid frames may still have a checksum, which never overrides their error mask. A consumer can recompute this logical buffer fingerprint to check the handoff; it is not a sensor-link CRC or a thermal/electrical validation. No synthesis frequency or resource claim is made for the reference constant multiplications.

These are logical integrity checks. They do not validate a sensor link, lane alignment, packet encoding or real-world image quality.

## Executable verification

From the workspace:

```powershell
python -B fpga/testbench/rtl_sim.py
python -B -m unittest discover -s tests -p test_fpga_rtl.py -v
```

The runner invokes actual `iverilog -g2012` and `vvp` executables. Set `HF_IVERILOG` and `HF_VVP` to their full paths, or place them on PATH. The current Windows session also supports the verified portable runtime under `%TEMP%/hf-iverilog-runtime/ucrt64/bin`. Tool acquisition is handled by the workspace's `tools/fetch_iverilog.py`; package URLs/hashes from the official MSYS2 distribution are retained in the simulation evidence. The runner does not install tools silently. Tests explicitly skip if executables are unavailable; a skip is not RTL evidence.

`../testbench/rtl-simulation-evidence.json` records six actual passing configurations: 4×3 pixels with FIFO depths 1/3/4, 1×1 with depth 1, 1×4 with depth 3 and 4×1 with depth 3. It records compiler/runtime versions, commands and source/tool provenance. The self-checking SystemVerilog scoreboard compares every accepted/emitted payload, permits intentional reset discards, checks output/completion holding under stalls and verifies counters/error masks plus a separate byte-wise checksum scoreboard for complete and aborted frames. It exercises deterministic pseudo-random output stalls, full FIFO exchanges, malformed boundaries/lengths/IDs, range/input errors, loss at EOF and while stalled, abort plus loss, abort of a held input, completion backpressure, reset during FIFO stall and reset of a held completion. A negative configuration test executes the simulator's parameter rejection.

For other project testbenches, `../testbench/rtl_sim.py` exposes `discover_tools()` and `run_iverilog(tb_path, *, top, parameters=None, plusargs=(), output_dir=None, timeout=60)`. It returns captured commands, compiler/runtime output and versions; failures raise `RtlSimulationError` with the result. Runtime paths can be passed as `+plusargs` without a shell. This is suitable for separate logical pixel-to-file integration tests and does not itself implement DMA or a sensor driver.

Before hardware use, review the actual receiver adapter, buffering budget, reset/clock crossings, downstream ownership/DMA, FPGA constraints and prolonged board integrity tests against authorized sensor documents. Simulation success does not close those gates.
