"""Non-audio-thread dispatcher. Each block/frame epoch owns a Rust pipeline.

Telemetry processes start on control threads, never on the receiver callback.
Their addresses remain separate: samples and object identities are not mixed.
"""
import importlib.util
import json
import pathlib
import sys
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]


class Stream:
    def __init__(self, pipeline, block, frames, telemetry, publish):
        self.pipeline = pipeline
        self.block = block
        self.frames = frames
        self.last = None
        self.rejected = 0
        self.received = 0
        self.last_seen_ns = None
        self.last_publish = 0
        self.error = None
        self.service = None
        self.telemetry_error = None
        self.stop = threading.Event()
        self.thread = None
        self.summary = None
        self.publish = publish
        if telemetry and frames == 480:
            self.thread = threading.Thread(target=self._telemetry, name='stream-telemetry', daemon=True)
            self.thread.start()

    def info(self):
        return dict(block=self.block, frames=self.frames, directory=self.pipeline.out.name,
                    lastSequence=self.last, receivedPackets=self.received, lastSeenUnixNs=self.last_seen_ns,
                    rejectedPackets=self.rejected, error=self.error,
                    telemetry=self.service.info if self.service else None,
                    telemetryError=self.telemetry_error)

    def _telemetry(self):
        try:
            sys.path.insert(0, str(ROOT / 'tools/telemetry'))
            from client import TelemetryService
            self.service = TelemetryService()
            self.service.attach(self.pipeline)
            self.publish()
            last = None
            next_query = 0
            with (self.pipeline.out / 'telemetry-snapshots.jsonl').open('x', encoding='utf-8') as output:
                while not self.stop.is_set():
                    self.service.pump(self.pipeline)
                    if time.monotonic() >= next_query:
                        frame = self.service.query('snapshot')
                        if frame is not None:
                            ident = frame['streamEpoch'], frame['telemetrySeq']
                            if ident != last:
                                output.write(json.dumps(frame, separators=(',', ':')) + '\n')
                                output.flush()
                                last = ident
                        next_query = time.monotonic() + .1
                    self.stop.wait(.005)
        except Exception as exc:
            self.telemetry_error = repr(exc)
            self.publish()

    def submit(self, record, data):
        self.received += 1
        self.last_seen_ns = time.time_ns()
        if time.monotonic() - self.last_publish >= 1:
            self.last_publish = time.monotonic()
            self.publish()
        # A rejected stream is quarantined; other streams keep consuming the ring.
        if self.error:
            self.rejected += 1
            return
        try:
            self.pipeline.submit(record, data)
            self.last = record['sequence']
        except Exception as exc:
            self.error = repr(exc)
            self.rejected += 1
            self.publish()

    def finish(self, **_):
        if self.summary is not None:
            return self.summary
        self.stop.set()
        if self.thread:
            self.thread.join(15)
            if self.thread.is_alive():
                raise RuntimeError('Telemetry control thread still active; retain pipeline')
        try:
            self.summary = self.pipeline.finish(tap_drain=self.service.pump if self.service else None)
            self.summary.update(dispatch=self.info())
            if self.service:
                time.sleep(.2)
                result = {key: self.service.query(key) for key in ('status', 'snapshot', 'history600', 'history3600')}
                (self.pipeline.out / 'telemetry-summary.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        finally:
            if self.service:
                self.service.close()
        self.publish()
        return self.summary


class Dispatcher:
    def __init__(self, out, dll, capacity=64, mode='individual', gain='passthrough', telemetry=False,
                 pipeline_factory=None, limit=16):
        self.out = pathlib.Path(out)
        self.dll, self.capacity, self.mode, self.gain = dll, capacity, mode, gain
        self.telemetry = telemetry
        self.limit = limit
        self.streams = {}
        self.current = {}
        self.mutex = threading.RLock()
        if pipeline_factory is None:
            spec = importlib.util.spec_from_file_location('multi_native', ROOT / 'tools/endpoint-probe/Native-Roar.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            pipeline_factory = module.NativePipeline
        self.factory = pipeline_factory
        self.publish()

    def publish(self):
        with self.mutex:
            rows = [s.info() for s in self.streams.values()]
            path = self.out / 'streams.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(rows, indent=2), encoding='utf-8')
            temporary.replace(path)
            # Backward-compatible discovery for the first available 480-frame route.
            available = next((s for s in rows if s['telemetry'] and not s['error'] and not s['telemetryError']), None)
            if available:
                (self.out / 'telemetry-service.json').write_text(json.dumps(available['telemetry'], indent=2), encoding='utf-8')

    def get(self, record):
        frames = record['frames']
        if not frames or len(frames) != record['count'] or any(n != frames[0] for n in frames):
            raise ValueError('Nonuniform stream frame counts')
        if not 1 <= frames[0] <= 4096:
            raise ValueError('Invalid stream frame count')
        block = record['block']
        with self.mutex:
            previous = self.current.get(block)
            # A frame-size change or sequence reset starts a new bounded epoch.
            if previous and previous.frames == frames[0] and (previous.last is None or record['sequence'] > previous.last):
                return previous
            if len(self.streams) >= self.limit:
                raise RuntimeError('Multi-stream epoch limit reached')
            directory = self.out / ('stream-' + str(len(self.streams)))
            directory.mkdir()
            pipeline = self.factory(directory, self.dll, capacity=self.capacity, mode=self.mode, gain=self.gain)
            pipeline.prepare(frames[0])
            stream = Stream(pipeline, block, frames[0], False, self.publish)
            self.streams[directory.name] = stream
            self.current[block] = stream
            if self.telemetry and frames[0] == 480:
                stream.thread = threading.Thread(target=stream._telemetry, name='stream-telemetry', daemon=True)
                stream.thread.start()
            self.publish()
            return stream
