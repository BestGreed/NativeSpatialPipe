import unittest
import numpy as np
from lsac_capture.fractional_gpu import band_grid,band_bin_limits,segments,numpy_reference,fft_length


class FractionalBandTests(unittest.TestCase):
    def test_partial_tail_uses_smooth_fft_size(self):
        self.assertEqual(fft_length(85144),96000)
        self.assertEqual(fft_length(144000),144000)
    def test_partition_has_no_shared_fft_bins(self):
        centres,bands=band_grid();self.assertEqual(len(centres),96)
        np.testing.assert_allclose(bands[:-1,1],bands[1:,0])
        limits=band_bin_limits(bands,144000,48000)
        np.testing.assert_array_equal(limits[:-1,1],limits[1:,0])
        self.assertEqual(limits[5,1],375)  # 125Hz lies on a shared mathematical edge.
        self.assertEqual(limits[6,0],375)

    def test_segments_preserve_all_eligible_frames_at_spatial_and_rotation_boundaries(self):
        ids=np.r_[np.full(191,0),[1,1,-1],np.full(250,2)]
        a,b,keys=segments(ids)
        self.assertEqual(sum(b-a),sum(ids>=0))
        for begin,end,key in zip(a,b,keys):
            self.assertLessEqual(end-begin,192)
            self.assertEqual(begin//192,(end-1)//192)
            self.assertTrue(np.all(ids[begin:end]==key))
        a,b,_=segments(np.full(20,-1));self.assertEqual(len(a),0)

    def test_independent_reference_preserves_complex_quadrature_and_energy(self):
        rate=48000;n=4800;t=np.arange(n)/rate
        x=np.column_stack([np.cos(2*np.pi*1000*t),np.sin(2*np.pi*1000*t),np.zeros(n),np.zeros(n)])
        rotations=np.tile(np.eye(3),(25,1,1));ids=np.zeros(n,dtype=int)
        c=numpy_reference(x,rate,[[950,1050]],0,n,ids,rotations,True)[0,0]/n
        self.assertAlmostEqual(c[0,0].real,.5,places=10)
        self.assertAlmostEqual(c[0,1].imag,.5,places=10)
        self.assertAlmostEqual(c[1,0].imag,-.5,places=10)


if __name__=='__main__':unittest.main()
