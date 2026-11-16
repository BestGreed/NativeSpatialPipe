import math
import struct
import unittest
from engine import HEADER,contract
from soundscape import SoundscapeEngine,ROW
from transport import Reassembler,fragments
from test_engine import block


def objects(seq=0,epoch=1,count=1,gen=1,flags=4,value=.25,ident=17,valid=1):
    header=HEADER.pack(b'NSPO',1,count,480,48000,epoch,seq)
    return header+b''.join(ROW.pack(slot,ident+slot,1,flags,gen,2.+slot,3.,-4.,valid,.5,1)+struct.pack('<480f',*([value]*480)) for slot in range(count))


class Tests(unittest.TestCase):
    def setUp(self):self.e=SoundscapeEngine(clock=lambda:0.)
    def feed(self,seq=0,**kwargs):
        self.e.ingest(block(seq,epoch=kwargs.get('epoch',1)))
        self.e.ingest_objects(objects(seq,**kwargs))
    def test_measured_positions_meter_gain_and_capability(self):
        self.feed();self.e.flush(True)
        s=contract.validate(self.e.latest);tap=s['taps']['decoded-input'];row=tap['objects'][0]
        self.assertTrue(tap['snapshotComplete']);self.assertEqual(tap['gainMode'],'applied')
        self.assertEqual(row['meter']['samplePeak'],.25)
        self.assertEqual(row['spatial']['position'],[2.,3.,-4.])
        self.assertAlmostEqual(row['spatial']['rendererPosition'][1],4/math.sqrt(29))
        self.assertEqual(row['objectKey']['generation'],1)
        self.assertTrue(self.e.capabilities()['routes'][0]['soundNavigation'])
    def test_eos_reuse_and_gap_do_not_join_lifecycles(self):
        self.feed(flags=12);self.e.flush(True);first=self.e.latest
        self.assertEqual(first['taps']['decoded-input']['objects'],[])
        self.assertEqual({e['kind'] for e in first['events']},{'onset','ended'})
        self.feed(1,gen=2,ident=19);self.e.flush(True);second=self.e.latest
        contract.validate_stream([first,second])
        self.assertEqual(second['taps']['decoded-input']['objects'][0]['objectKey']['generation'],2)
        self.e.ingest(block(2));self.e.ingest(block(3));self.e.ingest_objects(objects(3,gen=2,ident=19))
        self.e.flush(True)
        self.assertEqual(self.e.latest['taps']['decoded-input']['quality'],'gap')
        self.assertFalse(self.e.latest['taps']['decoded-input']['snapshotComplete'])
        self.assertEqual(self.e.latest['taps']['decoded-input']['objects'][0]['activeDurationUs'],10000)
    def test_full_capacity_fragmentation_and_memory_bounds(self):
        raw=objects(count=160);parts=list(fragments(raw,1));self.assertEqual(len(parts),7)
        r=Reassembler();result=None
        for part in reversed(parts):result=r.add(part) or result
        self.assertEqual(result,raw)
        self.e.ingest(block());self.assertTrue(self.e.ingest_objects(result));self.e.flush(True)
        self.assertEqual(len(self.e.latest['taps']['decoded-input']['objects']),160)
        for seq in range(1,50):self.e.ingest_objects(objects(seq))
        self.assertEqual(len(self.e.object_frames),32)
        for ident in range(30):r.add(list(fragments(raw,ident+10))[0])
        self.assertEqual(len(r.frames),16)
    def test_invalid_identity_unknown_position_and_expiry(self):
        self.feed(valid=0);self.e.flush(True)
        self.assertIsNone(self.e.latest['taps']['decoded-input']['objects'][0]['spatial']['position'])
        self.assertFalse(self.e.ingest_objects(objects(1,ident=99)))
        self.assertFalse(self.e.ingest_objects(objects(1,value=float('nan'))))
        now=[0.];r=Reassembler(clock=lambda:now[0]);raw=objects(count=160)
        r.add(next(fragments(raw,1)));now[0]=.3;r.expire();self.assertEqual(len(r.frames),0)
    def test_short_event_capacity_reports_overflow(self):
        self.e.ingest(block());self.e.ingest_objects(objects(count=160,flags=12));self.e.flush(True)
        message=contract.validate(self.e.latest)
        self.assertEqual(len(message['events']),256)
        self.assertEqual(message['eventOverflow'],64)
        self.assertEqual(message['taps']['decoded-input']['objects'],[])


if __name__=='__main__':unittest.main()
