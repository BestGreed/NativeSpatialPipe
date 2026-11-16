import unittest
import json

import numpy as np

from lsac_capture.coverage import Coverage
from lsac_capture.scan_window import ScanWindow
from lsac_capture.trajectory_coverage import TrajectoryCoverage
from test_coverage import calibration


class WindowTests(unittest.TestCase):
    def make(self):
        return ScanWindow(Coverage(calibration()).geometry,{})

    def test_reviewed_shape_upper_straight_then_level_lower_only_decelerates(self):
        w=self.make();s=w.settings
        z=np.linspace(.3,.6,20);top=np.array([w.limits(x)[1] for x in z])
        np.testing.assert_allclose(np.diff(top)/np.diff(z),s['upper_slope'],atol=1e-10)
        self.assertAlmostEqual(w.limits(1.1)[1],w.limits(1.2)[1])
        z=np.linspace(w.lower_start[0],w.lower_start[0]+s['lower_transition_m'],100)
        lower=np.array([w.limits(x)[0] for x in z]);slopes=np.diff(lower)/np.diff(z)
        self.assertTrue(np.all(slopes>=-1e-10));self.assertTrue(np.all(np.diff(slopes)<=1e-10))

    def test_window_clips_audio_supported_path_at_profile_boundaries(self):
        w=self.make();low,high=w.limits(.5);h=w.height
        c=TrajectoryCoverage(.03,[.35,.55,.75,.95],window=w)
        c.add((500,'LR'),[0,low-h-.1,.5],[0,high-h+.1,.5],1,recorded=True)
        self.assertAlmostEqual(sum(x['recorded_length_m'] for x in c.cells.values()),high-low,places=7)
        self.assertFalse(w.contains([0,high-h+.01,.5],0))
        self.assertEqual(w.evaluate([0,low-h-.01,.5],0)['reason'],'too_low')
        json.dumps(c.snapshot(),allow_nan=False)

    def test_world_height_and_per_layer_horizontal_angles(self):
        w=self.make();lo,hi=w.limits(.5);p=[0,(lo+hi)/2-w.height,.5]
        self.assertTrue(w.contains(p,0))
        self.assertGreater(w.evaluate(p,0)['lateral_limit_m'],w.evaluate(p,2)['lateral_limit_m'])
        p[0]=2;self.assertFalse(w.contains(p,0))

    def test_transition_polynomial_roots_leave_no_invalid_segment_middle(self):
        w=self.make();a=[0,.1,.5];b=[0,.6,1.1]
        cuts=sorted(w.cuts(a,b))
        self.assertGreater(len(cuts),3)
        for lo,hi in zip(cuts,cuts[1:]):
            # Within each root-delimited interval, upper/lower inclusion cannot
            # flip, even through the curved-to-horizontal join.
            values=[]
            for f in (.1,.5,.9):
                t=lo+(hi-lo)*f;p=[x+t*(y-x) for x,y in zip(a,b)]
                low,high=w.limits(p[2]);height=p[1]+w.height
                values.append((height>=low,height<=high))
            self.assertEqual(values[0],values[1]);self.assertEqual(values[1],values[2])


if __name__=='__main__':unittest.main()
