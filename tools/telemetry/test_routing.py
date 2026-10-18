import tempfile
import unittest
from pathlib import Path
from routing import TelemetryRoutes
ROOT=Path(__file__).resolve().parents[2]


class RoutesTests(unittest.TestCase):
    def setUp(self):
        self.now=0.;self.sizes={};self.pipelines=[object() for _ in range(4)]
        self.sizes=dict.fromkeys(self.pipelines,0)
        self.r=TelemetryRoutes(clock=lambda:self.now,size_reader=lambda p:self.sizes[p])
        self.assertIsNone(self.r.select(self.pipelines))

    def poll(self, seconds=.11, **growth):
        self.now+=seconds
        for index,amount in growth.items():self.sizes[self.pipelines[int(index)]]+=amount
        return self.r.select(self.pipelines)

    def test_zero_is_not_assumed_and_static_output_can_be_selected(self):
        self.assertIs(self.poll(**{'2':100}),self.pipelines[2])
        self.assertEqual(self.r.status()['selectedPipeline'],2)

    def test_current_stops_then_another_route_takes_over(self):
        self.assertIs(self.poll(**{'0':100}),self.pipelines[0])
        self.assertIs(self.poll(1.1,**{'1':100}),self.pipelines[1])
        self.assertIsNone(self.poll(1.1))

    def test_newer_active_route_preempts_only_after_second_progress(self):
        self.poll(**{'0':100})
        self.assertIs(self.poll(**{'0':100,'2':100}),self.pipelines[0])
        self.assertIs(self.poll(**{'0':100,'2':100}),self.pipelines[2])
        for _ in range(20):
            self.assertIs(self.poll(**{'0':100}),self.pipelines[2])
            self.assertIs(self.poll(**{'2':100}),self.pipelines[2])

    def test_resumed_old_route_is_new_activity(self):
        self.poll(**{'0':100});self.poll(1.1,**{'1':100})
        self.assertIs(self.poll(**{'0':100,'1':100}),self.pipelines[1])
        self.assertIs(self.poll(**{'0':100,'1':100}),self.pipelines[0])

    def test_existing_nonempty_file_is_not_evidence_of_live_output(self):
        self.sizes[self.pipelines[0]]=10000
        other=TelemetryRoutes(clock=lambda:self.now,size_reader=lambda p:self.sizes[p])
        self.assertIsNone(other.select(self.pipelines))
        self.now+=.11;self.assertIsNone(other.select(self.pipelines))

    def test_polling_is_bounded_and_no_pcm_is_read(self):
        calls=[]
        r=TelemetryRoutes(clock=lambda:self.now,size_reader=lambda p:(calls.append(p) or 0))
        r.select(self.pipelines)
        for _ in range(1000):r.select(self.pipelines)
        self.assertEqual(len(calls),4)
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as tmp:
            class Pipeline:
                out=Path(tmp)
            p=Pipeline();path=p.out/'roar-714.f32';path.write_bytes(b'a'*20)
            disk=TelemetryRoutes(clock=lambda:self.now)
            self.assertIsNone(disk.select([p]))
            with path.open('ab') as f:f.write(b'b'*20)
            self.now+=.11;self.assertIs(disk.select([p]),p)
            disk.close()
            self.assertEqual(path.read_bytes(),b'a'*20+b'b'*20)

    def test_error_excludes_route_without_touching_other_pipelines(self):
        self.poll(**{'0':100})
        def fail(p):
            if p is self.pipelines[0]:raise OSError('read unavailable')
            return self.sizes[p]
        self.r.size_reader=fail
        self.assertIs(self.poll(**{'2':100}),self.pipelines[2])


class EpochTests(unittest.TestCase):
    def test_switch_between_independent_native_pipelines_accepts_sequence_restart(self):
        # Isolated renderers and random-port service; no Hook or audio device.
        from test_service import native,DLL,TelemetryService,audio_packet
        import time
        with tempfile.TemporaryDirectory(dir=ROOT/'build') as tmp:
            a=Path(tmp)/'a';b=Path(tmp)/'b';a.mkdir();b.mkdir()
            pipelines=[native.NativePipeline(a,DLL),native.NativePipeline(b,DLL)]
            for p in pipelines:p.prepare(480)
            try:
                with TelemetryService() as svc:
                    previous=None
                    for step,(p,start) in enumerate(((pipelines[0],0),(pipelines[1],0),(pipelines[0],12))):
                        if previous is not None:previous.set_telemetry(False)
                        svc.attach(p)
                        for seq in range(start,start+12):
                            p.submit(*audio_packet(seq));time.sleep(.004);svc.pump(p)
                        deadline=time.monotonic()+3
                        while svc.query('status')['receivedBlocks']<(step+1)*12 and time.monotonic()<deadline:
                            svc.pump(p);time.sleep(.01)
                        self.assertEqual(svc.query('status')['receivedBlocks'],(step+1)*12)
                        time.sleep(.15);snapshot=svc.query('snapshot')
                        self.assertEqual(snapshot['streamEpoch'],step*2+1)
                        self.assertGreaterEqual(snapshot['sourceSequenceRange']['start'],start)
                        previous=p
                    self.assertEqual(svc.query('status')['reorderedBlocks'],0)
                    self.assertEqual(svc.query('status')['invalidBlocks'],0)
            finally:
                for p in pipelines:self.assertTrue(p.finish()['passed'])


if __name__=='__main__':unittest.main()
