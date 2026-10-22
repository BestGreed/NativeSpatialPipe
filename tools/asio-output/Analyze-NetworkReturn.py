"""Align actual OLR PCM against the configured external DVS 25..36 return."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).absolute().parents[2]/'work/consumer-deps'))
import numpy as np

root=Path(sys.argv[1])
meta=json.loads((root/'return.json').read_text())
playback=json.loads((root/'playback.json').read_text())
reference_file='sent-1-12.f32' if (root/'sent-1-12.f32').exists() else 'renderer.f32'
x=np.fromfile(root/reference_file,dtype='<f4').reshape(-1,12).astype(np.float64)
y=np.fromfile(root/'return-25-36.f32',dtype='<f4').reshape(-1,12).astype(np.float64)
if len(y)!=meta['frames'] or meta['sampleRate']!=48000 or meta['overflowCallbacks']:
    raise RuntimeError('Invalid/incomplete return capture')
# Restrict alignment search to +/-1 second and use all distinct tone slots.
lags=np.arange(1-len(x),len(y))
window=np.abs(lags)<=48000
score=np.zeros(np.count_nonzero(window))
fft_size=1<<(len(x)+len(y)-2).bit_length()
for ch in range(12):
    spectrum=np.fft.rfft(y[:,ch],fft_size)*np.fft.rfft(x[::-1,ch],fft_size)
    score+=np.fft.irfft(spectrum,fft_size)[:len(lags)][window]
lag=int(lags[window][np.argmax(score)])
xs=max(0,-lag);ys=max(0,lag)
count=min(len(x)-xs,len(y)-ys)
xx=x[xs:xs+count];yy=y[ys:ys+count]
# Exact error uses unmodified decoded PCM; mean removal below is only for r.
peak_error=np.max(np.abs(xx-yy),axis=0)
exact_fraction=np.mean(xx==yy,axis=0)
xx=xx-xx.mean(axis=0);yy=yy-yy.mean(axis=0)
xn=np.linalg.norm(xx,axis=0);yn=np.linalg.norm(yy,axis=0)
matrix=(xx.T@yy)/np.maximum(xn[:,None]*yn[None,:],1e-30)
rows=[]
for ch in range(12):
    reference=xx[:,ch];received=yy[:,ch]
    gain=float(reference@received/max(float(reference@reference),1e-30))
    residual=received-gain*reference
    signal_energy=float(received@received)
    residual_energy=float(residual@residual)
    snr=float(10*np.log10(max(signal_energy,1e-30)/max(residual_energy,1e-30)))
    others=np.delete(matrix[ch],ch)
    rows.append(dict(output=ch+1,input=ch+25,correlation=float(matrix[ch,ch]),
                     bestInput=int(np.argmax(np.abs(matrix[ch])))+25,gain=gain,
                     peakAbsoluteError=float(peak_error[ch]),exactSampleFraction=float(exact_fraction[ch]),
                     residualSnrDb=snr,maxOtherChannelCorrelation=float(np.max(np.abs(others)))))
passed=bool(playback['passed'] and all(r['bestInput']==r['input'] and
            r['correlation']>0.99 and r['gain']>0 and r['maxOtherChannelCorrelation']<0.05 for r in rows))
result=dict(passed=passed,scope='Configured network digital return; not microphone/acoustic or AVP decoding comparison',
            referenceFile=reference_file,
            lagFrames=lag,lagMilliseconds=lag/48,alignedFrames=count,
            alignment='One common integer-sample lag, no gain normalization or per-channel lag adjustment',
            latencyMeaning='Reference file to received ASIO callback sample index; callback-output reference isolates network return from output SRC; not calibrated DAC/acoustic latency',
            rows=rows,correlationMatrix=matrix.tolist())
# Independently validate OLR semantic -> actual physical output with the known
# sequential tone slots. Energy envelopes avoid treating SRC phase as routing.
olr=np.fromfile(root/'renderer.f32',dtype='<f4').reshape(-1,12).astype(np.float64)
sent=np.fromfile(root/'sent-1-12.f32',dtype='<f4').reshape(-1,12).astype(np.float64) if (root/'sent-1-12.f32').exists() else None
order=playback.get('outputOrder','olr')
expected=[0,2,1,5,4,7,6,3,10,8,9,11] if order=='dvs-speakers' else list(range(12))
labels=['L','C','R','Rs','Ls','Rrs','Lrs','LFE','Ltr','Ltf','Rtf','Rtr'] if order=='dvs-speakers' else ['L','R','C','LFE','Ls','Rs','Lrs','Rrs','Ltf','Rtf','Ltr','Rtr']
if sent is not None:
    def envelope(pcm):
        return np.mean(pcm[:len(pcm)//480*480].reshape(-1,480,12)**2,axis=1)
    a=envelope(olr);b=envelope(sent)
    elags=np.arange(1-len(a),len(b))
    es=sum(np.correlate(b[:,out],a[:,source],mode='full') for out,source in enumerate(expected))
    delay=int(elags[np.argmax(es)])
    ai=max(0,-delay);bi=max(0,delay);length=min(len(a)-ai,len(b)-bi)
    aa=a[ai:ai+length];bb=b[bi:bi+length]
    em=(aa.T@bb)/np.maximum(np.linalg.norm(aa,axis=0)[:,None]*np.linalg.norm(bb,axis=0)[None,:],1e-30)
    mapping=[dict(output=i+1,input=i+25,speaker=labels[i],expectedOlrSource=source+1,
                  observedOlrSource=int(np.argmax(em[:,i]))+1,envelopeSimilarity=float(em[source,i]))
             for i,source in enumerate(expected)]
    mapping_passed=all(row['expectedOlrSource']==row['observedOlrSource'] and row['envelopeSimilarity']>0.98 for row in mapping)
    result.update(outputOrder=order,speakerMapping=mapping,speakerMappingPassed=bool(mapping_passed),envelopeLagFrames=delay*480)
    passed=passed and mapping_passed
    result['passed']=bool(passed)
(root/'comparison.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='correlationMatrix'},indent=2))
if not passed:
    raise SystemExit('Network return comparison failed; recordings retained')
