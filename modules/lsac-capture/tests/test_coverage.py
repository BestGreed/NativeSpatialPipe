import math
import unittest

from lsac_capture.calibration import B_SERIAL
from lsac_capture.coverage import Coverage, validate_plan


def calibration():
    return dict(world_id='test-world', screen_world_pose=dict(position_m=[0,.2,0],quaternion_wxyz=[1,0,0,0]),
                reference_world_pose=dict(position_m=[0,0,0],quaternion_wxyz=[math.sqrt(.5),-math.sqrt(.5),0,0]),
                probe=dict(known_offset_tracker_m=[0,0,.10673]))


def pose(t, position=None, valid=True):
    return dict(kind='pose',serial=B_SERIAL,position_m=position or [0,.3,.34327],
                quaternion_wxyz=[1,0,0,0],arrival_lsl=t,valid=valid,source_world_id='test-world')


def audio(t, start=0, **kwargs):
    return dict(kind='audio_block',arrival_lsl=t,sample_start_i64=start,frames=480,rate=48000,channels=4,epoch=0,**kwargs)


class CoverageTests(unittest.TestCase):
    def test_capsule_screen_distance_and_boundary_ownership(self):
        c=Coverage(calibration())
        xyz=c.position(pose(1))
        self.assertAlmostEqual(xyz[1],.1)
        self.assertAlmostEqual(xyz[2],.45)
        self.assertEqual(c.locate([0,0,.55])[0],1)
        self.assertEqual(c.locate([0,0,.75])[0],2)
        self.assertIsNone(c.locate([0,0,.95]))
        self.assertIsNone(c.locate([0,-.01,.45]))

    def test_stationary_dwell_does_not_increase_coverage_area(self):
        c=Coverage(calibration(),dict(recording_target_seconds=.01))
        c.set_stage(500,'L',0,'test')
        for i in range(100):
            t=1+i*.01;c.feed_pose(pose(t));c.feed_audio(audio(t,i*480))
        g=next(g for g in c.snapshot()['groups'] if g['hz']==500 and g['route']=='L' and g['layer']==0)
        self.assertEqual(g['recorded_cells'],1)
        self.assertEqual(g['target_met_cells'],1)
        self.assertAlmostEqual(g['recording_seconds'],1)
        self.assertFalse(g['progress_target_met'])
        self.assertEqual(g['acoustic_snr_status'],'not_assessed')

    def test_clipping_stale_and_duplicate_packets_cannot_earn_time(self):
        c=Coverage(calibration());c.set_stage(500,'LR',0,'test')
        c.feed_pose(pose(1));c.feed_audio(audio(1),clipped=True)
        c.feed_audio(audio(1,480));c.feed_audio(audio(1,480))
        c.feed_audio(audio(2,960))
        self.assertAlmostEqual(sum(x['seconds'] for x in c.cells.values()),.01)
        self.assertEqual(c.diagnostics['duplicate_audio'],1)
        self.assertEqual(c.diagnostics['no_fresh_pose'],1)

    def test_groups_switches_and_invalid_pose_are_isolated(self):
        c=Coverage(calibration());c.set_stage(500,'L',1,'test')
        c.feed_pose(pose(1.01));c.feed_audio(audio(1.01))
        self.assertEqual(c.diagnostics['switch_guard'],1)
        c.feed_pose(pose(2));c.feed_audio(audio(2,480))
        c.set_stage(1000,'R',2.1,'test');c.feed_pose(pose(3));c.feed_audio(audio(3,960))
        c.feed_pose(pose(3.01,valid=False));c.feed_audio(audio(3.01,1440))
        self.assertEqual(len(c.cells),2)
        self.assertAlmostEqual(sum(x['seconds'] for x in c.cells.values()),.02)

    def test_world_mismatch_invalidates_pose(self):
        c=Coverage(calibration());c.set_stage(500,'L',0,'test')
        e=pose(1);e['source_world_id']='other';c.feed_pose(e);c.feed_audio(audio(1))
        self.assertIsNone(c.pose)
        self.assertEqual(c.diagnostics['world_mismatch'],1)
        self.assertFalse(c.cells)

    def test_plan_validation(self):
        with self.assertRaises(ValueError):validate_plan(dict(layer_edges_m=[.35,.55,.55,.95]))
        with self.assertRaises(ValueError):validate_plan(dict(target_radii_m=[.1,.65,.85]))
        with self.assertRaises(ValueError):validate_plan(dict(recording_target_seconds=0))

    def test_full_rate_pose_path_and_recorded_interval_are_kept(self):
        c=Coverage(calibration());c.set_stage(500,'L',0,'test')
        for i in range(20):
            t=1+i/180
            c.feed_pose(pose(t,[i*.002,.3,.34327]))
            if i%2==1:c.feed_audio(audio(t,(i//2)*480))
        snap=c.snapshot()['trajectory']
        self.assertEqual(snap['pose_segments'],19)
        self.assertAlmostEqual(snap['max_pose_segment_spacing_m'],.002)
        self.assertGreater(snap['groups'][0]['recorded_path_length_m'],0)
        self.assertIsNone(snap['coverage_fraction'])

    def test_head_height_uses_palm_plane_not_tilted_screen_plane(self):
        c=Coverage(calibration(),dict(head_centre_palm_m=[0,.4,.5],head_half_height_m=.1))
        # Test calibration screen centre is 0.2 m above palm: head ROI is
        # [0.3,0.5] m above palm, or [0.1,0.3] m above screen centre.
        self.assertAlmostEqual(c.trajectory.region_bounds[1][0],.1)
        self.assertAlmostEqual(c.trajectory.region_bounds[1][1],.3)
        self.assertIsNone(c.locate([0,0,.45]))
        self.assertIsNotNone(c.locate([0,.2,.45]))


if __name__=='__main__':unittest.main()
