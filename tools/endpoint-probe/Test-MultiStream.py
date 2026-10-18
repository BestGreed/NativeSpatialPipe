"""Real Rust pipelines: interleaved streams, frame epochs and isolation."""
import importlib.util
import json
import pathlib
import struct
import tempfile
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('multi', pathlib.Path(__file__).with_name('Multi-Stream.py'))
multi = importlib.util.module_from_spec(spec)
spec.loader.exec_module(multi)
DLL = ROOT / 'build/telemetry-objects/release/nsp_roar_bridge.dll'


def packet(block, seq, frames=480, value=.125):
    record = dict(block=block, sequence=seq, count=1, frames=[frames])
    return record, struct.pack('<4I4f', 7, frames, 2, 4, 0, 0, -1, 1) + struct.pack('<' + 'f'*frames, *([value]*frames))


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'work')
        self.addCleanup(self.temp.cleanup)
        self.dispatch = multi.Dispatcher(self.temp.name, DLL)
        self.addCleanup(lambda: [s.finish() for s in self.dispatch.streams.values()])

    def send(self, block, seq, **kwargs):
        record, data = packet(block, seq, **kwargs)
        stream = self.dispatch.get(record)
        stream.submit(record, data)
        return stream

    def test_interleaved_independent_sequences_and_pcm(self):
        for seq in range(20):
            a = self.send('0x123', seq, value=.125)
            b = self.send('0x456', seq, value=.25)
        self.assertIsNot(a, b)
        for stream, value in ((a, .125), (b, .25)):
            summary = stream.finish()
            self.assertTrue(summary['passed'], summary)
            self.assertEqual(summary['written'], 20)
            data = (stream.pipeline.out / 'roar-714.f32').read_bytes()
            x = struct.unpack('<'+'f'*(len(data)//4), data)
            for start in range(0, len(x), 480*12):
                self.assertEqual(x[start:start+480*12:12], (value,)*480)
                for channel in range(1,12):
                    self.assertFalse(any(x[start+channel:start+480*12:12]))

    def test_frame_change_and_reset_create_epochs(self):
        a = self.send('0x123', 0)
        b = self.send('0x123', 1, frames=240)
        c = self.send('0x123', 0)
        self.assertEqual(len(self.dispatch.streams), 3)
        for stream in (a,b,c):
            self.assertTrue(stream.finish()['passed'])

    def test_failed_stream_does_not_stop_other_stream(self):
        a = self.send('0x123', 0)
        self.send('0x123', 2)  # discontinuity is rejected by this stream's Rust worker
        time.sleep(.05)
        for seq in range(4):
            b = self.send('0x456', seq)
        self.assertFalse(a.finish()['passed'])
        self.assertTrue(b.finish()['passed'])
        self.assertEqual(b.summary['written'], 4)

    def test_independent_telemetry_services(self):
        self.dispatch.telemetry = True
        a = self.send('0x123', 0)
        b = self.send('0x456', 0)
        deadline = time.monotonic()+15
        while time.monotonic()<deadline:
            rows=json.loads((pathlib.Path(self.temp.name)/'streams.json').read_text())
            if all(row['telemetry'] for row in rows):break
            time.sleep(.02)
        self.assertTrue(all(row['telemetry'] for row in rows), rows)
        self.assertNotEqual(rows[0]['telemetry']['url'], rows[1]['telemetry']['url'])
        for seq in range(1,11):
            self.send('0x123',seq,value=.125)
            self.send('0x456',seq,value=.25)
            time.sleep(.01)
        for stream in (a,b):
            self.assertTrue(stream.finish()['passed'])
            summary=json.loads((stream.pipeline.out/'telemetry-summary.json').read_text())
            self.assertGreater(summary['status']['receivedBlocks'],0)
            self.assertEqual(summary['status']['invalidBlocks'],0)


if __name__ == '__main__':
    unittest.main()
