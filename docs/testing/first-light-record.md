# First-light lab record template

Do not use an unverified pinout, voltage, current limit or register sequence. Obtain controlled, authorized vendor data and test equipment before applying power. This is a record template, not evidence that a test occurred.

## Identification and readiness

- Date/operator: pending; project commit: pending; board/FPGA/sensor IDs and revisions: pending.
- Authorized references and version/date: pending; instruments and calibration: pending.
- Documented power, current limiting, clock and reset plan: pending; ESD and thermal setup: pending.

## Ordered evidence

| Step | Record before advancing | Result |
| --- | --- | --- |
| 1. Rail/clock | Rail sequence, measured values, stability, clock frequency and reset timing against specific authorized limits | Not run |
| 2. Control | Device identity, test pattern setting, mode and register initialization revision | Not run |
| 3. Receive | Lanes, rate, lock and alignment, receiver/IP revision, observed link/packet counters | Not run |
| 4. Frame | Expected versus actual lines, bytes and pixels; overflow, timeout and discarded-frame counts | Not run |
| 5. Pixels | Decoded test pattern, CFA, black and white levels, bit packing and clipping; exposure-response measurements | Not run |
| 6. DNG | Hash raw buffer and independently decode file; compare all Bayer samples and verify metadata | Not run |
| 7. Preview | Actual mode, measured latency/frame rate, dropped frame count and any switching interruption | Not run |

Each run must retain timestamped logs, measurements, generated files and hashes, plus unresolved anomalies. Stop and diagnose any unexplained integrity error before calling the frame valid. A visually plausible image alone is not evidence of bit-accurate acquisition.
