import copy
import unittest

import numpy as np

from lsac_capture.tap_groups import analyze_recording_groups,group_windows
from lsac_capture.tap_triplet import analyze_recording
from lsac_capture.session import unpack24


def fixture(gap=False,clip_time=None):
    rate=48000; seconds=12
    samples=np.zeros((rate*seconds,4),dtype=np.int32)
    for stamp in (1.,1.5,2.,10.,10.5,11.):samples[round(stamp*rate)]=1000000
    # An unmatched audio-only transient in the ending window is retained.
    samples[round(11.35*rate)]=1000000
    if clip_time is not None:samples[round(clip_time*rate)]=8388607
    raw=np.stack((samples&255,(samples>>8)&255,(samples>>16)&255),axis=-1).astype(np.uint8).tobytes()
    events=[dict(kind='audio_block',sample_start_i64=i*4800,frames=4800,pcm_byte_offset=i*4800*12,
                 lsl_first_frame=100+i*.1,rate=48000,channels=4,container_bits=24,epoch=0,
                 device_position_u64=i*4800,device_qpc_100ns_u64=round((100+i*.1)*1e7)) for i in range(seconds*10)]
    for index in range(seconds*250):
        if gap and 900<=index<1000:continue
        stamp=index*.004
        impulse=1000 if index in (250,375,500,2500,2625,2750) else 0
        events.append(dict(kind='raw_imu',serial='B',mask=3,epoch=0,unwrapped_u64=6000000+index*192000,
                           arrival_lsl=100+stamp,raw_accel_gyro=[impulse,0,0,0,0,0]))
    for label,stamp in (('initial_triplet_start',100.5),('initial_triplet_end',102.5),
                        ('ending_triplet_start',109.5),('ending_triplet_end',111.5)):
        events.append(dict(kind='operator_marker',label=label,arrival_lsl=stamp,world_id='world'))
    return dict(audio=dict(rate=48000),alignment_tracker_serial='B',world_id='world'),events,raw


