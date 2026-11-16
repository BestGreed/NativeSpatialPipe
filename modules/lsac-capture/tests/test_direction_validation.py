import unittest
import numpy as np
from lsac_capture.direction_validation import compare_records,axis_gain_candidate


class DirectionValidationTests(unittest.TestCase):
    def record(self,name,rotation,direction,position=(0,0,0),rms=1):
        return dict(name=name,Rmic_palm=rotation,beam_direction_palm=direction,
                    beam_direction_mic=np.asarray(rotation).T@direction,position_palm_m=position,W_rms=rms)

    def test_rotation_changes_local_bearing_but_not_fixed_source_global_bearing(self):
        first=self.record('a',np.eye(3),[1,0,0])
        second=self.record('b',np.array([[0,-1,0],[1,0,0],[0,0,1]]),[1,0,0])
        report=compare_records(first,second)
        self.assertAlmostEqual(report['microphone_rotation_degrees'],90)
        self.assertAlmostEqual(report['local_direction_difference_degrees'],90)
        self.assertAlmostEqual(report['global_direction_difference_degrees'],0)
        self.assertFalse(report['channel_semantics_automatically_verified'])

    def test_translation_level_and_actual_direction_error_remain_explicit(self):
        a=self.record('a',np.eye(3),[1,0,0])
        b=self.record('b',np.eye(3),[0,1,0],position=[.01,0,0],rms=2)
        report=compare_records(a,b)
        self.assertAlmostEqual(report['center_displacement_m'],.01)
        self.assertAlmostEqual(report['W_level_change_db'],6.020599913)
        self.assertAlmostEqual(report['global_direction_difference_degrees'],90)
        self.assertFalse(report['precision_timing_automatically_verified'])

    def test_relative_axis_candidate_uses_two_training_poses_and_keeps_45_degree_holdout(self):
        records=[];truth=np.array([.6,.7,-.3])
        for degrees in (0,90,45):
            t=np.radians(degrees);r=np.array([[np.cos(t),-np.sin(t),0],[np.sin(t),np.cos(t),0],[0,0,1]])
            records.append(dict(Rmic_palm=r,normalized_pressure_cross_local=(np.array([1,1.5,1])*(r.T@truth)).tolist()))
        report=axis_gain_candidate(records)
        self.assertAlmostEqual(report['channel_correction_xyz'][1],2/3)
        self.assertEqual(report['heldout_indices'],[2])
        self.assertLess(report['corrected_cross_direction_difference_degrees'][1],1e-5)
        self.assertFalse(report['applied_to_measurement_or_inversion'])
