"""Host-tested reference observations for FF1_Control_A0's three input nets.

No GPIO, hardware clock, interrupt, electrical conversion or output is implemented.
An electrical adapter must supply already-digital bool levels (True=high), or
None when a net cannot validly be sampled. See README.md for the release boundary.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


DEFAULT_DEBOUNCE_NS = 20_000_000


class Signal(str, Enum):
    RESET_N = "RESET_N"
    HALF_N = "HALF_N"
    FULL_N = "FULL_N"


class Transition(str, Enum):
    INITIALIZED = "initialized"
    PRESSED = "pressed"
    RELEASED = "released"
    ASSERTED = "asserted"
    DEASSERTED = "deasserted"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class DigitalSample:
    """One adapter observation; levels are electrical high/low, not actions."""

    timestamp_ns: int
    half_n: bool | None
    full_n: bool | None
    reset_n: bool | None


@dataclass(frozen=True, slots=True)
class Event:
    sequence: int
    signal: Signal
    transition: Transition
    observed_at_ns: int
    observed_since_ns: int
    level: bool | None

    @property
    def active(self) -> bool | None:
        """All imported signals are active low; None means unobserved/invalid."""
        return None if self.level is None else not self.level


@dataclass(frozen=True, slots=True)
class Snapshot:
    timestamp_ns: int | None
    half_pressed: bool | None
    full_pressed: bool | None
    reset_asserted: bool | None


def _integer_ns(value: object, label: str, *, positive: bool = False) -> None:
    # bool is an int subclass; accepting it would silently use 0/1 nanoseconds.
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer number of nanoseconds")
    if value < (1 if positive else 0):
        raise ValueError(f"{label} must be {'positive' if positive else 'nonnegative'}")


def _level(value: object, label: str) -> None:
    if value is not None and type(value) is not bool:
        raise TypeError(f"{label} must be bool (True=high), or None")


@dataclass(slots=True)
class _Button:
    stable: bool | None = None
    candidate: bool | None = None
    candidate_since_ns: int | None = None

    def observe(self, level: bool | None, now: int, debounce_ns: int):
        if level is None:
            previous = self.stable
            self.stable = self.candidate = self.candidate_since_ns = None
            if previous is not None:
                return Transition.UNAVAILABLE, now, None
            return None

        if self.candidate is not level:
            self.candidate = level
            self.candidate_since_ns = now
            return None

        assert self.candidate_since_ns is not None
        if now - self.candidate_since_ns < debounce_ns or self.stable is level:
            return None

        transition = (Transition.INITIALIZED if self.stable is None else
                      Transition.RELEASED if level else Transition.PRESSED)
        self.stable = level
        return transition, self.candidate_since_ns, level


class SupervisorInputs:
    """Deterministic sample-driven state; no capture/reset/power side effects.

    Button changes require identical observed levels separated by debounce_ns,
    with no sampled reversal or unavailable observation in between. This does
    not prove continuous physical stability between samples or detect missed
    pulses. RESET_N is an immediate observation, never a rail-good assertion.
    """

    def __init__(self, *, debounce_ns: int = DEFAULT_DEBOUNCE_NS):
        _integer_ns(debounce_ns, "debounce_ns", positive=True)
        self._debounce_ns = debounce_ns
        self._last_ns: int | None = None
        self._sequence = 0
        self._half = _Button()
        self._full = _Button()
        self._reset: bool | None = None

    @property
    def snapshot(self) -> Snapshot:
        def active(level):
            return None if level is None else not level
        return Snapshot(self._last_ns, active(self._half.stable),
                        active(self._full.stable), active(self._reset))

    def feed(self, sample: DigitalSample) -> tuple[Event, ...]:
        """Validate the entire sample before mutating; return ordered new events.

        Timestamps may repeat, but cannot regress. Repeated timestamps add no
        debounce time. Event time is the confirming sample's timestamp, while
        observed_since_ns identifies the first matching candidate observation.
        Simultaneous events always appear RESET_N, HALF_N, FULL_N, in that order.
        """
        if not isinstance(sample, DigitalSample):
            raise TypeError("sample must be a DigitalSample")
        _integer_ns(sample.timestamp_ns, "timestamp_ns")
        if self._last_ns is not None and sample.timestamp_ns < self._last_ns:
            raise ValueError("timestamp_ns regressed; restart/reconcile the adapter clock")
        for name in ("half_n", "full_n", "reset_n"):
            _level(getattr(sample, name), name)

        now = sample.timestamp_ns
        self._last_ns = now
        events: list[Event] = []

        def emit(signal, update):
            if update is not None:
                transition, since, level = update
                self._sequence += 1
                events.append(Event(self._sequence, signal, transition, now, since, level))

        if sample.reset_n is not self._reset:
            old = self._reset
            self._reset = sample.reset_n
            transition = (Transition.UNAVAILABLE if self._reset is None else
                          Transition.INITIALIZED if old is None else
                          Transition.DEASSERTED if self._reset else Transition.ASSERTED)
            emit(Signal.RESET_N, (transition, now, self._reset))

        emit(Signal.HALF_N, self._half.observe(sample.half_n, now, self._debounce_ns))
        emit(Signal.FULL_N, self._full.observe(sample.full_n, now, self._debounce_ns))
        return tuple(events)
