"""Prepare a traced, band-limited derivative of the official AES Music-Noise.

No playback. Filtering/outer fades mean this is not an AES75-compliant test.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import wave
import numpy as np

OFFICIAL_SHA1='46672df0844964f470f1f8eeea1a35099257a9d0'
CENTRES=[125,250,500,1000,2000,4000,8000,16000]
EDGES=[125/2**.5,16000*2**.5]


def validate_metadata(m):
    if m.get('source_sha1')!=OFFICIAL_SHA1 or m.get('aes75_compliant') is not False:
        raise ValueError('Music-Noise requires traced official source and derivative declaration')
    if m.get('rate')!=48000 or m.get('channels')!=2 or m.get('analysis_centres_hz')!=CENTRES:
        raise ValueError('Unsupported Music-Noise layout or analysis bands')
    if not np.allclose(m.get('band_hz',[]),EDGES):raise ValueError('Music-Noise band metadata disagrees')
    if not math.isfinite(m['seconds']) or not .1<=m['seconds']<=600:raise ValueError('Duration must be .1..600 seconds')
    if not math.isfinite(m['peak_dbfs']) or not -80<=m['peak_dbfs']<=-12:raise ValueError('Peak must be -80..-12 dBFS')


def bandlimit(mono,rate=48000):
    f=np.fft.rfftfreq(len(mono),1/rate);s=np.fft.rfft(mono)
    s[(f<EDGES[0])|(f>EDGES[1])]=0
    return np.fft.irfft(s,n=len(mono))


def prepare(source,out,seconds=600,peak_dbfs=-18,route='LR'):
    source=Path(source);raw=source.read_bytes()
    if hashlib.sha1(raw).hexdigest()!=OFFICIAL_SHA1:raise ValueError('Official Music-Noise checksum mismatch')
    with wave.open(str(source),'rb') as w:
        if (w.getnchannels(),w.getsampwidth(),w.getframerate())!=(1,2,48000):raise ValueError('Expected official mono PCM16 48kHz')
        mono=np.frombuffer(w.readframes(w.getnframes()),'<i2').astype(float)/32768
    gains={'L':[1.,0.],'R':[0.,1.],'LR':[1.,1.]}[route]
    m=dict(schema=1,kind='music_noise_bandlimited',rate=48000,channels=2,seconds=seconds,
        peak_dbfs=peak_dbfs,analysis_centres_hz=CENTRES,band_hz=EDGES,source_sha1=OFFICIAL_SHA1,
        source_sha256=hashlib.sha256(raw).hexdigest(),source_url='https://aes.org/standards/AES75/',
        source_file=str(source.resolve()),source_frames=len(mono),aes75_compliant=False,
        modifications=['circular FFT bandlimit 88.388..22627.417Hz; suppress unused sub-125-octave energy',
            'fixed gain, stereo routing, loop official period, 50ms outer fades only'],
        physical_channel_mapping_verified=False,channel_gains=gains,all_channels_identical=route=='LR',
        status='prepared_not_played',center_hz=None)
    validate_metadata(m)
    mono=bandlimit(mono);mono=np.resize(mono,round(seconds*48000))
    ramp=min(2400,len(mono)//4);fade=np.sin(np.linspace(0,np.pi/2,ramp))**2
    mono[:ramp]*=fade;mono[-ramp:]*=fade[::-1]
    mono*=10**(peak_dbfs/20)/np.max(np.abs(mono))
    samples=(mono[:,None]*gains).astype('<f4')
    m.update(frames=len(samples),seconds=len(samples)/48000,fade_seconds=ramp/48000,
        rms_dbfs=float(20*np.log10(np.sqrt(np.mean(mono**2)))),
        crest_factor_db=float(20*np.log10(np.max(np.abs(mono))/np.sqrt(np.mean(mono**2)))))
    out=Path(out);out.mkdir(parents=True,exist_ok=False);np.save(out/'reference.npy',samples)
    with (out/'reference.npy').open('rb') as f:m['sha256']=hashlib.file_digest(f,'sha256').hexdigest()
    (out/'reference.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps(m,indent=2))
    return m


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('source');p.add_argument('output')
    p.add_argument('--seconds',type=float,default=600);p.add_argument('--peak-dbfs',type=float,default=-18)
    p.add_argument('--route',choices=['L','R','LR'],default='LR');a=p.parse_args()
    prepare(a.source,a.output,a.seconds,a.peak_dbfs,a.route)
