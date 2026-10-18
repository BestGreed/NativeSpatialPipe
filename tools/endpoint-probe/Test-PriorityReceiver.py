"""Owned ring -> Rust metadata policy -> real Rust renderers. No audiodg hook."""
import ctypes as C
import importlib.util
import json
import pathlib
import struct
import tempfile
import time
import unittest
import sys
FULL="--full-timing" in sys.argv
if FULL:sys.argv.remove("--full-timing")

ROOT=pathlib.Path(__file__).resolve().parents[2]
HERE=pathlib.Path(__file__).parent
def load(name,file):
    spec=importlib.util.spec_from_file_location(name,HERE/file);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
native=load('priority_native','Native-Roar.py')
receiver=load('priority_receiver','Native-Receiver.py')
DLL=ROOT/'build/telemetry-objects/release/nsp_roar_bridge.dll'


class Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'work');self.pipelines=[]
        for i in range(4):
            out=pathlib.Path(self.temp.name)/str(i);out.mkdir()
            p=native.NativePipeline(out,DLL);p.prepare(480);self.pipelines.append(p)
        self.rx=receiver.Receiver(self.pipelines[0],priority_pipelines=self.pipelines[1:],timing=True,full_timing=FULL)
        self.rx.start();self.lib=self.rx.transport.lib
        self.lib.nsp_link_send.argtypes=[C.c_void_p,C.c_char_p,C.c_uint32,C.c_char_p,C.c_uint32];self.lib.nsp_link_send.restype=C.c_int
        self.writer=self.lib.nsp_link_open(self.rx.config['name'],self.rx.config['event'],0)
        self.assertTrue(self.writer);self.sent=0;self.clock=time.perf_counter_ns()-5_000_000_000
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.writer:self.lib.nsp_link_close(self.writer);self.writer=None
        self.rx.finish()
        for p in self.pipelines:p.finish()
        self.temp.cleanup()

    def send(self,block,seq,types=(2,),advance_ms=10,expect_error=False):
        self.clock+=int(advance_ms*1e6)
        record=dict(block=hex(block),sequence=seq,count=len(types),framesUniform=480,
                    captureQpcNs=self.clock,copyDoneQpcNs=self.clock,sendQpcNs=self.clock)
        properties=b''.join(struct.pack('<4I4f',slot+1,480,typ,4,1,0,-2,1) for slot,typ in enumerate(types))
        pcm=struct.pack('<480f',*([.01]*480))*len(types)
        raw=properties+pcm;header=json.dumps(record).encode()
        self.assertEqual(self.lib.nsp_link_send(self.writer,header,len(header),raw,len(raw)),0)
        self.sent+=1;deadline=time.monotonic()+3
        while self.rx.received<self.sent and not self.rx.errors and time.monotonic()<deadline:time.sleep(.001)
        if expect_error:self.assertTrue(self.rx.errors)
        else:self.assertEqual(self.rx.received,self.sent,self.rx.errors)

    def reports(self):
        result=self.rx.finish()
        self.assertTrue(result['passed'],result)
        self.assertEqual(result['receiverPcmCopies'],0)
        summaries=[p.finish() for p in self.pipelines]
        self.assertTrue(all(s['passed'] for s in summaries),[(s['error'],s['written']) for s in summaries])
        if FULL:
            for summary in summaries:self.assertEqual(summary['fullTiming']['captureToRenderMs']['count'],summary['written'])
        return result,summaries

    def test_telemetry_follows_static_stream_promoted_to_dynamic(self):
        self.assertIsNone(self.rx.telemetry_pipeline)
        self.send(0x111,0,types=(8192,))
        self.assertIsNone(self.rx.telemetry_pipeline)
        self.send(0x111,1,types=(1,))
        self.assertIs(self.rx.telemetry_pipeline,self.pipelines[1])
        self.send(0x222,0,types=(1,))
        self.assertIs(self.rx.telemetry_pipeline,self.pipelines[1],'concurrent dynamic stream must not flap the selected telemetry route')
        self.send(0x222,1,types=(1,),advance_ms=600)
        self.assertIs(self.rx.telemetry_pipeline,self.pipelines[1],'session primary remains stable across companion activity')
        self.reports()

    def test_promoted_pipeline_publishes_real_telemetry(self):
        sys.path.insert(0,str(ROOT/'tools/telemetry'))
        from client import TelemetryService
        self.send(0x111,0,types=(8192,))
        self.send(0x111,1,types=(1,))
        selected=self.rx.telemetry_pipeline
        self.assertIs(selected,self.pipelines[1])
        with TelemetryService(http_port=0) as service:
            service.attach(selected)
            for seq in range(2,32):
                self.send(0x111,seq,types=(1,))
                service.pump(selected);time.sleep(.005)
            deadline=time.monotonic()+3;status={}
            while time.monotonic()<deadline:
                service.pump(selected);status=service.query('status')
                if status.get('receivedBlocks',0)>0 and status.get('objectBlocks',0)>0:break
                time.sleep(.01)
            self.assertGreater(status.get('receivedBlocks',0),0,status)
            self.assertGreater(status.get('objectBlocks',0),0,status)
        self.reports()

    def test_static_audio_does_not_replace_dynamic_telemetry(self):
        self.send(0x111,0,types=(2,))
        self.assertIsNone(self.rx.telemetry_pipeline,'static-only frames do not establish an object telemetry route')
        self.send(0x222,0,types=(1,))
        self.assertIs(self.rx.telemetry_pipeline,self.pipelines[0],'dynamic route supersedes static fallback')
        self.send(0x111,1,types=(2,))
        self.assertIs(self.rx.telemetry_pipeline,self.pipelines[0])
        self.reports()

    def test_dynamic_companion_filters_nonzero_static_pcm(self):
        for seq in range(20):
            self.send(0x123,seq,(1,2))
            self.send(0x456,seq,(2,4)) # Nonzero PCM is deliberately discarded by policy.
        r,s=self.reports();routes={x['block']:x for x in r['streams']}
        self.assertEqual(routes['0x456']['filteredBlocks'],20)
        self.assertEqual(routes['0x123']['submittedBlocks'],20)
        self.assertEqual(sum(x['written'] for x in s),20)
        self.assertEqual(r['timing']['stages']['priorityDecision']['count'],40)
        self.assertEqual(r['timing']['stages']['nativeSubmit']['count'],20)

    def test_static_first_and_dynamic_exit_restore_without_sequence_gap(self):
        self.send(0x456,0)
        self.send(0x123,0,(1,))
        self.send(0x456,1)
        self.send(0x456,2,advance_ms=600)
        r,s=self.reports();routes={x['block']:x for x in r['streams']}
        self.assertEqual(routes['0x456']['filteredBlocks'],1)
        self.assertEqual(routes['0x456']['submittedBlocks'],2)
        self.assertEqual(sum(x['written'] for x in s),3)

    def test_previously_static_stream_promotes_immediately(self):
        self.send(0x123,0,(1,))
        self.send(0x456,0)
        self.send(0x456,1,(1,2))
        self.send(0x456,2,(2,)) # Ever-dynamic state persists for the route.
        r,s=self.reports();route=next(x for x in r['streams'] if x['block']=='0x456')
        self.assertTrue(route['everDynamic']);self.assertEqual(route['submittedBlocks'],2)
        self.assertEqual(route['filteredBlocks'],1)

    def test_single_static_and_two_dynamic_streams_are_preserved(self):
        self.send(0x456,0)
        self.send(0x456,1)
        self.send(0x123,0,(1,))
        self.send(0x789,0,(1,))
        r,s=self.reports();self.assertEqual(sum(x['submittedBlocks'] for x in r['streams']),4)
        self.assertEqual(sum(x['written'] for x in s),4)

    def test_extended_static_before_dynamic_does_not_poison_renderer(self):
        extended=tuple(1<<i for i in range(1,17))
        self.send(0x456,0,extended)
        self.send(0x123,0,(1,))
        self.send(0x456,1,extended)
        r,s=self.reports();route=next(x for x in r['streams'] if x['block']=='0x456')
        self.assertEqual(route['unsupportedBlocks'],1);self.assertEqual(route['filteredBlocks'],1)
        self.assertEqual(sum(x['written'] for x in s),1)

    def test_filtered_stream_still_checks_source_sequence(self):
        self.send(0x123,0,(1,));self.send(0x456,0)
        self.send(0x456,2,expect_error=True)
        self.assertFalse(self.rx.finish()['passed'])


if __name__=='__main__':unittest.main()
