import unittest
from lsac_capture.tap_triplet import fit_triplet


class TripletTests(unittest.TestCase):
    def test_three_taps_estimate_offset_but_never_drift(self):
        fit=fit_triplet([1.1,2.101,3.099],[1,2,3])
        self.assertAlmostEqual(fit["offset_seconds"],.1)
        self.assertIsNone(fit["drift_ppm"])
        self.assertTrue(fit["within_operational_tolerance"])

    def test_outlier_is_retained_and_fails_operational_gate(self):
        fit=fit_triplet([1.1,2.1,3.12],[1,2,3])
        self.assertFalse(fit["within_operational_tolerance"])
        self.assertEqual(len(fit["residuals_seconds"]),3)

    def test_missing_extra_or_reordered_pairs_rejected(self):
        for audio,imu in (([1,2],[1,2]),([1,2,3,4],[1,2,3,4]),([2,1,3],[1,2,3])):
            with self.assertRaises(ValueError):fit_triplet(audio,imu)


if __name__=="__main__":unittest.main()
