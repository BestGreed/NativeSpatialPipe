import ctypes as C
import json
import unittest
from unittest.mock import mock_open, patch

from lsac_capture.tracking import Tracker, Pose
from lsac_capture.receiver_session import supervise
from lsac_capture.pose_report import report


class PoseTests(unittest.TestCase):
    def setUp(self):
        self.events=[]
        self.tracker=Tracker.__new__(Tracker)
        self.tracker.identity=lambda obj: "probe"
        self.tracker.serials={"probe"}
        self.tracker.epochs={"probe":0}
        self.tracker.optical={"probe":9.9}
        self.tracker.clock=lambda:10.
        self.tracker.submit=lambda e,raw:self.events.append(e)

    def pose(self,quaternion):
        value=Pose((C.c_double*3)(1,2,3),(C.c_double*4)(*quaternion))
        self.tracker.pose(None,2**40,C.pointer(value))
        return self.events[-1]

    def test_pose_retains_provider_tick_and_quaternion_order(self):
        event=self.pose([1,0,0,0])
        self.assertTrue(event["valid"])
        self.assertEqual(event["pose_timecode_u64"],2**40)
        self.assertEqual(event["position_m"],[1,2,3])
        self.assertEqual(event["quaternion_wxyz"],[1,0,0,0])

    def test_zero_or_nonunit_quaternion_is_not_valid(self):
        for q in ([0,0,0,0],[2,0,0,0]):
            self.assertFalse(self.pose(q)["valid"])

    def test_stale_missing_or_future_optical_is_not_valid(self):
        for stamp in (9.,11.,float("-inf")):
            self.tracker.optical["probe"]=stamp
            self.assertFalse(self.pose([1,0,0,0])["valid"])

    def test_pose_session_bounds_checked_before_creating_directory(self):
        for seconds,mode in ((121,"pose"),(0,"pose"),(10,"bad"),(float("nan"),"pose"),(float("inf"),"diagnostic")):
            with self.assertRaises(ValueError): supervise("must-not-create-pose",seconds,mode)

    def test_report_preserves_invalid_pose_and_reconnect_boundary(self):
        events=[dict(kind="pose",serial="probe",epoch=0,arrival_lsl=10.,pose_timecode_u64=100,valid=True),
                dict(kind="pose",serial="probe",epoch=0,arrival_lsl=10.01,pose_timecode_u64=90,valid=False),
                dict(kind="pose",serial="probe",epoch=1,arrival_lsl=11.,pose_timecode_u64=1,valid=True),
                dict(kind="optical",decoded_event_args=[4,1,100,10,.2,0])]
        with patch("pathlib.Path.open",mock_open(read_data="\n".join(json.dumps(e) for e in events))), \
             patch("pathlib.Path.read_text",return_value="OOTX Decoder: Bad sync bit"):
            result=report("offline-fixture")
        self.assertEqual(result["counts"]["pose"],3)
        self.assertEqual(result["valid_pose_count"],2)
        self.assertEqual(result["pose_tick_backsteps"],1)
        self.assertAlmostEqual(result["max_pose_arrival_gap_seconds"],.01)
        self.assertEqual(result["gen1_optical_coverage"]["lh0_axis1"]["sensor_count"],1)


if __name__=="__main__": unittest.main()
