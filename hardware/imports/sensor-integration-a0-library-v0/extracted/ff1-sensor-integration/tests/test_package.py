#!/usr/bin/env python3
import csv,json,re,sys,unittest
from pathlib import Path
R=Path(__file__).resolve().parents[1];sys.path.insert(0,str(R/'analysis'))
from throughput_budget import budget
class PackageTests(unittest.TestCase):
 def test_raw14_frame(self):
  b=budget('test',6064,4040,14,19.2)
  self.assertEqual(b['packed_frame_bytes'],42872480)
  self.assertEqual(b['container16_frame_bytes'],48997120)
  self.assertAlmostEqual(b['packed_active_MB_per_second'],823.151616)
 def test_all_rx_channels(self):
  with (R/'design/zcu102_hpc0_rx.csv').open() as f: rows=list(csv.DictReader(f))
  self.assertEqual([int(r['channel']) for r in rows],list(range(8)))
  for keys in [('p_pin','n_pin'),('fpga_p_pin','fpga_n_pin')]:
   ps=[r[k] for r in rows for k in keys];self.assertEqual(len(set(ps)),16)
  x=(R/'design/zcu102_hpc0_rx_pins.xdc').read_text()
  for r in rows:
   for pol in ['p','n']:
    self.assertIn('PACKAGE_PIN '+r['fpga_'+pol+'_pin']+' [get_ports {hpc0_rx_'+pol+'['+r['channel']+']}]',x)
 def test_unknowns_remain_unknown(self):
  j=json.loads((R/'design/interface_requirements.json').read_text())
  self.assertFalse(j['receiver_candidate']['fabrication_release'])
  self.assertIsNone(j['receiver_candidate']['carrier_DP_to_sensor_lane_assignment'])
  self.assertIsNone(j['receiver_candidate']['GT_reference_clock_frequency'])
if __name__=='__main__':unittest.main(verbosity=2)
