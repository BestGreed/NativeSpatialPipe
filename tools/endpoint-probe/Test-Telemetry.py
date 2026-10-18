"""Standard-library-only DLL integration tests; never installs Hook or touches audio devices."""
import argparse
import ctypes as C
import importlib.util
import json
import math
import os
from pathlib import Path
import struct
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser()
parser.add_argument('--dll', type=Path, default=ROOT / 'build/telemetry/release/nsp_roar_bridge.dll')
parser.add_argument('--report', type=Path, help='Save the measured on/off equivalence report (no PCM)')
args, remaining = parser.parse_known_args()
DLL = C.CDLL(str(args.dll.resolve()))
for name, types, result in (
    ('nsp_pipeline_create', [C.c_uint32] * 4 + [C.c_char_p], C.c_void_p),
    ('nsp_pipeline_submit', [C.c_void_p, C.c_uint64, C.c_uint32, C.c_void_p, C.c_size_t], C.c_int32),
    ('nsp_pipeline_finish', [C.c_void_p, C.c_void_p, C.c_size_t], C.c_size_t),
    ('nsp_pipeline_telemetry_enable', [C.c_void_p, C.c_uint32], C.c_int32),
    ('nsp_pipeline_telemetry_read', [C.c_void_p, C.c_void_p, C.c_size_t], C.c_size_t),
    ('nsp_pipeline_destroy', [C.c_void_p], None),
):
    fn = getattr(DLL, name)
    fn.argtypes, fn.restype = types, result


def read_json(fn, handle):
    # A live snapshot can grow between sizing and copying. Retry boundedly;
    # undersized reads return the new requirement and leave the buffer untouched.
    size = fn(handle, None, 0)
    for _ in range(8):
        if not 0 < size < 1024 * 1024:
            raise AssertionError(f'invalid native payload size {size}')
        buf = C.create_string_buffer(size)
        needed = fn(handle, buf, size)
        if needed <= size:
            return json.loads(buf.value)
        size = needed
    raise AssertionError('unstable native payload')


