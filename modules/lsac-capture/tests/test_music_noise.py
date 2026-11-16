import unittest
import numpy as np
from lsac_capture.music_noise import bandlimit,validate_metadata,OFFICIAL_SHA1,CENTRES,EDGES


class MusicNoiseTests(unittest.TestCase):
    def test_filter_suppresses_unused_low_band_and_preserves_target_bands(self):
        t=np.arange(48000)/48000
        x=sum(np.sin(2*np.pi*f*t) for f in (40,125,500,16000))
        y=bandlimit(x)
        for f,want in [(40,0),(125,1),(500,1),(16000,1)]:
            self.assertAlmostEqual(float(2*np.mean(y*np.sin(2*np.pi*f*t))),want,places=8)

    def test_metadata_requires_traced_derivative_and_valid_bounds(self):
        m=dict(source_sha1=OFFICIAL_SHA1,aes75_compliant=False,rate=48000,channels=2,
            analysis_centres_hz=CENTRES,band_hz=EDGES,seconds=600,peak_dbfs=-12)
        validate_metadata(m)
        for changes in [dict(source_sha1='wrong'),dict(aes75_compliant=True),dict(rate=44100),dict(peak_dbfs=0),dict(seconds=601),dict(band_hz=[125,16000])]:
            with self.assertRaises(ValueError):validate_metadata(dict(m,**changes))


if __name__=='__main__':unittest.main()
