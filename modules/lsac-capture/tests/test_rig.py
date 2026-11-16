import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch,Mock

from lsac_capture.rig_session import EventTail,TRACKERS,RECEIVERS,atomic_json
from lsac_capture.ownership import ReceiverLease
from lsac_capture.rig_capture import run,streams_ready
from collections import Counter


class RigTests(unittest.TestCase):
    def test_tail_retains_partial_utf8_without_duplicate_events(self):
        data=json.dumps(dict(kind="operator_marker",label="屏幕"),ensure_ascii=False).encode()+b"\n"
        tail=EventTail("fixture")
        with patch.object(Path,"exists",return_value=True),patch.object(Path,"open",side_effect=lambda *a,**k:io.BytesIO(data[:-3])):
            self.assertEqual(tail.read(),[])
        with patch.object(Path,"exists",return_value=True),patch.object(Path,"open",side_effect=lambda *a,**k:io.BytesIO(data)):
            self.assertEqual(tail.read(),[dict(kind="operator_marker",label="屏幕")])
            self.assertEqual(tail.read(),[])

    def test_status_retries_windows_reader_contention_without_rewriting_payload(self):
        with patch.object(Path,"write_text") as write,patch.object(Path,"replace",side_effect=[PermissionError(13,"sharing"),None]) as replace,patch("lsac_capture.rig_session.time.sleep"):
            atomic_json("status.json",dict(phase="active"))
        self.assertEqual(write.call_count,1)
        self.assertEqual(replace.call_count,2)

    def test_binding_is_exact_and_hmd_excluded(self):
        self.assertEqual(set(TRACKERS.values()),{"LHR-C9F2A107","LHR-7712DB4C"})
        self.assertEqual(set(RECEIVERS),{"4E482CC871-2YX","C0531DBB07-2YX"})

    def test_combined_playback_rejected_before_session_io_without_confirmation(self):
        with patch("lsac_capture.session.Session") as session:
            with self.assertRaisesRegex(ValueError,"explicit confirmation"):
                run("missing-rig","must-not-create",playback_plan={})
            session.assert_not_called()

    def test_after_A_plane_calibration_only_B_is_required_but_A_cannot_substitute(self):
        b=TRACKERS["B"];a=TRACKERS["A"]
        counts=Counter(audio_block=10);devices=Counter({(b,"raw_imu"):20,(b,"pose"):10})
        self.assertTrue(streams_ready(counts,devices,{b:10},10.1,[b]))
        self.assertFalse(streams_ready(counts,devices,{b:10},10.1,[a,b]))
        self.assertFalse(streams_ready(counts,devices,{a:10},10.1,[b]))

    def test_mutex_rejects_second_owner_and_releases_partial_acquisition(self):
        api=Mock(); api.CreateMutexW.side_effect=[101,102]; api.WaitForSingleObject.side_effect=[0,258]
        with patch("lsac_capture.ownership.C.WinDLL",return_value=api):
            lease=ReceiverLease(["A","B"])
            with self.assertRaisesRegex(RuntimeError,"another LSAC process"): lease.acquire()
        api.ReleaseMutex.assert_called_once_with(101)
        self.assertEqual(api.CloseHandle.call_count,2)
        self.assertFalse(lease.claimed)


if __name__=="__main__": unittest.main()
