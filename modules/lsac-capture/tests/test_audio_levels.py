import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import numpy as np

from lsac_capture.audio_levels import report


class LevelTests(unittest.TestCase):
    def test_band_rise_and_independent_clip_report_are_adc_levels(self):
        rate=48000;t=np.arange(96000)/rate
        amplitude=np.where((t>=.6)&(t<1.4),4000,1000)
        mono=np.rint(amplitude*np.sin(2*np.pi*500*t)).astype(np.int32)
        samples=np.repeat(mono[:,None],4,axis=1);samples[-1,1]=-8388608
        raw=np.stack((samples&255,(samples>>8)&255,(samples>>16)&255),axis=-1).astype(np.uint8).tobytes()
        events=[dict(kind="audio_block",sample_start_i64=i*4800,frames=4800,pcm_byte_offset=i*4800*12,
                     lsl_first_frame=10+i*.1,device_qpc_100ns_u64=100000000+i*1000000,
                     device_position_u64=i*4800,rate=48000,channels=4,container_bits=24,epoch=0) for i in range(20)]
        events.extend([dict(kind="playback_started",arrival_lsl=10.6),dict(kind="playback_finished",arrival_lsl=11.4)])
        config=dict(audio=dict(rate=48000,user_reported_mic_gain=45))
        with patch.object(Path,"read_text",return_value=json.dumps(dict(config=config))), \
             patch.object(Path,"read_bytes",return_value=raw), \
             patch.object(Path,"open",side_effect=lambda *a,**k:io.StringIO("\n".join(json.dumps(e) for e in events))):
            result=report("fixture")
        self.assertAlmostEqual(result["median_band_rise_db"][0],20*np.log10(4),places=2)
        self.assertEqual(result["clipped_counts_per_channel"],[0,1,0,0])
        self.assertEqual(result["recording_peak_dbfs"][1],0.)
        self.assertFalse(result["db_spl"])
        self.assertFalse(result["raw_timestamps_modified"])


if __name__=="__main__":unittest.main()
