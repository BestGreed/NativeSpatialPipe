"""Check the exported first 100ms against original PCM and a fresh native render."""
import importlib.util
import json
import os
from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
spec = importlib.util.spec_from_file_location('prepare_replay', HERE/'prepare-replay.py')
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)
np = prep.np


class ReplayMeasurementTests(unittest.TestCase):
    def test_first_window_matches_all_original_blocks(self):
        archive_path = os.environ.get("NSP_REPLAY_ARCHIVE")
        if not archive_path:
            self.skipTest("Set NSP_REPLAY_ARCHIVE to run the recorded-audio comparison.")
        archive = Path(archive_path)
        replay = ROOT/'build/wwise-visualization-replay'
        m = json.loads((replay/'chunk-000.json').read_text(encoding='utf-8'))[0]
        source, rendered = [], []
        native = prep.module('fresh_native', ROOT/'tools/endpoint-probe/Native-Roar.py')
        engine = native.PacketRenderer(ROOT/'build/roar-native/release/nsp_croksor.dll',480)
        try:
            for i, (r, data) in enumerate(prep.packets(archive)):
                if i == 10:
                    break
                source.append(np.frombuffer(data,dtype='<f4',offset=r['count']*32).reshape(r['count'],480).copy())
                rendered.append(np.ctypeslib.as_array(engine.render(r,data)).reshape(12,480).copy())
        finally:
            engine.close()
        for values, items in [(source,m['taps']['decoded-input']['objects']),
                              (rendered,m['taps']['render-ready']['channels'])]:
            whole = np.concatenate(values,axis=1).astype(np.float64)
            energies, peaks = np.sum(whole*whole,axis=1), np.max(np.abs(whole),axis=1)
            for i, item in enumerate(items):
                with self.subTest(channel=i):
                    actual = item['meter']
                    self.assertEqual(actual['sampleCount'],4800)
                    self.assertAlmostEqual(actual['sumSquares'],energies[i],places=8)
                    self.assertEqual(actual['samplePeak'],peaks[i])
        self.assertEqual(len(source),10)
        self.assertEqual(len(m['taps']['decoded-input']['objects']),140)
        self.assertEqual(m['sourceSequenceRange'],dict(start=6775,endExclusive=6785))

    def test_no_web_pcm_and_history_coverage(self):
        root = ROOT/'build/wwise-visualization-replay'
        manifest = json.loads((root/'manifest.json').read_text(encoding='utf-8'))
        self.assertTrue(manifest['source']['matchesWwiseSourceManifest'])
        self.assertEqual(manifest['source']['packets'],6000)
        self.assertEqual(manifest['frameCount'],600)
        for window in (600,3600):
            m = json.loads((root/f'history-{window}.json').read_text(encoding='utf-8'))
            self.assertAlmostEqual(m['coverage']['ratio'],60/window)
            self.assertEqual(m['coverage']['validSamples'],2880000)
            self.assertEqual(m['actualRangeUs']['endExclusive'],60000000)
        for chunk in manifest['chunks']:
            frames = json.loads((root/chunk['file']).read_text(encoding='utf-8'))
            for frame in frames:
                self.assertNotIn('pcm',frame)
                self.assertEqual(frame['eventOverflow'],0)


if __name__ == '__main__':
    unittest.main()
