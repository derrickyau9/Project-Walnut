"""Protocol-independent frame-integrity oracle, not receiver RTL.

Feed decoded, integer-valued lines to compare expected pixel data. Caller must
implement protocol/lane decoding separately. The reference detects dimensions,
pixel range and content, but does not model clocks, DMA, or electrical signals.
"""

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class FrameReport:
    frame_id: int
    valid: bool
    lines_seen: int
    pixels_seen: int
    errors: tuple[str, ...]


class FrameOracle:
    def __init__(self, width: int, height: int, bits: int):
        for name, n in (("width", width), ("height", height), ("bits", bits)):
            if not isinstance(n, int) or isinstance(n, bool) or n <= 0:
                raise ValueError(f"{name} must be a positive integer")
        self.width, self.height, self.bits = width, height, bits
        self._active = None

    def start(self, frame_id: int, expected: Iterable[Iterable[int]] | None = None):
        """Start a frame; return the interrupted previous frame, if any."""
        if not isinstance(frame_id, int) or isinstance(frame_id, bool) or frame_id < 0:
            raise ValueError("frame_id must be a nonnegative integer")
        interrupted = self.finish("interrupted") if self._active is not None else None
        self._active = {"id": frame_id, "lines": 0, "pixels": 0, "errors": set(),
                        "expected": iter(expected) if expected is not None else None}
        return interrupted

    def line(self, pixels: Iterable[int]):
        if self._active is None:
            raise RuntimeError("line outside a frame")
        state = self._active
        values = tuple(pixels)
        state["lines"] += 1
        state["pixels"] += len(values)
        if len(values) != self.width:
            state["errors"].add("line_length")
        if state["lines"] > self.height:
            state["errors"].add("line_count")
        if any(not isinstance(v, int) or isinstance(v, bool) or not 0 <= v < (1 << self.bits) for v in values):
            state["errors"].add("pixel_range")
        if state["expected"] is not None:
            expected_line = next(state["expected"], None)
            if expected_line is None or values != tuple(expected_line):
                state["errors"].add("pixel_mismatch")

    def finish(self, error: str | None = None) -> FrameReport:
        if self._active is None:
            raise RuntimeError("no active frame")
        state, self._active = self._active, None
        if state["lines"] != self.height:
            state["errors"].add("line_count")
        if state["expected"] is not None and next(state["expected"], None) is not None:
            state["errors"].add("pixel_mismatch")
        if error is not None:
            state["errors"].add(error)
        errors = tuple(sorted(state["errors"]))
        return FrameReport(state["id"], not errors, state["lines"], state["pixels"], errors)
