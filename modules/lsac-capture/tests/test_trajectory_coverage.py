import unittest

from lsac_capture.trajectory_coverage import TrajectoryCoverage


class TrajectoryTests(unittest.TestCase):
    def make(self):
        return TrajectoryCoverage(.03,[.35,.55,.75,.95])

    def test_same_path_has_same_occupancy_at_different_callback_rates(self):
        coarse,fine=self.make(),self.make()
        a,b=[-.12,.1,.45],[.12,.1,.45]
        coarse.add((500,'L'),a,b,.2,recorded=True)
        n=180
        for i in range(n):
            u,v=i/n,(i+1)/n
            fine.add((500,'L'),[x+u*(y-x) for x,y in zip(a,b)],
                     [x+v*(y-x) for x,y in zip(a,b)],.2/n,recorded=True)
        self.assertEqual(set(coarse.cells),set(fine.cells))
        self.assertAlmostEqual(sum(x['recorded_length_m'] for x in fine.cells.values()),.24)
        self.assertAlmostEqual(sum(x['recording_seconds'] for x in fine.cells.values()),.2)

    def test_retracing_and_stationary_dwell_do_not_create_more_positions(self):
        c=self.make();a,b=[.001,.19,.451],[.019,.19,.451]
        c.add((500,'LR'),a,b,1,recorded=True)
        count=len(c.cells)
        c.add((500,'LR'),b,a,1,recorded=True)
        c.add((500,'LR'),a,a,100,recorded=True)
        self.assertEqual(len(c.cells),count)
        cells=next(iter(c.summaries().values()))
        self.assertEqual(cells[0]['second_direction_fraction'],0)

    def test_crossing_directions_retained(self):
        c=self.make()
        c.add((1000,'L'),[.001,.19,.451],[.019,.19,.451],1,recorded=True)
        c.add((1000,'L'),[.01,.181,.451],[.01,.199,.451],1,recorded=True)
        cell=next(iter(c.summaries().values()))[0]
        self.assertAlmostEqual(cell['second_direction_fraction'],.5)

    def test_segment_splits_layers_even_if_no_callback_inside_middle(self):
        c=self.make()
        c.add((500,'L'),[0,0,.4],[0,0,.9],1,recorded=True)
        groups=c.summaries()
        self.assertEqual({k[2] for k in groups},{0,1,2})
        self.assertAlmostEqual(sum(x['recorded_length_m'] for x in groups[(500,'L',1)]),.2)
        self.assertAlmostEqual(sum(x['recording_seconds'] for v in groups.values() for x in v),1)

    def test_radial_movement_changes_coverage_inside_one_angular_bin(self):
        c=self.make();c.add((500,'L'),[0,.01,.36],[0,.01,.52],1,recorded=True)
        self.assertGreater(len(c.cells),4)

    def test_distance_to_trajectory_is_metric_not_angular(self):
        c=self.make();c.add((500,'L'),[-.1,.1,.45],[.1,.1,.45],1,recorded=True)
        self.assertAlmostEqual(c.distance_to_recorded_path((500,'L'),[0,.12,.45]),.02)
        self.assertAlmostEqual(c.distance_to_recorded_path((500,'L'),[0,.1,.5]),.05)
        self.assertIsNone(c.distance_to_recorded_path((500,'R'),[0,.1,.45]))

    def test_head_region_clips_segments_at_non_grid_aligned_boundaries(self):
        c=TrajectoryCoverage(.03,[.35,.55,.75,.95],[[-.2,.2],[.115,.185],[0,.95]])
        c.add((500,'L'),[0,.05,.45],[0,.25,.45],2,recorded=True)
        self.assertAlmostEqual(sum(x['recorded_length_m'] for x in c.cells.values()),.07)
        self.assertAlmostEqual(sum(x['recording_seconds'] for x in c.cells.values()),.7)
        self.assertIsNone(c.layer([0,0,.45]))
        self.assertIsNone(c.layer([.21,.15,.45]))

    def test_head_region_excluded_path_cannot_support_nearest_distance(self):
        c=TrajectoryCoverage(.03,[.35,.55,.75,.95],[[-.2,.2],[.1,.3],[0,.95]])
        c.add((500,'L'),[-.1,0,.45],[.1,0,.45],1,recorded=True)
        self.assertFalse(c.cells)
        self.assertIsNone(c.distance_to_recorded_path((500,'L'),[0,.2,.45]))

    def test_quarter_space_clipping_and_group_isolation(self):
        c=self.make();c.add((500,'L'),[0,-.1,.45],[0,.1,.45],1,recorded=True)
        self.assertAlmostEqual(sum(x['recorded_length_m'] for x in c.cells.values()),.1)
        count=len(c.cells);c.add((500,'R'),[0,0,.45],[0,.1,.45],1,recorded=True)
        self.assertEqual(len(c.cells),2*count)


if __name__=='__main__':unittest.main()
