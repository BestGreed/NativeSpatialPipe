import copy
import math
import struct
import unittest
from engine import Engine, HEADER, contract


def block(seq=0,epoch=1,value=.5):
    return HEADER.pack(b'NSPT',1,12,480,48000,epoch,seq)+struct.pack('<5760f',*([value]*5760))


class Tests(unittest.TestCase):
    def setUp(self):
        self.now=0.
        self.e=Engine(clock=lambda:self.now)
    def test_silence_unknown_input_and_default_history(self):
        history=self.e.history()
        self.assertIsNone(history['actualRangeUs'])
        self.assertIsNone(history['channels'][0]['samplePeak'])
        self.e.ingest(block(value=0.)); self.now=.11; self.e.flush()
        s=contract.validate(self.e.latest)
        self.assertEqual(s['taps']['decoded-input']['quality'],'disconnected')
        self.assertEqual(s['taps']['decoded-input']['coverage']['validSamples'],0)
        self.assertTrue(s['taps']['render-ready']['channels'][0]['meter']['silent'])
        self.assertEqual(self.e.history()['coverage']['validSamples'],480)
        self.assertAlmostEqual(self.e.history()['coverage']['ratio'],.01/600)
    def test_sine_and_overload(self):
        samples=[math.sin(i*math.tau/48) for i in range(480)]*12
        raw=HEADER.pack(b'NSPT',1,12,480,48000,1,0)+struct.pack('<5760f',*samples)
        self.e.ingest(raw); self.e.flush(True)
        meter=self.e.latest['taps']['render-ready']['channels'][0]['meter']
        self.assertAlmostEqual(meter['rmsDbfs'],-3.0103,places=4)
        self.e.ingest(block(1,value=2.)); self.e.flush(True)
        self.assertTrue(self.e.latest['taps']['render-ready']['channels'][0]['meter']['overFullScale'])
    def test_gap_nonfinite_reorder_and_no_double_count(self):
        self.e.ingest(block()); self.e.ingest(block(2,value=float('nan')))
        self.e.ingest(block(3)); self.assertFalse(self.e.ingest(block(3)))
        self.e.flush(True)
        s=self.e.latest
        self.assertEqual(s['taps']['render-ready']['coverage']['validSamples'],960)
        self.assertEqual(s['taps']['render-ready']['droppedBlocks'],2)
        self.assertEqual(s['taps']['render-ready']['quality'],'gap')
        first=self.e.history()
        self.assertEqual(first,self.e.history())
        self.assertEqual(first['coverage']['validSamples'],960)
        self.assertEqual(self.e.invalid,1)
    def test_epoch_segmentation_and_monotonic_stream(self):
        self.e.ingest(block(100)); self.e.flush(True); first=copy.deepcopy(self.e.latest)
        self.now=1.
        self.e.ingest(block(0,3,value=1.)); self.e.flush(True)
        contract.validate_stream([first,self.e.latest])
        self.assertEqual(self.e.history()['coverage']['validSamples'],480)
        self.assertEqual(self.e.history()['channels'][0]['sumSquares'],480)
        self.assertFalse(self.e.ingest(block(102,1)))
    def test_history_capacity_boundary_and_linear_energy(self):
        for second in range(3601):
            self.e.ingest(block(second*100))
        self.assertEqual(len(self.e.buckets),3600)
        h=self.e.history(3600)
        self.assertEqual(h['coverage']['validSamples'],3600*480)
        self.assertAlmostEqual(h['channels'][0]['meanSquare'],.25)
        self.assertAlmostEqual(h['channels'][0]['contribution'],1/12)
        self.assertLessEqual(self.e.history(600)['coverage']['validSamples'],600*48000)
    def test_stale_invalid_queries_and_versions(self):
        self.e.ingest(block()); self.now=.3
        self.assertEqual(self.e.status()['status'],'stale')
        for window in (1,True,601):
            with self.assertRaises(ValueError):self.e.history(window)
        with self.assertRaises(ValueError):self.e.history(band='20-120')
        contract.validate(self.e.capabilities()); contract.validate(self.e.layout())
        bad=self.e.capabilities(); bad['schemaVersion']='0.1.0'
        with self.assertRaises(ValueError):contract.validate(bad)
        self.assertFalse(self.e.ingest(b'bad'))


if __name__=='__main__': unittest.main()