class TapGroupTests(unittest.TestCase):
    def test_two_groups_keep_common_origins_and_fit_six_pairs(self):
        config,events,raw=fixture()
        result=analyze_recording_groups(config,events,raw)
        self.assertEqual(result['status'],'provisional_start_end_audio_imu_alignment')
        self.assertEqual(len(result['paired_taps']),6)
        self.assertEqual(result['groups']['initial']['imu_origin_tick'],6000000)
        self.assertEqual(result['groups']['ending']['imu_origin_tick'],6000000)
        self.assertEqual(result['groups']['initial']['audio_origin_lsl'],100.)
        self.assertGreater(result['groups']['ending']['paired_taps'][0]['imu_seconds'],9.)
        self.assertEqual(len(result['groups']['ending']['unmatched_audio_candidates_seconds']),1)
        self.assertAlmostEqual(result['affine_fit']['rate'],1.)
        self.assertTrue(result['held_out_group_validation']['within_operational_tolerance'])
        self.assertFalse(result['fused_pose_timing_validated'])
        self.assertFalse(result['acoustic_calibrated'])

    def test_middle_scan_gap_is_reported_but_not_differenced_into_a_tap(self):
        config,events,raw=fixture(gap=True)
        result=analyze_recording_groups(config,events,raw)
        self.assertEqual(result['status'],'provisional_start_end_audio_imu_alignment')
        self.assertEqual(result['all_record_imu_timing']['issue_count'],1)
        self.assertGreater(result['all_record_imu_timing']['maximum_positive_device_interval_seconds'],.4)
        self.assertEqual(result['groups']['initial']['selected_window_imu_timing']['issue_count'],0)
        self.assertEqual(result['groups']['ending']['selected_window_imu_timing']['issue_count'],0)

    def test_gap_inside_initial_window_fails_that_group(self):
        config,events,raw=fixture()
        events=[e for e in events if not (e['kind']=='raw_imu' and 101.20<=e['arrival_lsl']<101.25)]
        result=analyze_recording_groups(config,events,raw)
        self.assertEqual(result['groups']['initial']['status'],'invalid_triplet_window')
        self.assertNotIn('affine_fit',result)
        self.assertEqual(result['groups']['ending']['status'],'provisional_triplet_offset')

    def test_scan_clipping_does_not_certify_scan_or_fail_clean_triplets(self):
        config,events,raw=fixture(clip_time=6.)
        result=analyze_recording_groups(config,events,raw)
        self.assertEqual(result['status'],'provisional_start_end_audio_imu_alignment')
        for report in result['groups'].values():
            self.assertEqual(report['clipping_counts_per_channel'],[1,1,1,1])
            self.assertEqual(report['selected_window_clipping_counts_per_channel'],[0,0,0,0])
        self.assertFalse(result['acoustic_calibrated'])

    def test_triplet_window_clipping_requires_repeat(self):
        config,events,raw=fixture(clip_time=1.25)
        result=analyze_recording_groups(config,events,raw)
        self.assertEqual(result['groups']['initial']['status'],'clipped_requires_repeat')
        self.assertNotIn('affine_fit',result)
        self.assertEqual(result['groups']['ending']['status'],'provisional_triplet_offset')

    def test_duplicate_missing_or_out_of_order_markers_rejected(self):
        _,events,_=fixture()
        markers=[e for e in events if e['kind']=='operator_marker']
        for selected in (markers[:-1],markers+[copy.deepcopy(markers[0])]):
            with self.assertRaises(ValueError):group_windows(selected,'world')
        markers[2]['arrival_lsl']=101.
        with self.assertRaises(ValueError):group_windows(markers,'world')

    def test_operator_subwindow_retains_strict_failure_and_every_excluded_candidate(self):
        config,events,raw=fixture(clip_time=1.25)
        samples=unpack24(raw,24).copy()
        for stamp in (3.,3.5,4.):samples[round(stamp*48000)]=1000000
        raw=np.stack((samples&255,(samples>>8)&255,(samples>>16)&255),axis=-1).astype(np.uint8).tobytes()
        for event in events:
            if event['kind']=='operator_marker' and event['label']=='initial_triplet_end':event['arrival_lsl']=104.5
            if event['kind']=='raw_imu' and round((event['arrival_lsl']-100)*250) in (750,875,1000):
                event['raw_accel_gyro']=[1000,0,0,0,0,0]
        selection=dict(basis='operator reports initial group repeated after clipping',
                       operator_confirmation='I tapped two groups and selected the second.',
                       groups=dict(initial=dict(audio_relative_seconds=[2.9,4.2]),ending=dict(audio_relative_seconds=[9.8,11.2])))
        result=analyze_recording_groups(config,events,raw,selection=selection,sensitivity=True)
        self.assertEqual(result['status'],'provisional_start_end_audio_imu_alignment')
        self.assertEqual(result['original_marker_validation_status'],'triplet_group_validation_failed')
        self.assertEqual(len(result['original_marker_group_reports']['initial']['paired_taps']),6)
        self.assertEqual(len(result['excluded_candidate_review']['initial']['imu_seconds']),3)
        self.assertEqual(len(result['excluded_candidate_review']['initial']['original_pairs_excluded']),3)
        self.assertEqual(len(result['excluded_candidate_review']['ending']['audio_seconds']),1)
        self.assertEqual(result['groups']['initial']['selected_window_clipping_counts_per_channel'],[0,0,0,0])
        self.assertEqual(result['operator_selection'],selection)
        self.assertTrue(all(s['status']=='provisional_start_end_audio_imu_alignment' for s in result['audio_threshold_sensitivity'].values()))

    def test_operator_selection_requires_confirmation_and_cannot_escape_marker(self):
        config,events,raw=fixture()
        selection=dict(basis='operator choice',operator_confirmation='',
                       groups=dict(initial=dict(audio_relative_seconds=[.8,2.2]),ending=dict(audio_relative_seconds=[9.8,11.2])))
        with self.assertRaisesRegex(ValueError,'operator_confirmation'):
            analyze_recording_groups(config,events,raw,selection=selection)
        selection['operator_confirmation']='Confirmed'
        selection['groups']['initial']['audio_relative_seconds']=[0.,2.2]
        with self.assertRaisesRegex(ValueError,'within its original marker'):
            analyze_recording_groups(config,events,raw,selection=selection)


if __name__=='__main__':unittest.main()
