import copy
import math
import random
import struct
import unittest
from metering import Metering
from engine import HEADER
from soundscape import SoundscapeEngine, ROW, STRIDE
from test_engine import block
from test_soundscape import objects


class MeterTests(unittest.TestCase):
    def test_native_matches_reference_random_extremes_and_silence(self):
        native,reference=Metering('native'),Metering('python')
        rng=random.Random(491)
        rows=[]
        for index in range(160):
            values=[0.]*480 if index==0 else [rng.choice((-1.,1.))*2**rng.randint(-120,120)*rng.random() for _ in range(480)]
            rows.append(b'padding!'+struct.pack('<480f',*values))
        raw=b'header'+b''.join(rows)
        actual=native.rows(raw,14,1928,160);expected=reference.rows(raw,14,1928,160)
        for (energy,peak),(expected_energy,expected_peak) in zip(actual,expected):
            self.assertTrue(math.isclose(energy,expected_energy,rel_tol=1e-14,abs_tol=0.))
            self.assertEqual(peak,expected_peak)

    def test_reject_nonfinite_in_every_row_position(self):
        meter=Metering('native')
        for value in (float('nan'),float('inf'),-float('inf')):
            for sample in (0,479,480,480*159+479):
                raw=bytearray(160*480*4)
                struct.pack_into('<f',raw,sample*4,value)
                self.assertIsNone(meter.rows(raw,0,1920,160))

    def test_native_bounds_and_buffer_types(self):
        meter=Metering('native')
        for first,stride,count in ((-1,1920,1),(0,1,2),(0,1920,161),(0,1920,0),(1,1920,1)):
            with self.assertRaises(ValueError):meter.rows(bytes(1920),first,stride,count)
        self.assertEqual(meter.rows(memoryview(bytes(1920)),0,1920,1),[(0.,0.)])

    def test_complete_stream_snapshot_history_and_events_match(self):
        now=[0.]
        native=SoundscapeEngine(clock=lambda:now[0],meter_backend='native')
        reference=SoundscapeEngine(clock=lambda:now[0],meter_backend='python')
        reference.session=native.session
        rng=random.Random(95)
        for seq in range(40):
            now[0]=seq*.01
            epoch=1 if seq<25 else 3
            output=block(seq,epoch=epoch,value=.125)
            flags=12 if seq==14 else 5 if seq==15 else 4
            raw=bytearray(objects(seq,epoch=epoch,count=140,gen=2 if seq>=15 else 1,flags=flags))
            for slot in range(140):
                values=[rng.uniform(-2.,2.) for _ in range(480)]
                struct.pack_into('<480f',raw,32+slot*STRIDE+ROW.size,*values)
            if seq in (8,29):struct.pack_into('<f',raw,32+ROW.size,float('nan'))
            for engine in (native,reference):
                engine.ingest(output);engine.ingest_objects(bytes(raw));engine.flush()
            if native.latest is not None:
                # Python 3.12 compensated sum and native compensated sum agree
                # to roundoff; other semantic fields must match exactly.
                self.compare(native.latest,reference.latest)
        native.flush(True);reference.flush(True)
        self.compare(native.latest,reference.latest)
        self.compare(native.history(),reference.history())
        self.assertEqual(native.object_invalid,reference.object_invalid)

    def compare(self,left,right):
        if isinstance(left,dict):
            self.assertEqual(left.keys(),right.keys())
            for key in left:self.compare(left[key],right[key])
        elif isinstance(left,list):
            self.assertEqual(len(left),len(right))
            for a,b in zip(left,right):self.compare(a,b)
        elif type(left) is float:
            self.assertTrue(math.isclose(left,right,rel_tol=1e-13,abs_tol=1e-13),(left,right))
        else:self.assertEqual(left,right)


if __name__=='__main__':unittest.main()
