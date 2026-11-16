import math
import unittest

import numpy as np

from lsac_capture.field_model import (rotate_foa,rotation_matrix,covariance,analytic_band,
    acoustic_center_screen,microphone_rotation_screen,spatial_bins,interpolation_bins,local_gaussian,PoseInterpolator,motion_covariance,adaptive_window_covariances,accumulate_spatial)


SCREEN=dict(position_m=[0,0,0],quaternion_wxyz=[1,0,0,0])
PROBE=dict(known_offset_tracker_m=[0,0,.10673],known_rotation_wxyz=[1,0,0,0],
           common_axis_tracker=[0,0,1],common_axis_angle_radians=0.)


class FieldModelTests(unittest.TestCase):
    def test_spatial_sample_conservation_revisits_covariance_and_psd(self):
        events=[dict(kind='pose',serial='B',arrival_lsl=i*.004,position_m=[.01+i*.002,0,0],
                     quaternion_wxyz=[1,0,0,0],epoch=0,valid=True) for i in range(27)]
        poses=PoseInterpolator(events,'B'); rng=np.random.default_rng(12)
        signal=rng.normal(size=(4800,4))+1j*rng.normal(size=(4800,4));cells={}
        accumulate_spatial(signal,0,4800,0,poses,SCREEN,PROBE,cells,7,100,True)
        self.assertEqual(sum(c['frames'] for c in cells.values()),4800)
        samples=[i for c in cells.values() for segment in c['segments'] for i in range(segment['start_sample'],segment['start_sample']+segment['frames'])]
        self.assertEqual(sorted(samples),list(range(100,4900)))
        # Spatial accumulation may mix disjoint passes but must retain both ranges.
        accumulate_spatial(signal,0,4800,0,poses,SCREEN,PROBE,cells,8,10000,True)
        self.assertEqual(sum(c['frames'] for c in cells.values()),9600)
        for c in cells.values():
            self.assertEqual(c['source_indices'],{7,8})
            self.assertGreaterEqual(len(c['segments']),2)
            matrix=c['matrix']/c['weight']
            np.testing.assert_allclose(matrix,matrix.conj().T,atol=1e-12)
            self.assertGreaterEqual(np.linalg.eigvalsh(matrix).min(),-1e-12)
        self.assertGreater(len(cells),1)

    def test_spatial_cell_transform_and_stationary_W_energy(self):
        events=[dict(kind='pose',serial='B',arrival_lsl=i*.004,position_m=[.005,0,0],
                     quaternion_wxyz=[1,0,0,0],epoch=0,valid=True) for i in range(27)]
        signal=np.tile(np.array([2+3j,1,4,5]),(4800,1));cells={}
        transform=dict(rotation=[[0,-1,0],[1,0,0],[0,0,1]],translation=[.04,0,0])
        accumulate_spatial(signal,0,4800,0,PoseInterpolator(events,'B'),SCREEN,PROBE,cells,0,0,True,cell_transform=transform)
        self.assertEqual(list(cells),[(2,0,5)])
        cell=next(iter(cells.values()))
        np.testing.assert_allclose(cell['position']/cell['weight'],[.005,0,.10673])
        np.testing.assert_allclose(cell['matrix']/cell['weight'],covariance(rotate_foa(signal,np.eye(3))),rtol=1e-12)
        self.assertEqual(cell['segments'],[dict(start_sample=0,frames=4800)])
        self.assertAlmostEqual(cell['weight'],4800)

    def test_spatial_invalid_parent_does_not_partially_mutate_cells(self):
        events=[dict(kind='pose',serial='B',arrival_lsl=i*.004,position_m=[0,0,0],
                     quaternion_wxyz=[1,0,0,0],epoch=0,valid=True) for i in range(27)]
        events[15]['valid']=False;cells={}
        with self.assertRaisesRegex(ValueError,'invalid_pose'):
            accumulate_spatial(np.ones((4800,4)),0,4800,0,PoseInterpolator(events,'B'),SCREEN,PROBE,cells,0,0,True)
        self.assertEqual(cells,{})

    def test_ambix_mapping_rotates_XYZ_but_never_W(self):
        # Original order W,Y,Z,X. A +90 degree Z rotation sends X->Y,Y->-X.
        source=np.array([[1+2j,3+4j,4+5j,2+3j]])
        q=[math.sqrt(.5),0,0,math.sqrt(.5)]
        result=rotate_foa(source,rotation_matrix(q))
        np.testing.assert_allclose(result,[[1+2j,-3-4j,2+3j,4+5j]],atol=1e-12)

    def test_covariance_is_Hermitian_PSD_and_cross_sign_is_C_i0(self):
        rng=np.random.default_rng(23)
        signal=rng.normal(size=(200,4))+1j*rng.normal(size=(200,4))
        c=covariance(signal)
        np.testing.assert_allclose(c,c.conj().T,atol=1e-12)
        self.assertGreaterEqual(np.linalg.eigvalsh(c).min(),-1e-12)
        c=covariance(np.array([[1,1j,0,0]],complex))
        self.assertAlmostEqual(c[1,0].imag,.5)
        self.assertAlmostEqual(c[0,1].imag,-.5)

    def test_analytic_filter_energy_matches_real_band_RMS_squared(self):
        t=np.arange(4800)/48000
        source=np.zeros((4800,4));source[:,0]=2*np.cos(2*np.pi*500*t)+3*np.cos(2*np.pi*1000*t)
        z=analytic_band(source)
        self.assertAlmostEqual(covariance(z)[0,0].real,2.,places=6)

    def test_back_to_back_offset_rotates_with_tracker_not_world_Z(self):
        q=[math.sqrt(.5),0,math.sqrt(.5),0]
        np.testing.assert_allclose(acoustic_center_screen([1,0,0],q,SCREEN),[1.10673,0,0],atol=1e-12)
        probe=dict(PROBE,common_axis_angle_radians=math.pi/2)
        r=rotation_matrix(microphone_rotation_screen(q,SCREEN,probe))
        np.testing.assert_allclose(r@[1,0,0],[0,1,0],atol=1e-12)

    def test_gaussian_averages_linear_energy_and_preserves_PSD(self):
        positions=[[0,0,0],[.01,0,0],[.02,0,0]]
        c=np.array([np.eye(4)*v for v in (1,100,1)],complex)
        bins=spatial_bins(positions,c)
        result=local_gaussian([.01,0,0],bins)
        weights=np.exp(-.5*(np.array([.01,0,.01])/.02)**2);weights/=weights.sum()
        self.assertAlmostEqual(result['covariance'][0,0].real,float(weights@np.array([1,100,1])))
        db_interpolation=10**(float(weights@np.array([0,20,0]))/10)
        self.assertGreater(result['covariance'][0,0].real,db_interpolation*5)
        self.assertGreaterEqual(np.linalg.eigvalsh(result['covariance']).min(),0)
        self.assertEqual(result['geometry_rank'],1)

    def test_outside_support_is_unknown_and_dwell_does_not_add_spatial_bins(self):
        positions=np.array([[0,0,0],[.01,0,0],[.02,0,0]])
        matrices=np.array([np.eye(4)*v for v in (1,2,3)],complex)
        bins=spatial_bins(positions,matrices)
        unknown=local_gaussian([1,0,0],bins)
        self.assertFalse(unknown['known']);self.assertNotIn('covariance',unknown)
        dense=spatial_bins(np.vstack((np.repeat(positions[:1],100,axis=0),positions[1:])),
                           np.concatenate((np.repeat(matrices[:1],100,axis=0),matrices[1:])))
        self.assertEqual(len(dense['positions']),3)
        original=local_gaussian([.01,0,0],bins)
        repeated=local_gaussian([.01,0,0],dense)
        self.assertEqual(repeated['spatial_bin_count'],3)
        np.testing.assert_allclose(original['covariance'],repeated['covariance'])

    def test_short_invalid_callback_between_4ms_centres_cannot_be_skipped(self):
        events=[dict(kind='pose',serial='B',arrival_lsl=i*.004,position_m=[0,0,0],
                     quaternion_wxyz=[1,0,0,0],epoch=0,valid=True) for i in range(27)]
        events += [dict(events[0],arrival_lsl=.002),dict(events[0],arrival_lsl=.003,valid=False)]
        poses=PoseInterpolator(events,'B')
        with self.assertRaisesRegex(ValueError,'invalid_pose'):
            motion_covariance(np.ones((4800,4),complex),0,4800,0.,poses,SCREEN,PROBE,True)
        events[-1].update(valid=True,epoch=1)
        with self.assertRaisesRegex(ValueError,'epoch'):
            motion_covariance(np.ones((4800,4),complex),0,4800,0.,PoseInterpolator(events,'B'),SCREEN,PROBE,True)

    def test_large_footprint_points_are_visible_but_cannot_support_voxels(self):
        positions=np.array([[0,0,0],[.01,0,0],[.02,0,0],[.03,0,0]])
        matrices=np.array([np.eye(4)*v for v in (1,2,3,10000)],complex)
        bins=interpolation_bins(positions,matrices,[.001,.005,.014,.0635])
        self.assertEqual(bins['excluded_motion_count'],1)
        self.assertEqual(bins['eligible_mask'].tolist(),[True,True,True,False])
        self.assertEqual(len(bins['positions']),3)
        value=local_gaussian([.01,0,0],bins)
        self.assertEqual(value['spatial_bin_count'],3)
        self.assertLess(value['covariance'][0,0].real,3)
        empty=interpolation_bins(positions,matrices,[.1]*4)
        self.assertFalse(local_gaussian([.01,0,0],empty)['known'])

    def moving_poses(self,speed=.6):
        return [dict(kind='pose',serial='B',arrival_lsl=i*.004,position_m=[speed*i*.004,0,0],
                     quaternion_wxyz=[1,0,0,0],epoch=0,valid=True) for i in range(27)]

    def test_adaptive_children_reduce_motion_preserve_duration_and_covariance(self):
        signal=np.ones((4800,4),complex)
        result=adaptive_window_covariances(signal,0,4800,0.,PoseInterpolator(self.moving_poses(),'B'),SCREEN,PROBE,True)
        self.assertEqual(result['split_count'],5)
        children=result['estimates']
        self.assertEqual([c['local_start_frame'] for c in children],[0,960,1920,2880,3840])
        self.assertEqual(sum(c['frames'] for c in children),4800)
        self.assertAlmostEqual(sum(c['duration_seconds'] for c in children),.1)
        self.assertGreater(result['parent_motion']['motion_diameter_m'],.04)
        self.assertTrue(all(c['motion']['motion_diameter_m']<.02 for c in children))
        self.assertTrue(all(0<c['motion_quality']<=1 for c in children))
        average=sum(c['covariance']*c['duration_seconds'] for c in children)/.1
        np.testing.assert_allclose(average,result['parent_covariance'],atol=1e-12)
        self.assertTrue(all(np.linalg.eigvalsh(c['covariance']).min()>-1e-12 for c in children))
        stationary=adaptive_window_covariances(signal,0,4800,0.,PoseInterpolator(self.moving_poses(0),'B'),SCREEN,PROBE,True)
        self.assertEqual(stationary['split_count'],1)
        self.assertEqual(stationary['estimates'][0]['frames'],4800)

    def test_duration_weighted_bins_do_not_gain_weight_from_splitting(self):
        p=np.array([[.001,0,0],[.001,0,0]])
        matrices=np.array([np.eye(4),np.eye(4)*9],complex)
        parent=spatial_bins(p,matrices,weights=[.1,.1],durations=[.1,.1],source_indices=[0,1])
        children=spatial_bins(np.repeat(p[:1],6,axis=0),np.array([np.eye(4)]*5+[np.eye(4)*9]),
            weights=[.02]*5+[.1],durations=[.02]*5+[.1],source_indices=[0]*5+[1])
        self.assertEqual(len(children['positions']),1)
        self.assertEqual(children['source_parent_counts'],[2])
        self.assertAlmostEqual(children['duration_sums'][0],.2)
        self.assertAlmostEqual(children['weight_sums'][0],parent['weight_sums'][0])
        np.testing.assert_allclose(parent['covariances'],children['covariances'],atol=1e-12)
        self.assertGreaterEqual(np.linalg.eigvalsh(children['covariances'][0]).min(),0)

    def test_adaptive_split_cannot_rescue_a_parent_with_invalid_pose_or_gap(self):
        events=self.moving_poses()
        events.append(dict(events[0],arrival_lsl=.003,valid=False))
        with self.assertRaisesRegex(ValueError,'invalid_pose'):
            adaptive_window_covariances(np.ones((4800,4),complex),0,4800,0.,PoseInterpolator(events,'B'),SCREEN,PROBE,True)
        events=[e for e in self.moving_poses() if not .02<e['arrival_lsl']<.072]
        with self.assertRaisesRegex(ValueError,'pose_gap'):
            adaptive_window_covariances(np.ones((4800,4),complex),0,4800,0.,PoseInterpolator(events,'B'),SCREEN,PROBE,True)


if __name__=='__main__':unittest.main()
