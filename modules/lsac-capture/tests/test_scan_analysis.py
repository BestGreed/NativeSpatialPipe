import copy
import math
import unittest

import numpy as np

from lsac_capture.scan_analysis import analyze_records, third_octave_rms, pose_validation, pose_imu_clock_evidence,playback_interval


def fixture(count=3):
    events=[]
    raw=bytearray()
    for block in range(count):
        n=4800
        samples=np.rint(1000000*np.sin(2*np.pi*500*np.arange(n)/48000)).astype(np.int32)
        values=np.repeat(samples[:,None],4,axis=1)
        packed=np.stack((values&255,(values>>8)&255,(values>>16)&255),axis=-1).astype(np.uint8).tobytes()
        events.append(dict(kind="audio_block",sample_start_i64=block*n,frames=n,pcm_byte_offset=len(raw),
                           lsl_first_frame=10+block*.1,rate=48000,channels=4,container_bits=24,valid_bits=24,
                           flags=0,epoch=0))
        raw.extend(packed)
    for i in range(count*25+1):
        stamp=10+i*.004
        events.append(dict(kind="pose",serial="B",position_m=[1.,0.,0.],quaternion_wxyz=[1.,0.,0.,0.],
                           arrival_lsl=stamp,pose_timecode_u64=i*192000,epoch=0,valid=True))
        events.append(dict(kind="raw_imu",serial="B",arrival_lsl=stamp+.001,unwrapped_u64=i*192000,epoch=0))
    return events, lambda offset,length: bytes(raw[offset:offset+length]), dict(world_id="same-world")


def aligned_calibration():
    return dict(world_id="same-world",screen_world_pose=dict(position_m=[0.,0.,0.],quaternion_wxyz=[1.,0.,0.,0.]),
                probe=dict(known_offset_tracker_m=[0.,0.,.10673],known_rotation_wxyz=[1.,0.,0.,0.],
                           common_axis_tracker=[0.,0.,1.],common_axis_angle_radians=0.,mechanical_transform_confirmed=True),
                pose_clock_mapping=dict(serial="B",epoch=0,validated=True,pose_latency_validated=True,
                    provider_tick_semantics_confirmed=True,provider_tick_range=[0,48000000],
                    seconds_per_tick=1/48000000,offset_lsl_seconds=10.))


