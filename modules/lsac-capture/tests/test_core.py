import math
from pathlib import Path
import struct
import unittest

from lsac_capture.core import TickUnwrapper,probe_pose,rotate,axis_angle_observable,first_to_last,fit_taps,validate_config,fit_common_axis
from lsac_capture.session import unpack24
from lsac_capture.windows import pcm_format


class CoreTests(unittest.TestCase):
    def test_tick_wrap_and_reorder(self):
        u=TickUnwrapper(3)
        u.update(2**32-5)
        self.assertEqual(u.update(3)["unwrapped_u64"],2**32+3)
        self.assertTrue(u.update(2)["reordered"])
        self.assertEqual(u.update(4)["epoch"],3)

    def test_reconnection_not_wrap(self):
        u=TickUnwrapper(4)
        self.assertEqual(u.update(7)["unwrapped_u64"],7)
        self.assertEqual(u.update(8)["epoch"],4)

    def test_late_packet_across_wrap_does_not_advance_epoch(self):
        u=TickUnwrapper(); u.update(2**32-3); u.update(5)
        late=u.update(2**32-1)
        self.assertTrue(late["reordered"])
        self.assertEqual(late["unwrapped_u64"],2**32-1)
        self.assertEqual(u.update(6)["unwrapped_u64"],2**32+6)

    def test_invalid_tick(self):
        for n in (-1,2**32,1.5):
            with self.assertRaises(ValueError): TickUnwrapper().update(n)

    def test_first_last_timestamp(self):
        self.assertEqual(first_to_last(5,1,48000),5)
        self.assertAlmostEqual(first_to_last(5,480,48000),5+479/48000)
        with self.assertRaises(ValueError): first_to_last(1,0,48000)

    def test_local_axis_composition(self):
        # Tracker rolled 90deg about x; its local z axis is no longer world z.
        q=[math.sqrt(.5),math.sqrt(.5),0,0]
        p,result=probe_pose([1,2,3],q,[0,0,.1],[1,0,0,0],[0,0,1],math.pi/2)
        self.assertAlmostEqual(p[1],1.9)
        rotated=rotate(result,[1,0,0])
        self.assertAlmostEqual(rotated[2],1)

    def test_axis_observability(self):
        self.assertFalse(axis_angle_observable([0,0,4],[0,0,1]))
        self.assertTrue(axis_angle_observable([1,0,4],[0,0,1]))

    def test_common_axis_fit_only_one_angle(self):
        result=fit_common_axis([[1,0,1],[0,1,1]],[[0,1,1],[-1,0,1]],[0,0,1])
        self.assertAlmostEqual(result["angle_degrees"],90)
        self.assertFalse(result["translation_or_tilt_fitted"])
        self.assertLess(max(result["residual_radians"]),1e-7)

    def test_common_axis_rejects_endfire(self):
        with self.assertRaises(ValueError): fit_common_axis([[0,0,1]],[[0,0,1]],[0,0,1])

    def test_tap_offset_drift_outlier(self):
        x=[0,.8,1.9,3.3,120,121.1,122.7,124.8]
        y=[.031+v*1.00012 for v in x]; y[2]+=.1
        result=fit_taps(y,x)
        self.assertAlmostEqual(result["drift_ppm"],120,places=5)
        self.assertAlmostEqual(result["offset_seconds"],.031)
        self.assertNotIn(2,result["inliers"])
        self.assertIn("not_fused_pose",result["interpretation"])

    def test_tap_short_or_degenerate(self):
        with self.assertRaises(ValueError): fit_taps([1,2],[1,2])
        with self.assertRaises(ValueError): fit_taps([1]*8,[1]*8)

    def test_pcm24_extremes_no_loss_or_downmix(self):
        vals=[-8388608,-1,0,8388607]
        raw=b"".join((n & 0xffffff).to_bytes(3,"little") for n in vals)
        self.assertEqual(unpack24(raw,24).tolist(),[vals])
        self.assertEqual(unpack24(struct.pack("<4i",*(n<<8 for n in vals)),32).tolist(),[vals])

    def test_format_exact_four_channel(self):
        f=pcm_format(dict(channels=4,rate=48000,container_bits=24,valid_bits=24)).raw[:40]
        self.assertEqual(struct.unpack_from("<HHI",f),(65534,4,48000))
        self.assertEqual(struct.unpack_from("<HHHHI",f,12),(12,24,22,24,0))

    def test_config_missing_and_pairing(self):
        with self.assertRaises(ValueError): validate_config(dict(schema=4))
        import json
        path=Path(__file__).resolve().parents[1]/"probe.example.json"
        c=json.loads(path.read_text(encoding="utf-8"))
        with self.assertRaises(ValueError): validate_config(c)
        c["audio"].update(mic_position="fixed-menu-position-confirmed",channel_order_verified=True)
        c["tracking"]["serials"]=["fixture-probe","fixture-reference"]
        c["probe"].update(known_offset_tracker_m=[0,0,.1],mechanical_transform_confirmed=True,
                           support_and_usb_strain_relief_confirmed=True)
        self.assertIs(validate_config(c),c)
        c["tracking"]["pair_device"]=True
        with self.assertRaises(ValueError): validate_config(c)


if __name__=="__main__": unittest.main()
