from collections import Counter
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np

from lsac_capture.rig_capture import streams_ready
from lsac_capture.rig_session import TRACKERS
from lsac_capture.tap_analysis import validate_audio_timing,validate_imu_timing,audio_qpc_diagnostic
from lsac_capture.tap_triplet import analyze


class TapCaptureReviewTests(unittest.TestCase):
    def audio(self):
        return [dict(sample_start_i64=i*480,frames=480,pcm_byte_offset=i*5760,
                     lsl_first_frame=10+i*.01,device_qpc_100ns_u64=100000000+i*100000,
                     device_position_u64=i*480,rate=48000,channels=4,container_bits=24,epoch=0)
                for i in range(2)]

    def test_A_only_cannot_satisfy_B_readiness_or_stale_pose(self):
        counts=Counter(audio_block=20,raw_imu=1000,pose=1000)
        devices=Counter({(TRACKERS["A"],"raw_imu"):1000,(TRACKERS["A"],"pose"):1000})
        stamps={s:10. for s in TRACKERS.values()}
        self.assertFalse(streams_ready(counts,devices,stamps,10.1))
        devices.update({(TRACKERS["B"],"raw_imu"):20,(TRACKERS["B"],"pose"):10})
        self.assertTrue(streams_ready(counts,devices,stamps,10.1))
        self.assertFalse(streams_ready(counts,devices,stamps,11.))

    def test_valid_pcm24_and_24_in_32_accounting(self):
        audio=self.audio()
        self.assertEqual(validate_audio_timing(audio,{"audio":{"rate":48000}},11520),24)
        for i,e in enumerate(audio): e.update(container_bits=32,pcm_byte_offset=i*7680)
        self.assertEqual(validate_audio_timing(audio,{"audio":{"rate":48000}},15360),32)

    def test_concatenated_audio_gap_flags_or_byte_mismatch_refused(self):
        for key,value in (("sample_start_i64",481),("flags",1),("discontinuity",True),
                          ("timestamp_error",True),("epoch",1),("lsl_first_frame",9.),
                          ("lsl_first_frame",float('nan')),("device_qpc_100ns_u64",float('nan')),
                          ("device_qpc_100ns_u64",0),("device_position_u64",481),("pcm_byte_offset",5761)):
            audio=self.audio(); audio[1][key]=value
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_audio_timing(audio,{"audio":{"rate":48000}},11520)
        with self.assertRaisesRegex(ValueError,"byte count"):
            validate_audio_timing(self.audio(),{"audio":{"rate":48000}},1)

    def test_packet_QPC_jitter_is_diagnostic_not_sample_loss(self):
        audio=self.audio()
        audio[1]['lsl_first_frame']=10.021168
        audio[1]['device_qpc_100ns_u64']=100211680
        self.assertEqual(validate_audio_timing(audio,{"audio":{"rate":48000}},11520),24)
        diagnostic=audio_qpc_diagnostic(audio)
        self.assertAlmostEqual(diagnostic['raw_qpc_step_minus_nominal_seconds']['maximum'],.011168)
        self.assertEqual(diagnostic['raw_qpc_step_minus_nominal_seconds']['over_two_samples'],1)
        self.assertFalse(diagnostic['timestamps_modified'])
        self.assertFalse(diagnostic['dejitter_applied'])
        self.assertFalse(diagnostic['clock_accuracy_validated'])
        self.assertEqual(audio[1]['lsl_first_frame'],10.021168)
        audio[1]['lsl_first_frame']=9.999
        audio[1]['device_qpc_100ns_u64']=99990000
        self.assertEqual(validate_audio_timing(audio,{"audio":{"rate":48000}},11520),24)

    def test_long_IMU_gap_cannot_be_a_jerk_tap(self):
        imu=[dict(unwrapped_u64=i*192000,epoch=0,tick_hz=48000000) for i in range(3)]
        validate_imu_timing(imu)
        imu[-1]["unwrapped_u64"]=48000000
        with self.assertRaisesRegex(ValueError,"gap"):
            validate_imu_timing(imu)

    def test_triplet_uses_B_acceleration_not_A_or_gyro_only_callbacks(self):
        samples=np.zeros((48000,4),dtype=np.int32)
        for index in (9600,24000,38400,47520):samples[index]=1000000
        raw=np.stack((samples&255,(samples>>8)&255,(samples>>16)&255),axis=-1).astype(np.uint8).tobytes()
        config={"audio":{"rate":48000},"alignment_tracker_serial":TRACKERS["B"]}
        events=[dict(kind="audio_block",sample_start_i64=i*24000,frames=24000,pcm_byte_offset=i*24000*12,
                     lsl_first_frame=10+i*.5,rate=48000,channels=4,container_bits=24,epoch=0) for i in range(2)]
        for index in range(250):
            for serial in TRACKERS.values():
                impulse=1000 if index in ((50,125,200) if serial==TRACKERS["B"] else (25,100,175)) else 0
                events.append(dict(kind="raw_imu",serial=serial,unwrapped_u64=index*192000,
                    arrival_lsl=10+index*.004,epoch=0,mask=3,raw_accel_gyro=[impulse,0,0,0,0,0]))
            events.append(dict(kind="raw_imu",serial=TRACKERS["B"],unwrapped_u64=index*192000,
                arrival_lsl=10+index*.004,epoch=0,mask=2,raw_accel_gyro=[99999,0,0,0,0,0]))
        with patch.object(Path,"read_text",return_value=json.dumps({"config":config})), \
             patch.object(Path,"read_bytes",return_value=raw), \
             patch.object(Path,"open",side_effect=lambda *a,**k:io.StringIO("\n".join(json.dumps(e) for e in events))):
            result=analyze("fixture")
        self.assertEqual(result["tracker_serial"],TRACKERS["B"])
        self.assertEqual(len(result["paired_taps"]),3)
        self.assertEqual(result["status"],"provisional_triplet_offset")
        self.assertEqual(len(result["unmatched_audio_candidates_seconds"]),1)
        self.assertLess(abs(result["fit"]["offset_seconds"]),.005)


if __name__=="__main__":unittest.main()
