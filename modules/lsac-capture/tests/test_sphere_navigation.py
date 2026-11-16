import json
import math
import unittest

import numpy as np

from lsac_capture.sphere_navigation import SphereNavigator, _real_harmonics


class SphereNavigatorTests(unittest.TestCase):
    def make(self, **kwargs):
        return SphereNavigator([.45, .65, .90], **kwargs)

    def test_equal_solid_angle_grid_and_sh_normalization(self):
        nav = self.make()
        state = nav.snapshot()
        self.assertEqual(state['angular_grid']['cells_per_layer'], 192)
        self.assertAlmostEqual(sum(c['solid_angle_sr'] for c in state['layers'][0]['cells']), 4 * math.pi)
        for order in range(7):
            rows = _real_harmonics(nav.directions, order)
            self.assertEqual(rows.shape, (192, (order + 1) ** 2))
            np.testing.assert_allclose(np.sum(rows * rows, axis=1), 1., atol=2e-14)

    def test_initial_state_does_not_claim_acoustic_confidence(self):
        state = self.make().snapshot()
        self.assertEqual(state['basis'], 'geometry_only')
        self.assertFalse(state['acoustic_confidence_validated'])
        self.assertFalse(state['verified'])
        self.assertEqual(state['recording_bin_count'], 0)
        self.assertTrue(all(layer['geometric_rank'] == 0 for layer in state['layers']))
        self.assertTrue(all(layer['geometric_condition_number'] is None for layer in state['layers']))
        self.assertTrue(all(layer['coverage_fraction'] == 0 for layer in state['layers']))
        json.dumps(state, allow_nan=False)

    def test_invalid_or_nonrecorded_samples_never_count(self):
        nav = self.make()
        for point, flags in [([.45, 0, 0], {'recording': False}),
                             ([.45, 0, 0], {'valid': False}),
                             ([math.nan, 0, 0], {}), ([0, 0, 0], {}),
                             ([0, 0], {}), ([2., 0, 0], {})]:
            self.assertFalse(nav.add_sample(point, **flags))
        self.assertEqual(nav.snapshot()['recording_bin_count'], 0)

    def test_callback_rate_and_dwell_do_not_increase_gram(self):
        nav = self.make()
        self.assertTrue(nav.add_sample([.451, .001, .001]))
        initial = nav.snapshot()
        for _ in range(10000):
            self.assertFalse(nav.add_sample([.452, .002, .002]))
        current = nav.snapshot()
        self.assertEqual(current['recording_bin_count'], 1)
        self.assertEqual(current['repeated_bin_samples'], 10000)
        self.assertEqual(initial['geometry_risk'], current['geometry_risk'])

    def test_radial_layers_do_not_complete_each_other_and_actual_radius_retained(self):
        nav = self.make()
        initial = nav.snapshot()
        self.assertTrue(nav.add_sample([.472, .001, .002]))
        state = nav.snapshot()
        self.assertEqual([layer['recorded_bin_count'] for layer in state['layers']], [1, 0, 0])
        self.assertAlmostEqual(state['layers'][0]['actual_radius_min_m'], np.linalg.norm([.472, .001, .002]))
        self.assertLess(state['layers'][0]['geometry_risk'], initial['layers'][0]['geometry_risk'])
        self.assertEqual(state['layers'][1]['geometry_risk'], initial['layers'][1]['geometry_risk'])
        self.assertTrue(nav.add_sample([.65, 0, 0]))
        self.assertTrue(nav.add_sample([.90, 0, 0]))
        self.assertEqual([layer['recorded_bin_count'] for layer in nav.snapshot()['layers']], [1, 1, 1])

    def test_angular_cell_contribution_is_bounded(self):
        nav = self.make(max_bins_per_angular_cell=2)
        self.assertTrue(nav.add_sample([.39, 0, 0]))
        self.assertTrue(nav.add_sample([.43, 0, 0]))
        risk = nav.snapshot()['geometry_risk']
        self.assertTrue(nav.add_sample([.47, 0, 0]))
        self.assertTrue(nav.add_sample([.51, 0, 0]))
        self.assertEqual(nav.snapshot()['geometry_risk'], risk)
        self.assertEqual(nav.snapshot()['recording_bin_count'], 4)

    def test_reported_gain_matches_geometry_risk_reduction(self):
        nav = self.make()
        state = nav.snapshot()
        target = state['recommendation']
        before = state['layers'][target['layer_index']]['geometry_risk']
        self.assertTrue(nav.add_sample(target['point_local_m']))
        after = nav.snapshot()['layers'][target['layer_index']]['geometry_risk']
        self.assertAlmostEqual(before - after, target['information_gain'], places=10)

    def test_directional_diversity_increases_data_rank(self):
        nav = self.make(order=3)
        for direction in nav.directions[::3]:
            nav.add_sample(direction * .45)
        state = nav.snapshot()
        self.assertEqual(state['layers'][0]['geometric_rank'], 16)
        self.assertEqual(state['layers'][1]['geometric_rank'], 0)
        self.assertLess(state['layers'][0]['geometry_risk'], state['layers'][1]['geometry_risk'])
        np.testing.assert_allclose(nav._inverse[0],
                                   np.linalg.inv(nav._grams[0] + .1 * np.eye(16)), atol=1e-12)

    def test_target_is_locked_until_recorded_arrival_or_skip(self):
        nav = self.make()
        first = nav.snapshot([0, .45, 0])['recommendation']
        second = nav.snapshot([0, -.9, .1])['recommendation']
        self.assertEqual(first, second)
        self.assertFalse(nav.add_sample(first['point_local_m'], recording=False))
        self.assertEqual(nav.snapshot(first['point_local_m'])['recommendation'], first)
        self.assertTrue(nav.add_sample(first['point_local_m']))
        third = nav.snapshot()['recommendation']
        self.assertNotEqual((third['layer_index'], third['cell_index']),
                            (first['layer_index'], first['cell_index']))

    def test_skip_changes_target_without_claiming_coverage(self):
        nav = self.make()
        first = nav.snapshot()['recommendation']
        self.assertTrue(nav.skip())
        next_state = nav.snapshot()
        second = next_state['recommendation']
        self.assertNotEqual((first['layer_index'], first['cell_index']),
                            (second['layer_index'], second['cell_index']))
        self.assertEqual(next_state['recording_bin_count'], 0)
        self.assertEqual(next_state['skipped_target_count'], 1)
        cell = next_state['layers'][first['layer_index']]['cells'][first['cell_index']]
        self.assertTrue(cell['skipped'])
        self.assertFalse(cell['visited'])

    def test_stale_tracking_hides_but_does_not_retarget(self):
        nav = self.make()
        first = nav.snapshot()['recommendation']
        state = nav.snapshot([0, 0, .9], stale=True)
        self.assertIsNone(state['recommendation'])
        self.assertEqual(state['recommendation_status'], 'tracking_stale')
        self.assertEqual(nav.snapshot()['recommendation'], first)
        self.assertIsNone(nav.snapshot([math.nan, 0, 0])['recommendation'])

    def test_height_limits_preserve_unmeasured_caps_and_local_arc(self):
        nav = self.make(min_z_m=-.12, max_z_m=.12)
        state = nav.snapshot()
        target = state['recommendation']
        self.assertIsNotNone(target)
        self.assertTrue(all(-.12 <= point[2] <= .12 for point in target['path_local_m']))
        self.assertGreater(len(target['path_local_m']), 1)
        radius = state['layers'][target['layer_index']]['radius_m']
        start = np.asarray(target['path_local_m'][0]) / radius
        for point in target['path_local_m']:
            self.assertAlmostEqual(float(np.linalg.norm(point)), radius)
            angle = math.acos(float(np.clip(start @ (np.asarray(point) / radius), -1, 1)))
            self.assertLessEqual(angle, math.radians(18) + 1e-12)
        for layer in state['layers']:
            self.assertGreater(layer['unreachable_unvisited_fraction'], 0)
            self.assertEqual(layer['coverage_fraction'], 0)
            self.assertEqual(layer['north_cap_recorded_bins'], 0)
            self.assertEqual(layer['south_cap_recorded_bins'], 0)
        nav.add_sample([0, 0, .45])
        state = nav.snapshot()
        self.assertEqual(state['layers'][0]['north_cap_recorded_bins'], 1)
        nav.set_reach_limits(min_z_m=.4, max_z_m=.8)
        new_target = nav.snapshot()['recommendation']
        self.assertTrue(all(.4 - 1e-12 <= p[2] <= .8 + 1e-12 for p in new_target['path_local_m']))
        self.assertEqual(nav.snapshot()['recording_bin_count'], 1)

    def test_no_reachable_target_is_not_completion(self):
        nav = self.make(min_z_m=2., max_z_m=3.)
        state = nav.snapshot()
        self.assertIsNone(state['recommendation'])
        self.assertEqual(state['recommendation_status'], 'no_unskipped_reachable_target')
        self.assertTrue(all(layer['coverage_fraction'] == 0 for layer in state['layers']))
        self.assertTrue(all(layer['unreachable_unvisited_fraction'] == 1 for layer in state['layers']))

    def test_tightened_height_limit_checks_whole_locked_arc(self):
        nav = self.make()
        for _ in range(20):
            target = nav.snapshot()['recommendation']
            heights = [p[2] for p in target['path_local_m']]
            if max(heights) - min(heights) > 1e-6:
                break
            nav.skip()
        self.assertGreater(max(heights) - min(heights), 1e-6)
        start = heights[0]
        if max(heights) > start + 1e-6:
            bound = (start + max(heights)) / 2
            nav.set_reach_limits(max_z_m=bound)
            self.assertTrue(all(p[2] <= bound + 1e-12
                                for p in nav.snapshot()['recommendation']['path_local_m']))
        else:
            bound = (start + min(heights)) / 2
            nav.set_reach_limits(min_z_m=bound)
            self.assertTrue(all(p[2] >= bound - 1e-12
                                for p in nav.snapshot()['recommendation']['path_local_m']))

    def test_reset_and_json_snapshot_do_not_expose_mutable_lock(self):
        nav = self.make(min_z_m=-.2, max_z_m=.2)
        state = nav.snapshot()
        original = list(state['recommendation']['point_local_m'])
        state['recommendation']['point_local_m'][0] = 999
        state['recommendation']['path_local_m'][0][0] = 999
        self.assertEqual(nav.snapshot()['recommendation']['point_local_m'], original)
        nav.add_sample(original)
        nav.snapshot()
        nav.skip()
        nav.reset()
        state = nav.snapshot()
        self.assertEqual(state['recording_bin_count'], 0)
        self.assertEqual(state['skipped_target_count'], 0)
        self.assertEqual(state['reach_limits'], {'min_z_m': -.2, 'max_z_m': .2})
        json.dumps(state, allow_nan=False)

    def test_bad_configuration_rejected(self):
        for radii in ([], [.5, .4], [.5, .5], [math.nan], [-1]):
            with self.assertRaises(ValueError):
                SphereNavigator(radii)
        for kwargs in ({'order': 7}, {'order': 1.5}, {'angular_cells': 10},
                       {'regularization': 0}, {'spatial_bin_size_m': 0},
                       {'shell_half_width_m': -1}, {'min_z_m': 1, 'max_z_m': 0}):
            with self.assertRaises(ValueError):
                self.make(**kwargs)


if __name__ == '__main__':
    unittest.main()
