"""Output-only layout dimensions; capture/object semantics stay unchanged."""
import struct
import unittest
from engine import HEADER, contract
from soundscape import SoundscapeEngine
from layout_presets import LAYOUTS


class OutputLayouts(unittest.TestCase):
    def test_seven_output_layouts_through_snapshot_history_and_contract(self):
        for layout, channels in LAYOUTS.items():
            with self.subTest(layout=layout):
                engine=SoundscapeEngine(output_layout=layout)
                count=len(channels)
                raw=HEADER.pack(b'NSPT',1,count,480,48000,1,0)
                raw+=struct.pack('<'+'f'*(count*480),*([.25]*(count*480)))
                self.assertTrue(engine.ingest(raw))
                engine.flush(True)
                for message in (engine.layout(),engine.capabilities(),engine.latest,engine.history()):
                    contract.validate(message)
                self.assertEqual([r['channelId'] for r in engine.layout()['channels']],channels)
                self.assertEqual(len(engine.latest['taps']['render-ready']['channels']),count)
                self.assertEqual(len(engine.history()['channels']),count)
                self.assertEqual(engine.history()['coverage']['validSamples'],480)
                self.assertFalse(engine.ingest(raw[:-4]))
