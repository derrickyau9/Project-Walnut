"""Behavioral tests of the portable digital-input reference; no hardware attached."""
from pathlib import Path
import json
import xml.etree.ElementTree as ET
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'firmware' / 'supervisor-mcu'))
from hf_supervisor import DigitalSample, Signal, SupervisorInputs, Transition

MS = 1_000_000


def sample(ms, half=True, full=True, reset=True):
    return DigitalSample(ms * MS, half, full, reset)


class SupervisorTests(unittest.TestCase):
    def initialized(self):
        model = SupervisorInputs()
        model.feed(sample(0))
        model.feed(sample(20))
        return model

    def test_startup_unknown_then_initialized_is_not_an_edge(self):
        model = SupervisorInputs()
        self.assertEqual((None, None, None),
                         (model.snapshot.half_pressed, model.snapshot.full_pressed,
                          model.snapshot.reset_asserted))
        self.assertEqual((), model.feed(sample(0, None, None, None)))
        reset = model.feed(sample(5))
        self.assertEqual([(Signal.RESET_N, Transition.INITIALIZED, False)],
                         [(e.signal, e.transition, e.active) for e in reset])
        self.assertEqual((), model.feed(sample(24)))
        buttons = model.feed(sample(25))
        self.assertEqual([Signal.HALF_N, Signal.FULL_N], [e.signal for e in buttons])
        self.assertTrue(all(e.transition is Transition.INITIALIZED for e in buttons))
        self.assertTrue(all(e.active is False for e in buttons))

    def test_initial_held_button_does_not_fabricate_press(self):
        model = SupervisorInputs()
        reset = model.feed(sample(0, half=False, reset=False))
        self.assertEqual(Transition.INITIALIZED, reset[0].transition)
        self.assertTrue(reset[0].active)
        events = model.feed(sample(20, half=False, reset=False))
        self.assertEqual(Transition.INITIALIZED, events[0].transition)
        self.assertTrue(events[0].active)
        self.assertTrue(model.snapshot.half_pressed)
        self.assertTrue(model.snapshot.reset_asserted)

    def test_press_bounce_restarts_interval_and_hold_does_not_repeat(self):
        model = self.initialized()
        for s in [sample(25, False), sample(27), sample(29, False), sample(48, False)]:
            self.assertEqual((), model.feed(s))
        events = model.feed(sample(49, False))
        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual((Signal.HALF_N, Transition.PRESSED, 49 * MS, 29 * MS),
                         (event.signal, event.transition, event.observed_at_ns, event.observed_since_ns))
        self.assertEqual((), model.feed(sample(10000, False)))

    def test_release_bounce_is_debounced_symmetrically(self):
        model = self.initialized()
        model.feed(sample(21, False))
        model.feed(sample(41, False))
        for s in [sample(50), sample(52, False), sample(56), sample(75)]:
            self.assertEqual((), model.feed(s))
            self.assertTrue(model.snapshot.half_pressed)
        events = model.feed(sample(76))
        self.assertEqual(Transition.RELEASED, events[0].transition)
        self.assertFalse(events[0].active)
        self.assertFalse(model.snapshot.half_pressed)

    def test_short_press_does_not_emit_transition(self):
        model = self.initialized()
        self.assertEqual((), model.feed(sample(21, False)))
        self.assertEqual((), model.feed(sample(40)))
        self.assertEqual((), model.feed(sample(60)))
        self.assertFalse(model.snapshot.half_pressed)

    def test_full_press_is_independent_of_half(self):
        model = self.initialized()
        model.feed(sample(21, full=False))
        events = model.feed(sample(41, full=False))
        self.assertEqual([(Signal.FULL_N, Transition.PRESSED)],
                         [(e.signal, e.transition) for e in events])
        self.assertFalse(model.snapshot.half_pressed)
        self.assertTrue(model.snapshot.full_pressed)

    def test_one_bouncing_channel_does_not_delay_other_channel(self):
        model = self.initialized()
        model.feed(sample(21, False, False))
        model.feed(sample(30, True, False))
        model.feed(sample(35, False, False))
        events = model.feed(sample(41, False, False))
        self.assertEqual([Signal.FULL_N], [e.signal for e in events])
        events = model.feed(sample(55, False, False))
        self.assertEqual([Signal.HALF_N], [e.signal for e in events])

    def test_simultaneous_events_have_fixed_order_and_consecutive_sequences(self):
        model = self.initialized()
        model.feed(sample(21, False, False))
        events = model.feed(sample(41, False, False, False))
        self.assertEqual([Signal.RESET_N, Signal.HALF_N, Signal.FULL_N],
                         [e.signal for e in events])
        self.assertEqual([Transition.ASSERTED, Transition.PRESSED, Transition.PRESSED],
                         [e.transition for e in events])
        self.assertEqual([4, 5, 6], [e.sequence for e in events])

    def test_reset_observation_is_immediate_and_does_not_gate_buttons(self):
        model = self.initialized()
        model.feed(sample(21, full=False))
        events = model.feed(sample(25, full=False, reset=False))
        self.assertEqual(Transition.ASSERTED, events[0].transition)
        events = model.feed(sample(41, full=False, reset=False))
        self.assertEqual([(Signal.FULL_N, Transition.PRESSED)],
                         [(e.signal, e.transition) for e in events])
        events = model.feed(sample(42, full=False, reset=True))
        self.assertEqual(Transition.DEASSERTED, events[0].transition)
        self.assertTrue(model.snapshot.full_pressed)
        self.assertEqual((), model.feed(sample(43, full=False)))

    def test_unknown_reset_is_not_deassertion_or_a_power_good_claim(self):
        model = self.initialized()
        event = model.feed(sample(21, reset=None))[0]
        self.assertEqual((Transition.UNAVAILABLE, None), (event.transition, event.active))
        self.assertIsNone(model.snapshot.reset_asserted)
        self.assertEqual((), model.feed(sample(22, reset=None)))
        event = model.feed(sample(23, reset=False))[0]
        self.assertEqual((Transition.INITIALIZED, True), (event.transition, event.active))

    def test_unknown_button_invalidates_state_without_fabricating_release(self):
        model = self.initialized()
        model.feed(sample(21, False))
        model.feed(sample(41, False))
        event = model.feed(sample(42, None))[0]
        self.assertEqual((Signal.HALF_N, Transition.UNAVAILABLE, None),
                         (event.signal, event.transition, event.active))
        self.assertIsNone(model.snapshot.half_pressed)
        self.assertEqual((), model.feed(sample(43, None)))
        self.assertEqual((), model.feed(sample(1000, False)))
        self.assertEqual((), model.feed(sample(1019, False)))
        event = model.feed(sample(1020, False))[0]
        self.assertEqual(Transition.INITIALIZED, event.transition)
        self.assertTrue(event.active)

    def test_unknown_during_candidate_discards_elapsed_time(self):
        model = self.initialized()
        model.feed(sample(21, False))
        model.feed(sample(40, None))
        model.feed(sample(100, False))
        self.assertEqual((), model.feed(sample(119, False)))
        event = model.feed(sample(120, False))[0]
        self.assertEqual((Transition.INITIALIZED, 100 * MS),
                         (event.transition, event.observed_since_ns))

    def test_equal_timestamps_do_not_add_debounce_time(self):
        model = self.initialized()
        for _ in range(10):
            self.assertEqual((), model.feed(sample(20, False)))
        self.assertEqual((), model.feed(sample(39, False)))
        self.assertEqual(Transition.PRESSED, model.feed(sample(40, False))[0].transition)

    def test_event_time_is_confirmation_not_interpolated_deadline(self):
        model = self.initialized()
        model.feed(sample(21, False))
        event = model.feed(sample(100, False))[0]
        self.assertEqual((100 * MS, 21 * MS), (event.observed_at_ns, event.observed_since_ns))

    def test_invalid_samples_are_transactional_including_candidate_history(self):
        bad_times = [-1, 1.0, True, False, None, '21', float('nan'), 20 * MS]
        bad_samples = [DigitalSample(t, True, True, True) for t in bad_times]
        for name in ['half_n', 'full_n', 'reset_n']:
            for value in [0, 1, 'high', [], object()]:
                values = dict(timestamp_ns=30 * MS, half_n=True, full_n=True, reset_n=True)
                values[name] = value
                bad_samples.append(DigitalSample(**values))
        for bad in bad_samples:
            with self.subTest(bad=bad):
                changed, control = self.initialized(), self.initialized()
                changed.feed(sample(21, False))
                control.feed(sample(21, False))
                before = changed.snapshot
                with self.assertRaises((TypeError, ValueError)):
                    changed.feed(bad)
                self.assertEqual(before, changed.snapshot)
                self.assertEqual(control.feed(sample(41, False)), changed.feed(sample(41, False)))
                self.assertEqual(control.snapshot, changed.snapshot)

    def test_custom_interval_and_arbitrarily_large_monotonic_epoch(self):
        model = SupervisorInputs(debounce_ns=3)
        start = 10 ** 40
        model.feed(DigitalSample(start, False, True, True))
        self.assertEqual((), model.feed(DigitalSample(start + 2, False, True, True)))
        events = model.feed(DigitalSample(start + 3, False, True, True))
        self.assertEqual(2, len(events))
        self.assertTrue(model.snapshot.half_pressed)

    def test_invalid_configuration_and_sample_type(self):
        for value in [True, False, None, 0, -1, 1.0, '20']:
            with self.subTest(value=value), self.assertRaises((TypeError, ValueError)):
                SupervisorInputs(debounce_ns=value)
        model = SupervisorInputs()
        with self.assertRaises(TypeError):
            model.feed({'timestamp_ns': 0})
        self.assertIsNone(model.snapshot.timestamp_ns)

    def test_contract_uses_verified_J1_nets_and_leaves_hardware_unassigned(self):
        root = Path(__file__).resolve().parents[1]
        contract = json.loads((root / 'firmware/supervisor-mcu/reference-contract.json').read_text(encoding='utf-8-sig'))
        netlist = ET.parse(root / 'hardware/imports/control-board-a0-library-v0/extracted/hasslefree-ff1-pcb/reports/netlist.xml')
        j1 = {int(node.attrib['pin']): net.attrib['name'].lstrip('/')
              for net in netlist.findall('./nets/net')
              for node in net.findall('node') if node.attrib['ref'] == 'J1'}
        self.assertEqual('V3V3', j1[contract['electrical']['accessory_supply_J1_pin']])
        self.assertTrue(all(j1[p] == 'GND' for p in contract['electrical']['ground_J1_pins']))
        self.assertEqual({Signal.HALF_N.value, Signal.FULL_N.value, Signal.RESET_N.value},
                         {signal['net'] for signal in contract['signals']})
        for signal in contract['signals']:
            self.assertEqual(j1[signal['J1_pin']], signal['net'])
            self.assertTrue(signal['active_low'])
            self.assertIsNone(signal['mcu_GPIO'])
            self.assertIsNone(signal['target_peripheral'])
        self.assertFalse(contract['mcu_present_on_imported_board'])
        self.assertIsNone(contract['selected_mcu_part'])
        self.assertFalse(contract['electrical']['direct_connection_approved'])
        self.assertFalse(contract['target_firmware_release'])
        self.assertEqual(20 * MS, contract['digital_adapter_boundary']['reference_button_debounce_ns'])

    def test_instances_and_replays_are_independent_and_deterministic(self):
        trace = [sample(0), sample(20), sample(21, full=False),
                 sample(41, full=False, reset=False), sample(42, None, False, None)]
        first, second = SupervisorInputs(), SupervisorInputs()
        first_events = tuple(e for observation in trace for e in first.feed(observation))
        self.assertIsNone(second.snapshot.timestamp_ns)
        second_events = tuple(e for observation in trace for e in second.feed(observation))
        self.assertEqual(first_events, second_events)
        self.assertEqual(first.snapshot, second.snapshot)


if __name__ == '__main__':
    unittest.main()
