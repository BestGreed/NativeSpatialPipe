import math
import io
import unittest
from unittest.mock import patch

from lsac_capture.calibration import (A_SERIAL,B_SERIAL,calibrate_heading,calibrate_planes,
    compose_pose,microphone_pose,pose_in_reference,sample_before_marker,summarize_static_pose,
    target_geometry,target_svg)
from lsac_capture.calibration import scaled_target_geometry
from lsac_capture.calibration_cli import choose_monitor
from lsac_capture.calibration_cli import read_events
from lsac_capture.calibration_cli import OneShotMarkerGate
from lsac_capture.core import rotate


def rotation(axis,degrees):
    angle=math.radians(degrees)/2
    return [math.cos(angle),*[math.sin(angle)*x for x in axis]]


def sample(serial=A_SERIAL,position=(0,0,0),q=(1,0,0,0),world="rig-geometry-1"):
    return dict(kind="static_tracker_sample",serial=serial,world_id=world,
                position_m=list(position),quaternion_wxyz=list(q))


def poses():
    return [dict(kind="pose",serial=A_SERIAL,arrival_lsl=10+i*.01,epoch=0,position_m=[0,0,0],
                 quaternion_wxyz=[(-1 if i%2 else 1),0,0,0],valid=True,optical_age_seconds=.005)
            for i in range(101)]


I=dict(position_m=[0,0,0],quaternion_wxyz=[1,0,0,0])