class ScanAnalysisTests(unittest.TestCase):
    def test_fft_band_rejects_out_of_band_and_preserves_rms(self):
        t=np.arange(4800)/48000
        values=np.column_stack((2*np.sin(2*np.pi*500*t),3*np.sin(2*np.pi*1000*t)))
        rms=third_octave_rms(values)
        self.assertAlmostEqual(rms[0],math.sqrt(2),places=10)
        self.assertLess(rms[1],1e-12)

    def test_default_analysis_refuses_raw_arrival_and_tap_fit(self):
        events,reader,config=fixture()
        calibration=aligned_calibration()
        del calibration["pose_clock_mapping"]
        calibration["probe"]["audio_imu_tap_fit"]={"offset":1,"rate":1}
        with self.assertRaisesRegex(ValueError,"No fused-pose"):
            analyze_records(events,reader,config,calibration,serial="B")

    def test_explicit_preview_applies_back_to_back_local_offset(self):
        events,reader,config=fixture()
        for event in events:
            if event["kind"]=="pose":
                event["quaternion_wxyz"]=[math.sqrt(.5),0.,math.sqrt(.5),0.]
        result=analyze_records(events,reader,config,mode="arrival-preview",serial="B")
        self.assertEqual(result["accepted_window_count"],3)
        self.assertFalse(result["precision_certified"])
        for row in result["windows"]:
            self.assertAlmostEqual(row["acoustic_center_position_m"][0],1.10673)
            self.assertAlmostEqual(row["acoustic_center_position_m"][2],0.)
            self.assertEqual(row["association"],"arrival-preview")
            self.assertAlmostEqual(row["band_rms_adc_counts"][0],1000000/math.sqrt(2),delta=1.)

    def test_aligned_mapping_ignores_host_arrival_delay(self):
        events,reader,config=fixture()
        for event in events:
            if event["kind"]=="pose":
                event["arrival_lsl"]+=10
        result=analyze_records(events,reader,config,aligned_calibration(),serial="B")
        self.assertEqual(result["accepted_window_count"],3)
        self.assertFalse(result["db_spl"])

    def test_calibration_from_different_world_is_refused(self):
        events,reader,config=fixture()
        config["world_id"]="other-world"
        with self.assertRaisesRegex(ValueError,"world_id"):
            analyze_records(events,reader,config,aligned_calibration(),mode="arrival-preview",serial="B")

    def test_audio_gap_and_flags_never_bridge_a_window(self):
        events,reader,config=fixture()
        events[1]["flags"]=1
        result=analyze_records(events,reader,config,mode="arrival-preview",serial="B")
        self.assertEqual([row["start_sample"] for row in result["windows"]],[0,9600])
        self.assertEqual(result["audio_exclusions"]["flagged_audio_blocks"],1)

    def test_missing_pose_gap_is_not_interpolated(self):
        events,reader,config=fixture()
        events=[e for e in events if e["kind"]!="pose" or not 10.01<e["arrival_lsl"]<10.10]
        result=analyze_records(events,reader,config,mode="arrival-preview",serial="B")
        self.assertFalse(result["windows"][0]["accepted"])
        self.assertIn(result["windows"][0]["rejection"],("stale_pose","pose_gap_or_epoch_boundary"))

    def test_clipping_rejected_before_spatial_level(self):
        events,reader,config=fixture(1)
        raw=bytearray(reader(0,4800*12))
        raw[0:3]=bytes.fromhex("ffff7f")
        result=analyze_records(events,lambda o,n:bytes(raw[o:o+n]),config,mode="arrival-preview",serial="B")
        self.assertEqual(result["rejections"],{"audio_clipping":1})
        self.assertNotIn("acoustic_center_position_m",result["windows"][0])

    def test_clock_fit_requires_latency_and_measured_range(self):
        events,reader,config=fixture()
        calibration=aligned_calibration()
        calibration["pose_clock_mapping"]["pose_latency_validated"]=False
        with self.assertRaisesRegex(ValueError,"latency"):
            analyze_records(events,reader,config,calibration,serial="B")
        calibration=aligned_calibration()
        calibration["pose_clock_mapping"]["provider_tick_range"]=[0,1]
        result=analyze_records(events,reader,config,calibration,serial="B")
        self.assertEqual(result["accepted_window_count"],0)
        self.assertGreater(result["excluded_pose_events"]["pose_outside_measured_mapping_range"],0)

    def test_marker_phases_and_native_tick_evidence_are_descriptive(self):
        events,reader,config=fixture()
        events += [dict(kind="operator_marker",label="stationary",arrival_lsl=10.),
                   dict(kind="operator_marker",label="return",arrival_lsl=10.2)]
        result=pose_validation(events,"B")
        self.assertEqual([p["label"] for p in result["phases"]],["stationary","return"])
        self.assertEqual(result["phases"][1]["median_distance_from_first_phase_m"],0.)
        evidence=pose_imu_clock_evidence(events,"B")
        self.assertFalse(evidence["validated"])
        self.assertEqual(evidence["native_pose_minus_raw_imu_ticks"]["median"],0)

    def jittered_packets(self):
        events,reader,config=fixture(1)
        first=events.pop(0)
        first.update(frames=2400,device_position_u64=0,device_qpc_100ns_u64=100000000)
        second=copy.deepcopy(first)
        second.update(sample_start_i64=2400,pcm_byte_offset=2400*12,
                      device_position_u64=2400,device_qpc_100ns_u64=100611680,lsl_first_frame=10.061168)
        return [first,second,*events],reader,config

    def test_explicit_nominal_preview_joins_sample_continuous_QPC_jitter_only(self):
        events,reader,config=self.jittered_packets()
        original=copy.deepcopy(events)
        packet=analyze_records(events,reader,config,mode="arrival-preview",serial="B")
        self.assertEqual(packet["window_count"],0)
        nominal=analyze_records(events,reader,config,mode="arrival-preview",serial="B",
                                audio_time_basis="nominal-sampleclock")
        self.assertEqual(nominal["accepted_window_count"],1)
        self.assertTrue(nominal["nominal_clock_unvalidated"])
        self.assertTrue(nominal["analysis_time_axis_derived"])
        self.assertFalse(nominal["raw_timestamps_modified"])
        self.assertFalse(nominal["pyxdf_dejitter_applied"])
        self.assertFalse(nominal["precision_certified"])
        self.assertAlmostEqual(nominal["windows"][0]["audio_center_lsl"],10+4799/96000)
        self.assertAlmostEqual(nominal["audio_qpc_timing"]["raw_qpc_step_minus_nominal_seconds"]["maximum"],.011168)
        self.assertEqual(events,original)

    def test_nominal_preview_cannot_bridge_real_sample_device_or_epoch_gaps(self):
        for key,value in (("sample_start_i64",2401),("device_position_u64",2401),("epoch",1),("flags",1)):
            events,reader,config=self.jittered_packets()
            events[1][key]=value
            with self.subTest(key=key):
                result=analyze_records(events,reader,config,mode="arrival-preview",serial="B",
                                        audio_time_basis="nominal-sampleclock")
                self.assertEqual(result["window_count"],0)

    def test_nominal_clock_cannot_be_called_aligned_and_jitter_is_refused(self):
        events,reader,config=self.jittered_packets()
        with self.assertRaisesRegex(ValueError,"only in arrival-preview"):
            analyze_records(events,reader,config,aligned_calibration(),serial="B",audio_time_basis="nominal-sampleclock")
        with self.assertRaisesRegex(ValueError,"Packet QPC"):
            analyze_records(events,reader,config,aligned_calibration(),serial="B")

    def test_playback_selection_excludes_taps_from_levels_counts_and_rejections(self):
        events,reader,config=fixture(5)
        raw=bytearray(reader(0,5*4800*12))
        values=np.rint(4000000*np.sin(2*np.pi*500*np.arange(4800)/48000)).astype(np.int32)
        values=np.repeat(values[:,None],4,axis=1)
        raw[:4800*12]=np.stack((values&255,(values>>8)&255,(values>>16)&255),axis=-1).astype(np.uint8).tobytes()
        raw[4*4800*12:4*4800*12+3]=bytes.fromhex('ffff7f')
        events += [dict(kind='playback_started',arrival_lsl=10.1),
                   dict(kind='playback_finished',arrival_lsl=10.4,status='completed_not_acoustically_verified')]
        original=copy.deepcopy(events)
        result=analyze_records(events,lambda o,n:bytes(raw[o:o+n]),config,mode='arrival-preview',serial='B',
                               playback_only=True,playback_edge_seconds=0.)
        self.assertEqual(result['window_count'],3)
        self.assertEqual(result['accepted_window_count'],3)
        self.assertEqual(result['windows_outside_selection'],2)
        self.assertEqual(result['rejections'],{})
        self.assertEqual(result['relative_level_reference_scope'],'selected_playback_interval')
        self.assertTrue(all(abs(row['band_db_re_selected_max'][0])<1e-8 for row in result['windows']))
        self.assertEqual(result['pose_validation']['pose_count'],126)
        self.assertEqual(events,original)
        trimmed=analyze_records(events,lambda o,n:bytes(raw[o:o+n]),config,mode='arrival-preview',serial='B',
                                playback_only=True,playback_edge_seconds=.1)
        self.assertEqual(trimmed['window_count'],1)

    def test_multiple_playbacks_require_explicit_index_and_incomplete_playback_rejected(self):
        events=[dict(kind='playback_started',arrival_lsl=1.),
                dict(kind='playback_finished',arrival_lsl=2.,status='completed_not_acoustically_verified'),
                dict(kind='playback_started',arrival_lsl=3.),
                dict(kind='playback_finished',arrival_lsl=4.,status='completed_not_acoustically_verified')]
        with self.assertRaisesRegex(ValueError,'Multiple playback'):playback_interval(events)
        self.assertEqual(playback_interval(events,1)['raw_started_lsl'],3.)
        with self.assertRaisesRegex(ValueError,'no matching finish'):playback_interval(events[:-1],1)
        events[1]['status']='cancelled'
        with self.assertRaisesRegex(ValueError,'did not finish normally'):playback_interval(events,0)


if __name__=="__main__": unittest.main()
