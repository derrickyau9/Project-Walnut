"""Estimate raw pixel traffic and idealized single-burst backlog (SI units).

These are arithmetic estimates only: protocol overhead, memory arbitration,
alignment, multi-frame schedules and margin must be measured separately.
"""

import argparse
import json
import math


def estimate(width, height, bits, fps, buffer_frames, burst_mb_s=None, drain_mb_s=None):
    if any(not isinstance(v, int) or isinstance(v, bool) or v <= 0 for v in
           (width, height, bits, buffer_frames)):
        raise ValueError("width, height, bits and buffer_frames must be positive integers")
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps must be finite and positive")
    if (burst_mb_s is None) != (drain_mb_s is None):
        raise ValueError("burst and drain rates must be provided together")
    pixels = width * height
    packed = (pixels * bits + 7) // 8
    container = pixels * ((bits + 7) // 8)
    result = {
        "pixels": pixels,
        "packed_bytes_per_frame": packed,
        "byte_aligned_bytes_per_frame": container,
        "byte_aligned_bytes_per_second": container * fps,
        "byte_aligned_buffer_bytes": container * buffer_frames,
        "units": "decimal bytes and seconds; payload only",
    }
    if burst_mb_s is not None:
        if not all(math.isfinite(v) for v in (burst_mb_s, drain_mb_s)) or burst_mb_s <= 0 or drain_mb_s < 0:
            raise ValueError("burst rate must be positive and drain rate nonnegative, both finite")
        # Fluid model: a continuous frame arrives at fixed burst rate and the
        # output drains at constant rate from an initially empty buffer.
        result["idealized_peak_backlog_bytes"] = math.ceil(
            container * max(0.0, 1.0 - drain_mb_s / burst_mb_s))
        result["burst_model"] = "one continuous byte-aligned frame; initially empty; constant rates; excludes overhead and stalls"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--width", type=int, required=True)
    parser.add_argument("--height", type=int, required=True)
    parser.add_argument("--bits", type=int, required=True)
    parser.add_argument("--fps", type=float, required=True)
    parser.add_argument("--buffer-frames", type=int, default=8)
    parser.add_argument("--burst-mb-s", type=float)
    parser.add_argument("--drain-mb-s", type=float)
    args = parser.parse_args()
    try:
        result = estimate(args.width, args.height, args.bits, args.fps, args.buffer_frames,
                          args.burst_mb_s, args.drain_mb_s)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