class CalibrationTests(unittest.TestCase):
    def test_static_quaternion_signs_do_not_cancel(self):
        result=summarize_static_pose(poses(),A_SERIAL,10,11,"world")
        self.assertEqual(result["count"],101)
        self.assertEqual(result["quaternion_wxyz"],[1,0,0,0])
        self.assertEqual(result["max_position_deviation_m"],0)

    def test_static_rejects_wrong_identity_stale_invalid_and_reconnect(self):
        for mutation in (lambda e:e.update(serial=B_SERIAL),lambda e:e.update(valid=False),
                         lambda e:e.update(optical_age_seconds=.5),lambda e:e.update(epoch=1),
                         lambda e:e.update(quaternion_wxyz=[0,0,0,0]),lambda e:e.update(position_m=[1,0,0])):
            events=poses()
            for e in events[40:61]:mutation(e)
            with self.assertRaises(ValueError):summarize_static_pose(events,A_SERIAL,10,11,"world")

    def test_static_rejects_gaps_and_boundary_without_recent_samples(self):
        events=poses()
        with self.assertRaises(ValueError):summarize_static_pose(events[:40]+events[61:],A_SERIAL,10,11,"world")
        with self.assertRaises(ValueError):summarize_static_pose(events,A_SERIAL,10,12,"world")
        with self.assertRaises(ValueError):summarize_static_pose(events,A_SERIAL,10,11,"world",max_gap_seconds=float("nan"))

    def test_relative_pose_rotates_translation_in_reference_frame(self):
        parent=dict(position_m=[2,3,4],quaternion_wxyz=rotation([0,0,1],90))
        child=dict(position_m=[1,0,0],quaternion_wxyz=[1,0,0,0])
        world=compose_pose(parent,child)
        self.assertAlmostEqual(world["position_m"][0],2)
        self.assertAlmostEqual(world["position_m"][1],4)
        result=pose_in_reference(world,parent)
        for a,b in zip(result["position_m"],child["position_m"]):self.assertAlmostEqual(a,b)

    def test_plane_angle_convention_closed_upright_flat(self):
        palm=sample()
        for normal_angle,expected_opening in ((180,0),(90,90),(0,180)):
            result=calibrate_planes(sample(position=(0,0,.2),q=rotation([1,0,0],normal_angle)),palm,"palm_rest",I,I)
            self.assertAlmostEqual(result["opening_angle_degrees"],expected_opening)
            self.assertAlmostEqual(result["sampled_point_distance_m"],.2)
            self.assertEqual(result["angle_label"],"lid_opening_relative_to_palm_rest")
        with self.assertRaises(ValueError):calibrate_planes(sample(world="other"),palm,"desk",I,I)
        with self.assertRaises(ValueError):calibrate_planes(sample(B_SERIAL),palm,"desk",I,I)

    def test_heading_only_one_axis_and_back_to_back_offset_follows_rotation(self):
        heading=calibrate_heading(sample(B_SERIAL),[0,1,0],[1,0,0,0],[1,0,0])
        self.assertAlmostEqual(heading["probe"]["common_axis_angle_radians"],math.pi/2)
        self.assertFalse(heading["fit"]["translation_or_tilt_fitted"])
        transformed=microphone_pose(dict(position_m=[1,2,3],quaternion_wxyz=rotation([0,1,0],90)),heading)
        self.assertAlmostEqual(transformed["position_m"][0],1.10673)
        self.assertAlmostEqual(transformed["position_m"][2],3)
        with self.assertRaises(ValueError):calibrate_heading(sample(B_SERIAL),[0,0,1],[1,0,0,0],[0,0,1])
        with self.assertRaises(ValueError):calibrate_heading(sample(B_SERIAL),[0,1,1],[1,0,0,0],[1,0,0])

    def test_marker_uses_prior_static_window_and_common_world(self):
        events=poses()+[dict(kind="operator_marker",label="screen_contact",arrival_lsl=11.1,world_id="same-world")]
        result=sample_before_marker(events,A_SERIAL,"screen_contact",pre_seconds=1.,end_gap_seconds=.1)
        self.assertEqual(result["count"],101)
        self.assertEqual(result["world_id"],"same-world")
        with self.assertRaises(ValueError):sample_before_marker(events,A_SERIAL,"missing")

    def test_last_matching_marker_replaces_prior_window_and_later_motion_does_not_pollute(self):
        early=poses()
        early[20]["valid"]=False
        later=[dict(e,arrival_lsl=e["arrival_lsl"]+10,position_m=[.2,0,0]) for e in poses()]
        after=[dict(e,arrival_lsl=e["arrival_lsl"]+20,position_m=[5,0,0],valid=False) for e in poses()]
        events=early+[dict(kind="operator_marker",label="contact",arrival_lsl=11.1,world_id="world")]
        events+=later+[dict(kind="operator_marker",label="contact",arrival_lsl=21.1,world_id="world")]+after
        class OnePass:
            def __iter__(self):return iter(events)
            def __len__(self):raise AssertionError("Do not materialize the entire spool")
        result=sample_before_marker(OnePass(),A_SERIAL,"contact",pre_seconds=1.,end_gap_seconds=.1)
        self.assertAlmostEqual(result["position_m"][0],.2)
        self.assertEqual(result["operator_marker"]["arrival_lsl"],21.1)
        self.assertEqual(result["count"],101)

    def test_live_event_reader_ignores_only_final_unterminated_record(self):
        data=b'{"kind":"pose","n":1}\n{"kind":"pose","n":'
        with patch("pathlib.Path.open",return_value=io.BytesIO(data)):
            self.assertEqual(list(read_events("live.jsonl")),[dict(kind="pose",n=1)])
        with patch("pathlib.Path.open",return_value=io.BytesIO(b'{"kind":"pose",bad}\n')):
            with self.assertRaises(ValueError):list(read_events("corrupt.jsonl"))

    def test_live_event_reader_stops_at_initial_file_boundary(self):
        class GrowingFile(io.BytesIO):
            def readline(self,size=-1):
                line=super().readline(size)
                current=self.tell()
                self.seek(0,2);self.write(b'{"n":2}\n');self.seek(current)
                return line
        with patch("pathlib.Path.open",return_value=GrowingFile(b'{"n":1}\n')):
            self.assertEqual(list(read_events("growing.jsonl")),[dict(n=1)])

    def test_marker_gate_locks_before_io_and_requires_explicit_rearm(self):
        gate=OneShotMarkerGate()
        self.assertTrue(gate.claim())
        requests=[]
        for _ in range(1000):
            if gate.claim():requests.append("unexpected autorepeat request")
        self.assertEqual(requests,[])
        self.assertTrue(gate.locked)
        gate.rearm()
        self.assertTrue(gate.claim())
        self.assertFalse(gate.claim())

    def test_marker_gate_does_not_unlock_after_failed_request(self):
        gate=OneShotMarkerGate()
        with self.assertRaises(OSError):
            if gate.claim():raise OSError("request outcome uncertain")
        self.assertFalse(gate.claim())
        gate.rearm()
        self.assertTrue(gate.claim())

    def test_target_uses_physical_dimensions_in_both_axes(self):
        result=target_geometry(1920,1080,320,180,100)
        self.assertEqual(result["radius_px"],[300,300])
        self.assertIn('rx="300.0"',target_svg(result))
        self.assertFalse(result["physical_size_verified"])
        with self.assertRaises(ValueError):target_geometry(1920,1080,320,180,200)

    def test_A_surface_convention_flips_USB_axis_and_contact_normal(self):
        q=[0,1,0,0]
        self.assertEqual(rotate(q,[0,1,0]),[0,-1,0])
        self.assertEqual(rotate(q,[0,0,1]),[0,0,-1])
        self.assertEqual(rotate(q,[1,0,0]),[1,0,0])

    def test_named_monitor_does_not_implicitly_select_primary(self):
        rows=[dict(device=r"\\.\DISPLAY5",index=0,primary=True),
              dict(device=r"\\.\DISPLAY6",index=1,primary=False)]
        self.assertEqual(choose_monitor(rows,"DISPLAY6")["index"],1)
        self.assertEqual(choose_monitor(rows,r"\\.\DISPLAY5")["index"],0)
        self.assertEqual(choose_monitor(rows,"1")["index"],1)
        self.assertEqual(choose_monitor(rows)["index"],0)
        with self.assertRaises(ValueError):choose_monitor(rows,"DISPLAY404")

    def test_target_pixel_fit_preserves_tracker_diameter_and_infers_display_size(self):
        nominal=target_geometry(2880,1800,300,190,99.65)
        fitted=scaled_target_geometry(nominal,.95)
        self.assertEqual(fitted["diameter_mm"],99.65)
        self.assertEqual(fitted["physical_tracker_diameter_mm"],99.65)
        self.assertAlmostEqual(fitted["radius_px"][0],nominal["radius_px"][0]*.95)
        self.assertAlmostEqual(fitted["radius_px"][1],nominal["radius_px"][1]*.95)
        self.assertAlmostEqual(fitted["effective_width_mm"],300/.95)
        self.assertAlmostEqual(fitted["effective_height_mm"],190/.95)
        self.assertTrue(fitted["user_adjusted"])
        self.assertFalse(fitted["physical_size_verified"])
        reset=scaled_target_geometry(fitted,1.)
        self.assertEqual(reset["width_mm"],300)
        self.assertFalse(reset["user_adjusted"])

    def test_target_fit_rejects_invalid_scale_and_target_outside_screen(self):
        nominal=target_geometry(2880,1800,300,190,99.65)
        for scale in (0,-1,float("nan"),float("inf"),3):
            with self.assertRaises(ValueError):scaled_target_geometry(nominal,scale)


if __name__=="__main__":unittest.main()
