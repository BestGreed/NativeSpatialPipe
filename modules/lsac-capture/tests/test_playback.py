import ctypes as C
import math
from pathlib import Path
import struct
import threading
import tempfile
import json
import unittest
from unittest.mock import patch, MagicMock
import uuid

import numpy as np

from lsac_capture.playback import (parse_format,third_octave_edges,reference_samples,
                                   encode_samples,play,_render,PCM_GUID,prepare_reference,_load_reference)


def wave_format(channels=2,rate=48000,bits=32,valid=24,mask=3):
    align=channels*bits//8
    return struct.pack("<HHIIHHHHI",0xfffe,channels,rate,rate*align,align,bits,22,valid,mask)+uuid.UUID(PCM_GUID).bytes_le


class PlaybackTests(unittest.TestCase):
    def test_fixed_route_keeps_muted_channel_exactly_silent_and_refuses_boost(self):
        a=reference_samples(seconds=.1,peak_dbfs=-12,channel_gains=[1,0])
        self.assertTrue(np.all(a[:,1]==0))
        self.assertGreater(float(np.max(abs(a[:,0]))),.25)
        for gains in ([0,0],[1,2],[-1,0],[1],[1,float('nan')]):
            with self.assertRaises(ValueError):reference_samples(seconds=.1,channel_gains=gains)

    def test_saved_fixed_route_is_verified_before_native_render(self):
        test_root=Path(__file__).resolve().parents[3]/'build'/'lsac'/'test-tmp'
        test_root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=test_root) as temp:
            folder=Path(temp)/'left'
            prepare_reference(folder,seconds=.1,peak_dbfs=-12,channel_gains=[1,0])
            metadata,fmt,raw=_load_reference(folder,dict(format_hex=wave_format().hex()))
            self.assertEqual(metadata['channel_gains'],[1,0])
            self.assertFalse(metadata['all_channels_identical'])
            self.assertTrue(np.all(np.frombuffer(raw,dtype='<i4').reshape(-1,2)[:,1]==0))
            path=folder/'reference.json';metadata['channel_gains']=[0,1]
            path.write_text(json.dumps(metadata),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'fixed channel gains'):_load_reference(folder,dict(format_hex=wave_format().hex()))

    def test_exact_third_octave_edges(self):
        low,high=third_octave_edges()
        self.assertAlmostEqual(low,445.44935907016963)
        self.assertAlmostEqual(high,561.2310241546866)
        self.assertAlmostEqual(math.log2(high/low),1/3)

    def test_seed_channels_level_and_fades(self):
        a=reference_samples(seconds=1,channels=4)
        b=reference_samples(seconds=1,channels=4)
        self.assertTrue(np.array_equal(a,b))
        self.assertTrue(np.array_equal(a[:,0],a[:,3]))
        self.assertAlmostEqual(float(np.max(np.abs(a))),.001,places=9)
        self.assertTrue(np.all(a[[0,-1]]==0))
        self.assertFalse(np.array_equal(a,reference_samples(seconds=1,channels=4,seed=1)))

    def test_energy_is_in_selected_band_except_documented_fade_skirts(self):
        a=reference_samples(seconds=2,channels=1)[:,0]
        energy=np.abs(np.fft.rfft(a))**2
        frequencies=np.fft.rfftfreq(len(a),1/48000)
        # A genuine band check, not an exact FFT-zero test of the implementation.
        fraction=energy[(frequencies>=440)&(frequencies<=567)].sum()/energy.sum()
        self.assertGreater(fraction,.99)

    def test_invalid_pilot_bounds(self):
        for kwargs in (dict(peak_dbfs=-11),dict(seconds=601),dict(seconds=float("nan")),
                       dict(channels=0),dict(rate=100),dict(center_hz=24000),dict(seed=-1)):
            with self.assertRaises(ValueError): reference_samples(**kwargs)

    def test_long_reference_keeps_period_boundaries_unfaded(self):
        a=reference_samples(rate=8000,channels=2,seconds=121,peak_dbfs=-18,band_octaves=1)
        period=8000*60
        self.assertEqual(a.shape,(8000*121,2))
        np.testing.assert_array_equal(a[1000:period-1000],a[period+1000:2*period-1000])
        self.assertGreater(float(np.max(np.abs(a[period-400:period+400]))),.02)
        self.assertTrue(np.all(a[[0,-1]]==0))
        self.assertAlmostEqual(float(np.max(abs(a))),10**(-18/20),places=7)

    def test_full_octave_band_is_distinct_from_third_octave(self):
        from lsac_capture.playback import band_edges
        self.assertAlmostEqual(band_edges(500,1)[0],353.5533905932738)
        self.assertAlmostEqual(band_edges(16000,1)[1],22627.41699796952)
        with self.assertRaises(ValueError):reference_samples(seconds=.1,center_hz=17000,band_octaves=1)

    def test_prepared_playback_never_reloads_or_encodes_on_live_render_path(self):
        from lsac_capture.playback import prepare_playback_reference
        root=Path(__file__).resolve().parents[3]/'build'/'lsac'/'test-tmp';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            folder=Path(tmp)/'reference';prepare_reference(folder,seconds=.1)
            plan=dict(flow='render',endpoint_id='reviewed',format_hex=wave_format().hex(),formats=[dict(mode='shared',exact=True)])
            prepared=prepare_playback_reference(plan,folder)
            def render(*args):args[-1]['status']='cancelled'
            with patch('lsac_capture.playback._load_reference',side_effect=AssertionError('live-path reload')), \
                 patch('lsac_capture.playback._render',side_effect=render), \
                 patch('lsac_capture.scheduling.CaptureMMCSS'), \
                 patch('lsac_capture.playback.qpf',return_value=1000),patch('lsac_capture.playback.qpc',return_value=1000):
                result=play(plan,folder,Path(tmp)/'output',confirm_playback=True,prepared_reference=prepared)
                self.assertEqual(result['status'],'cancelled')
                wrong=dict(plan,format_hex='bad')
                with self.assertRaisesRegex(ValueError,'match route/source'):
                    play(wrong,folder,Path(tmp)/'wrong',confirm_playback=True,prepared_reference=prepared)

    def test_operator_requested_minus_18_dbfs_peak(self):
        a=reference_samples(seconds=1,peak_dbfs=-18)
        self.assertAlmostEqual(float(np.max(np.abs(a))),10**(-18/20),places=7)

    def test_extensible_channel_mask_and_valid_bits_preserved(self):
        fmt=parse_format(wave_format(channels=6,mask=0x3f))
        self.assertEqual(fmt["channels"],6)
        self.assertEqual(fmt["channel_mask"],0x3f)
        self.assertEqual(fmt["valid_bits"],24)
        self.assertEqual(fmt["encoding"],"PCM")

    def test_bad_wave_formats_refused(self):
        for raw in (b"bad",wave_format()[:-1],wave_format(bits=24,valid=32),wave_format(channels=0)):
            with self.assertRaises(ValueError): parse_format(raw)

    def test_integer_valid_bits_are_left_aligned(self):
        values=np.array([[1.,-1.],[.5,-.5]],dtype="<f4")
        fmt=parse_format(wave_format())
        encoded=np.frombuffer(encode_samples(values,fmt),dtype="<i4")
        self.assertEqual(encoded.tolist(),[2147483392,-2147483392,1073741824,-1073741824])
        self.assertTrue(np.all((encoded&255)==0))

    def test_packed24_and_float32_encoding(self):
        fmt=parse_format(wave_format(bits=24,valid=24))
        self.assertEqual(encode_samples(np.array([[1.,-1.]]),fmt),bytes.fromhex("ffff7f010080"))
        raw=struct.pack("<HHIIHHH",3,2,48000,384000,8,32,0)
        fmt=parse_format(raw)
        self.assertEqual(encode_samples(np.array([[.25,-.25]]),fmt),struct.pack("<ff",.25,-.25))

    def test_unconfirmed_playback_cannot_read_files_or_activate_native(self):
        with patch("lsac_capture.playback._load_reference") as load,patch("lsac_capture.playback.COM") as native:
            with self.assertRaises(ValueError): play({},"missing","must-not-create")
            load.assert_not_called(); native.assert_not_called()

    def test_capture_route_and_unsupported_mode_refused_before_reference_io(self):
        with patch("lsac_capture.playback._load_reference") as load:
            for plan in (dict(endpoint_id="capture",flow="capture"),
                         dict(endpoint_id="render",flow="render",formats=[])):
                with self.assertRaises(ValueError): play(plan,"missing","must-not-create",confirm_playback=True)
            load.assert_not_called()

    def test_render_preserves_queue_order_and_drains_after_last_frame(self):
        fmt=parse_format(wave_format())
        raw=encode_samples(np.repeat(np.arange(6,dtype=float)[:,None]/10,2,axis=1),fmt)
        plan=dict(endpoint_id="chosen",format_hex=wave_format().hex())
        buffers=[]; calls=[]; paddings=iter([2,0]); events=[]
        dev,client,writer=C.c_void_p(10),C.c_void_p(20),C.c_void_p(30)
        def invoke(obj,index,types=(),args=()):
            calls.append((obj.value,index))
            if obj.value==20:
                if index==9: args[0]._obj.value=100000; args[1]._obj.value=100000
                if index==4: args[0]._obj.value=4
                if index==5: args[0]._obj.value=100000
                if index==14: args[1]._obj.value=writer.value
                if index==6: args[0]._obj.value=next(paddings)
            elif obj.value==30 and index==3:
                buffer=C.create_string_buffer(args[0]*fmt["align"]); buffers.append(buffer)
                args[1]._obj.value=C.addressof(buffer)
            return 0
        result={}
        with patch("lsac_capture.playback.COM") as com, \
             patch("lsac_capture.playback._activate",return_value=(dev,client,{})), \
             patch("lsac_capture.playback.method",return_value=lambda *args:0), \
             patch("lsac_capture.playback.invoke",side_effect=invoke), \
             patch("lsac_capture.playback.release"), \
             patch("lsac_capture.playback.qpc",return_value=100), \
             patch("lsac_capture.playback.time.sleep"), \
             patch("pathlib.Path.exists",return_value=False):
            _render(plan,fmt,raw,dict(frames=6,seconds=.1),Path("unused"),"shared",threading.Event(),
                    lambda event:events.append(event),result)
        self.assertEqual(b"".join(b.raw for b in buffers),raw)
        self.assertEqual([e["sample_start_i64"] for e in events if e["kind"]=="playback_queued"],[0,4])
        self.assertEqual(result["queued_frames"],6)
        self.assertEqual(result["status"],"completed_not_acoustically_verified")
        self.assertIn((20,10),calls); self.assertIn((20,11),calls)


if __name__=="__main__": unittest.main()
