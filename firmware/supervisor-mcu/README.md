# Walnut FF-1 supervisor input reference

This is portable, host-tested Python reference logic for the already-digital signals exposed by FF1_Control_A0. It is not target MCU firmware. The imported control PCB contains buttons and a supply supervisor; it contains no MCU. No GPIO assignment, peripheral, interrupt, hardware timer, output driver, host adapter or power sequencer is implemented here.

## Verified board boundary

The source is the [imported KiCad PCB and schematic](../../hardware/imports/control-board-a0-library-v0/extracted/hasslefree-ff1-pcb/hardware/FF1_Control_A0.kicad_pro), checked against its exported netlist in the [hardware review](../../hardware/interface-review.md). [reference-contract.json](reference-contract.json) records this mapping and leaves MCU/GPIO choices null.

| J1 pin | Verified accessory net | Role in this reference | MCU GPIO |
| --- | --- | --- | --- |
| 1 | V3V3 | External regulated 3.3 V supply; not a sampled digital pin | Unassigned; not a GPIO |
| 2, 6 | GND | Ground reference | Unassigned; not a GPIO |
| 3 | HALF_N | Active-low independent button observation | Unassigned |
| 4 | FULL_N | Active-low independent button observation | Unassigned |
| 5 | RESET_N | Active-low local supply-supervisor observation | Unassigned |

A future electrical adapter must provide valid, already-digitized logic levels after verifying the exact controller, bank voltage, Schmitt thresholds, absolute maximum limits, grounding, pull-ups, startup/down and back-power behavior. The board pulls its signals up to local 3.3 V; it must not be wired directly into an unverified 1.8 V input. ZCU102 FMC VADJ choices documented in the hardware review are 1.2/1.5/1.8 V. Required voltage translation and input conditioning belong to a separately reviewed adapter. Python booleans are not an electrical level shifter or a metastability filter.

The board's RC filtering does not guarantee debounce. Its test plan suggests starting at 20 ms, then measuring the real board and selected receiver. This model uses 20,000,000 ns by default for both button directions; this is a reference configuration, not a measured acceptance limit.

## Input and event contract

`hf_supervisor.py` has no runtime dependencies outside the Python standard library. Create `SupervisorInputs`, then call `feed(DigitalSample(timestamp_ns, half_n, full_n, reset_n))`. The immutable sample levels are strict `bool` values (`True` electrical high, `False` electrical low), or `None` when the adapter cannot validly observe a channel. Integers `0`/`1`, strings and other coercible values are rejected.

The caller provides a nonnegative integer monotonic timestamp in nanoseconds. Its epoch is arbitrary. Equal timestamps are accepted and add no elapsed time; regression, booleans, floats and negative values are rejected. A target timer wrap must be unwrapped by its future adapter, not fed as a backwards clock. The model reads no wall clock, sleeps nowhere and changes no files or hardware. Invalid samples are fully validated before state, candidate history or sequence numbers change.

Each button starts unknown. A known candidate must be observed unchanged for at least the configured interval before its state initializes or changes. A sampled reversal restarts that candidate interval. Startup with a held button emits `initialized` with `active=True`, not a fabricated `pressed` edge. Later stable transitions emit `pressed`/`released`; a hold does not repeat. The two buttons are independent, including simultaneous states and `FULL_N` asserted without `HALF_N`.

`None` immediately invalidates that channel's history. If its stable state was known, one `unavailable` event is emitted. Unknown time contributes no debounce interval, and a later valid channel initializes again after qualification. Loss of observation never fabricates a release. Other channels continue independently.

`RESET_N` is observed immediately, without the button debounce interval. Initial known state emits `initialized`; subsequent low/high transitions emit `asserted`/`deasserted`; loss emits `unavailable`. This reports only the local accessory supervisor output. Initial high does not prove sensor power, host health or recovery. The imported device does not guarantee a valid logic output below its POR floor, so a future adapter must mark observations invalid when its validity conditions are not met. Reset observations do not clear buttons, gate events, assert host/sensor reset or run a power sequence.

`feed` returns an immutable tuple of new `Event` records. Simultaneous events have the fixed order `RESET_N`, `HALF_N`, `FULL_N`. Each record includes a consecutive per-instance `sequence`, `signal`, `transition`, `observed_at_ns`, `observed_since_ns` and electrical `level`. Its `active` property applies active-low interpretation. Events are timestamped at the confirming sample, not interpolated or backdated to a theoretical debounce deadline. `observed_since_ns` identifies the first matching candidate sample, not a proven physical edge time. `snapshot` reports the latest accepted timestamp and each known stable logical state; an unknown state is `None`.

Sampling cannot prove what happened between samples or recover missed pulses. The real adapter must select and test a sampling cadence, maximum observation gap and synchronization strategy. This reference does not impose a fabricated polling rate, sensor timing or MCU interrupt setup. Returned events are observations only; they do not trigger capture. Application policy and durable command reconciliation remain separate from button conditioning.

## Deterministic example

With the module directory on the import path:

```python
from hf_supervisor import DigitalSample, SupervisorInputs

inputs = SupervisorInputs()
inputs.feed(DigitalSample(0, True, True, True))  # reset initializes
inputs.feed(DigitalSample(20_000_000, True, True, True))  # buttons initialize
inputs.feed(DigitalSample(21_000_000, True, False, True))
events = inputs.feed(DigitalSample(41_000_000, True, False, True))
# One FULL_N pressed event; HALF_N remains released.
```

Tests replay explicit times; they do not wait for the system clock:

```powershell
python -B -m unittest discover -s tests -p test_supervisor.py -v
```

All 19 tests pass. They cover initialization, both-edge bounce, short pulses, held and independent/simultaneous buttons, reset observation, unknown intervals, repeated/regressing/invalid timestamps, transactional rejection and deterministic replay. These are software tests without an attached board.

## Blockers before target firmware

A controller is not selected and is not present on the imported PCB. Choose and review an actual MCU/host adapter hardware revision before adding target code. Required evidence includes its exact part/package and IO bank limits, signal conditioning/translation and connector mapping, input configuration, monotonic timer resolution/wrap handling, sampling/interrupt concurrency, startup/down validity rules, cable/ESD behavior and bench waveforms. Verify the real supervisor and button behavior against the accessory test plan. Sensor reset, sensor rails and capture timing need their own authorized sensor documents and reviewed design.

This package does not close any hardware manufacturing, first-light or reliability gate.