def packet(frames, amplitude):
    types = [2 << c for c in range(12)]
    props = b''.join(struct.pack('<4I4f', c + 1, frames, typ, 4, 0., 0., -1., 1.) for c, typ in enumerate(types))
    pcm = b''.join(struct.pack('<f', amplitude * (c + 1) / 12 * math.sin(i * math.tau / 48))
                   for c in range(12) for i in range(frames))
    return props + pcm


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'build')
        self.handles = []
        self.previous = os.environ.pop('NSP_TELEMETRY', None)

    def tearDown(self):
        for handle in self.handles:
            DLL.nsp_pipeline_destroy(handle)
        self.temp.cleanup()
        if self.previous is not None:
            os.environ['NSP_TELEMETRY'] = self.previous

    def create(self, name, frames=480):
        path = Path(self.temp.name) / f'{name}.f32'
        handle = DLL.nsp_pipeline_create(frames, 64, 0, 1, str(path).encode())
        self.assertTrue(handle)
        self.handles.append(handle)
        return handle, path

    def submit(self, handle, seq, amplitude=1., frames=480):
        data = packet(frames, amplitude)
        self.assertEqual(DLL.nsp_pipeline_submit(handle, seq, 12, data, len(data)), 0)
        time.sleep(.003)

    def test_actual_roar_meter_and_audio_byte_equivalence(self):
        off, off_path = self.create('off')
        on, on_path = self.create('on')
        self.assertEqual(read_json(DLL.nsp_pipeline_telemetry_read, on)['status'], 'disabled')
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(on, 1), 0)
        for seq in range(40):
            self.submit(off, seq)
            self.submit(on, seq)
        off_report = read_json(DLL.nsp_pipeline_finish, off)
        on_report = read_json(DLL.nsp_pipeline_finish, on)
        self.assertTrue(off_report['passed'])
        self.assertTrue(on_report['passed'])
        self.assertEqual(off_path.read_bytes(), on_path.read_bytes())
        tap = on_report['telemetry']
        self.assertEqual(tap['status'], 'stopped')
        self.assertEqual(tap['publishedBlocks'], 40)
        self.assertEqual(tap['consumedBlocks'], 40)
        self.assertEqual(tap['poolDroppedBlocks'], 0)
        snapshot = tap['snapshot']
        self.assertEqual(snapshot['quality'], 'live')
        start, end = (snapshot['sourceSequenceRange'][k] for k in ('start', 'endExclusive'))
        samples = struct.unpack('<' + 'f' * (on_path.stat().st_size // 4), on_path.read_bytes())
        for c, channel in enumerate(snapshot['channels']):
            values = samples[start * 480 * 12 + c:end * 480 * 12:12]
            meter = channel['meter']
            self.assertEqual(meter['sampleCount'], len(values))
            self.assertAlmostEqual(meter['sumSquares'], sum(x * x for x in values), places=8)
            self.assertEqual(meter['samplePeak'], max(map(abs, values)))
        self.assertEqual(on_report['telemetryPublishMs']['count'], 40)
        self.assertEqual(off_report['telemetryPublishMs']['count'], 0)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps({
                'scope': 'Synthetic 12-static-object input through real ROAR; 40 blocks; no Hook/device or performance acceptance',
                'dll': str(args.dll.resolve()), 'audioByteEquivalent': True,
                'allChannelMetersMatchRenderedPcm': True, 'off': off_report, 'on': on_report,
            }, indent=2), encoding='utf-8')

    def test_runtime_toggle_stale_and_read_buffer_contract(self):
        handle, _ = self.create('toggle')
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(handle, 2), -1)
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(handle, 1), 0)
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(handle, 1), 0)
        self.submit(handle, 0)
        time.sleep(.15)
        first = read_json(DLL.nsp_pipeline_telemetry_read, handle)
        self.assertEqual(first['snapshot']['streamEpoch'], 1)
        self.assertEqual(first['status'], 'live')
        time.sleep(.3)
        self.assertEqual(read_json(DLL.nsp_pipeline_telemetry_read, handle)['status'], 'stale')
        buf = C.create_string_buffer(b'keep')
        self.assertGreater(DLL.nsp_pipeline_telemetry_read(handle, buf, len(buf)), len(buf))
        self.assertEqual(buf.value, b'keep')
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(handle, 0), 0)
        self.submit(handle, 1)
        self.assertIsNone(read_json(DLL.nsp_pipeline_telemetry_read, handle)['snapshot'])
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(handle, 1), 0)
        self.submit(handle, 2, amplitude=0.)
        report = read_json(DLL.nsp_pipeline_finish, handle)
        self.assertTrue(report['passed'])
        snapshot = report['telemetry']['snapshot']
        self.assertEqual(snapshot['streamEpoch'], 3)
        self.assertEqual(snapshot['sourceSequenceRange']['start'], 2)
        self.assertTrue(all(c['meter']['silent'] for c in snapshot['channels']))
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(handle, 1), -1)

    def test_unsupported_frames_do_not_fail_audio(self):
        handle, _ = self.create('unsupported', frames=240)
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(handle, 1), -1)
        self.submit(handle, 0, frames=240)
        report = read_json(DLL.nsp_pipeline_finish, handle)
        self.assertTrue(report['passed'])
        self.assertEqual(report['telemetry']['status'], 'unsupported')

    def test_over_full_scale_remains_unclipped(self):
        handle, _ = self.create('over')
        self.assertEqual(DLL.nsp_pipeline_telemetry_enable(handle, 1), 0)
        self.submit(handle, 0, amplitude=2.)
        report = read_json(DLL.nsp_pipeline_finish, handle)
        self.assertTrue(report['passed'])
        meter = report['telemetry']['snapshot']['channels'][-1]['meter']
        self.assertEqual(meter['samplePeak'], 2.)
        self.assertTrue(meter['overFullScale'])

    def test_python_control_wrapper(self):
        spec = importlib.util.spec_from_file_location('telemetry_control', ROOT / 'tools/endpoint-probe/Native-Roar.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        pipeline = module.NativePipeline(Path(self.temp.name), args.dll.resolve())
        pipeline.prepare(480)
        try:
            with self.assertRaises(TypeError):
                pipeline.set_telemetry(1)
            pipeline.set_telemetry(True)
            self.assertEqual(pipeline.telemetry()['status'], 'warmup')
            record = {'block': 'telemetry-test', 'sequence': 0, 'count': 12, 'frames': [480] * 12}
            pipeline.submit(record, packet(480, 1.))
            time.sleep(.15)
            self.assertEqual(pipeline.telemetry()['snapshot']['validSamples'], 480)
            pipeline.set_telemetry(False)
            self.assertEqual(pipeline.telemetry()['status'], 'disabled')
        finally:
            self.assertTrue(pipeline.finish()['passed'])


if __name__ == '__main__':
    unittest.main(argv=[__file__, *remaining])
