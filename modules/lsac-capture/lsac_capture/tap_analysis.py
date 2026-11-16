"""Inspect paired acoustic/raw-IMU taps without modifying original timestamps.

Detection is provisional: save thresholds, all candidates, pairing gates and
onset/peak sensitivity. This measures the whole mechanical/acoustic path,
not an independently known ADC latency or fused-pose timestamp offset.
"""
import csv
import json
import math
from pathlib import Path
import sys

import numpy as np

from .core import fit_taps
from .session import unpack24


def peaks(times, values, threshold, separation=.18):
    candidates=np.where((values[1:-1]>=values[:-2]) &
                        (values[1:-1]>values[2:]) & (values[1:-1]>threshold))[0]+1
    selected=[]
    for i in candidates[np.argsort(values[candidates])[::-1]]:
        if all(abs(times[i]-times[j])>separation for j in selected): selected.append(int(i))
    return sorted(selected)


def onset(times, values, peak, threshold, lookback=.030):
    indices=np.where((times>=times[peak]-lookback)&(times<=times[peak])&(values>threshold))[0]
    return int(indices[0]) if len(indices) else peak


def validate_audio_timing(audio, config, pcm_bytes):
    """Validate sample continuity, not packet-QPC agreement with sample rate.

    Tap onset time is logical sample/48000. Raw packet QPC remains diagnostic;
    a mismatch between packet QPC increments and packet length is not by itself
    a missing-sample indication. The 100 ms backwards bound below is a gross
    timestamp sanity check, not a synchronization accuracy tolerance.
    """
    if not audio: raise ValueError('No recorded audio blocks')
    expected=0; byte_count=0; first=audio[0]
    bits=first.get('container_bits',24)
    if bits not in (24,32): raise ValueError('Unsupported audio container')
    previous=None
    for e in audio:
        if e.get('rate',config['audio']['rate'])!=48000 or e.get('channels',4)!=4 or e.get('container_bits',24)!=bits:
            raise ValueError('Tap timing requires one exact 4ch/48k PCM24 format')
        if e.get('sample_start_i64')!=expected or e.get('frames',0)<=0:
            raise ValueError('Audio logical gap/overlap invalidates tap timing')
        if e.get('flags') or e.get('device_gap_frames') or e.get('discontinuity') or e.get('timestamp_error'):
            raise ValueError('Audio gap/flags invalidate tap timing')
        if e.get('timestamp_basis')=='arrival_fallback_invalid_hardware_timestamp':
            raise ValueError('Audio arrival-time fallback invalidates tap timing')
        if not math.isfinite(e.get('lsl_first_frame',float('nan'))):
            raise ValueError('Audio hardware timestamp missing/nonfinite')
        qpc=e.get('device_qpc_100ns_u64')
        if qpc is not None and (not isinstance(qpc,(int,float)) or not math.isfinite(qpc) or qpc<0):
            raise ValueError('Audio raw QPC missing/nonfinite/negative')
        if previous:
            if e.get('epoch',0)!=previous.get('epoch',0):
                raise ValueError('Audio epoch change invalidates tap timing')
            if e['lsl_first_frame']-previous['lsl_first_frame']<-.100:
                raise ValueError('Audio timestamp has a gross backwards step')
            old_qpc=previous.get('device_qpc_100ns_u64')
            if qpc is not None and old_qpc is not None and (qpc-old_qpc)/1e7<-.100:
                raise ValueError('Audio raw QPC has a gross backwards step')
            old_position=previous.get('device_position_u64')
            position=e.get('device_position_u64')
            if old_position is not None and position is not None and position!=old_position+previous['frames']:
                raise ValueError('Audio device-position gap/overlap invalidates tap timing')
        if e.get('pcm_byte_offset',byte_count)!=byte_count:
            raise ValueError('Audio PCM byte offsets are not contiguous')
        expected+=e['frames']; byte_count+=e['frames']*4*(bits//8); previous=e
    if pcm_bytes!=byte_count: raise ValueError('PCM byte count differs from audio sidecar')
    return bits


def audio_qpc_diagnostic(audio):
    """Describe raw QPC packet jitter separately from logical sample continuity."""
    def summary(values):
        if not values: return dict(count=0,minimum=None,maximum=None,median=None,over_two_samples=0)
        return dict(count=len(values),minimum=float(min(values)),maximum=float(max(values)),
                    median=float(np.median(values)),over_two_samples=sum(abs(v)>2/48000 for v in values))
    lsl_step=[]; qpc_step=[]; qpc_origin=[]; lsl_origin=[]
    if audio:
        first=audio[0]; first_qpc=first.get('device_qpc_100ns_u64')
        for e in audio:
            nominal=(e['sample_start_i64']-first['sample_start_i64'])/48000
            lsl_origin.append(e['lsl_first_frame']-first['lsl_first_frame']-nominal)
            if first_qpc is not None and e.get('device_qpc_100ns_u64') is not None:
                qpc_origin.append((e['device_qpc_100ns_u64']-first_qpc)/1e7-nominal)
        for a,b in zip(audio,audio[1:]):
            nominal=a['frames']/48000
            lsl_step.append(b['lsl_first_frame']-a['lsl_first_frame']-nominal)
            if a.get('device_qpc_100ns_u64') is not None and b.get('device_qpc_100ns_u64') is not None:
                qpc_step.append((b['device_qpc_100ns_u64']-a['device_qpc_100ns_u64'])/1e7-nominal)
    return dict(raw_qpc_step_minus_nominal_seconds=summary(qpc_step),
                mapped_lsl_step_minus_nominal_seconds=summary(lsl_step),
                raw_qpc_from_first_minus_nominal_seconds=summary(qpc_origin),
                mapped_lsl_from_first_minus_nominal_seconds=summary(lsl_origin),
                onset_time_basis='logical_sample_index/48000_nominal_rate',
                raw_timestamp_source='events.jsonl: device_qpc_100ns_u64 and lsl_first_frame',
                timestamps_modified=False,dejitter_applied=False,clock_accuracy_validated=False,
                interpretation='Packet timestamp residuals are retained as timing evidence, not treated as sample loss when device positions/logical samples/flags are continuous; nominal sample timing does not remove or validate QPC jitter.')


def validate_imu_timing(imu,max_interval=.020):
    """Reject differences across missing/reordered accelerometer intervals."""
    if len(imu)<3: raise ValueError('Insufficient selected-tracker accelerometer samples')
    if len({e.get('epoch',0) for e in imu})!=1 or any(e.get('reordered',False) for e in imu):
        raise ValueError('IMU reconnect/reordering needs manual segmentation')
    if any(e.get('tick_hz',48000000)!=48000000 for e in imu):
        raise ValueError('Unexpected IMU tick frequency')
    ticks=[e['unwrapped_u64'] for e in imu]
    if any(b<=a or (b-a)/48000000>max_interval for a,b in zip(ticks,ticks[1:])):
        raise ValueError('IMU device-clock gap/repetition exceeds tap timing interval limit; segment/re-record')


def analyze(folder,serial=None):
    p=Path(folder)
    config=json.loads((p/'session.json').read_text(encoding='utf-8'))['config']
    plan=config.get('tapping_plan',{})
    expected=[plan.get('start_count',3),plan.get('end_count',5)]
    events=[json.loads(line) for line in (p/'events.jsonl').open(encoding='utf-8')]
    audio=[e for e in events if e['kind']=='audio_block']
    serial=serial or config.get('alignment_tracker_serial')
    serials={e['serial'] for e in events if e['kind']=='raw_imu'}
    if serial is None and len(serials)!=1: raise ValueError('Explicit alignment_tracker_serial required for multiple IMU devices')
    serial=serial or next(iter(serials))
    imu=[e for e in events if e['kind']=='raw_imu' and e['serial']==serial and e.get('mask',3)&1]
    if len(imu)<3: raise ValueError('Insufficient IMU samples for selected tracker: '+str(serial))
    validate_imu_timing(imu)
    raw=(p/'audio.pcm').read_bytes(); bits=validate_audio_timing(audio,config,len(raw))
    x=unpack24(raw,bits).astype(np.float64)
    dx=np.diff(x,axis=0); n=len(dx)//48*48
    # RMS of four-channel first difference, 1 ms bins; retain all channel data.
    av=np.sqrt(np.mean(dx[:n].reshape(-1,48,4)**2,axis=(1,2)))
    at=np.arange(len(av))*.001
    ticks=np.array([e['unwrapped_u64'] for e in imu],dtype=np.int64)
    it=(ticks-ticks[0])/48e6
    a=np.array([e['raw_accel_gyro'][:3] for e in imu])
    iv=np.linalg.norm(np.diff(a,axis=0),axis=1)
    # Jerk belongs to the interval ending at the later IMU sample.
    it=it[1:]
    ap=peaks(at,av,50000); ip=peaks(it,iv,300)
    if not audio or not ap or not ip: raise ValueError('No usable audio/IMU tap candidates; inspect channel levels and thresholds')
    anchor=audio[0]['lsl_first_frame']
    coarse_offset=imu[0]['arrival_lsl']-anchor
    pairs=[]; used=set()
    for i in ip:
        choices=[j for j in ap if abs(at[j]-(it[i]+coarse_offset))<=.100 and j not in used]
        if len(choices)!=1: continue
        j=choices[0]
        used.add(j)
        ai=onset(at,av,j,40000); ii=onset(it,iv,i,150)
        pairs.append(dict(audio_peak_seconds=float(at[j]),imu_peak_seconds=float(it[i]),
                          audio_onset_seconds=float(at[ai]),imu_onset_seconds=float(it[ii]),
                          audio_sample_index=ai*48,imu_index=ii+1,
                          imu_unwrapped_tick=int(ticks[ii+1]),
                          audio_peak_strength=float(av[j]),imu_peak_strength=float(iv[i]),
                          host_gate_difference_seconds=float(at[j]-it[i]-coarse_offset)))
    if len(pairs)<6: raise ValueError('Need at least six unambiguous paired taps; use tap_triplet for one group')
    fits={}
    for method in ('onset','peak'):
        fits[method]=fit_taps([v[f'audio_{method}_seconds'] for v in pairs],
                              [v[f'imu_{method}_seconds'] for v in pairs])
    sensitivity={}
    for threshold in (20000,40000,80000):
        times=[float(at[onset(at,av,min(ap,key=lambda j:abs(at[j]-v['audio_peak_seconds'])),threshold)]) for v in pairs]
        sensitivity[str(threshold)]=fit_taps(times,[v['imu_onset_seconds'] for v in pairs])
    gaps=np.diff([v['audio_onset_seconds'] for v in pairs])
    split=int(np.argmax(gaps))+1 if len(gaps) else 0
    offsets=np.array([v['audio_onset_seconds']-v['imu_onset_seconds'] for v in pairs])
    validation={}
    if split==expected[0] and len(pairs)==sum(expected):
        start_offset=float(np.median(offsets[:split]))
        residual=offsets[split:]-start_offset
        validation=dict(method=f'fixed unit rate; median offset from first {split}; last {expected[1]} held out',
                        offset_seconds=start_offset,residuals_seconds=residual.tolist(),
                        rms_seconds=float(np.sqrt(np.mean(residual**2))),
                        max_absolute_seconds=float(max(abs(residual))))
    bad_audio=[e for e in audio if e['flags'] or e['device_gap_frames']]
    result=dict(schema=1,source=str(p.resolve()),status='provisional_tap_alignment',
                tracker_serial=serial,
                audio_origin='first recorded audio sample; seconds=sample_index/48000',
                imu_origin=dict(first_unwrapped_tick=int(ticks[0]),tick_hz=48000000),
                model='audio_seconds = offset_seconds + rate * imu_seconds',
                paired_taps=pairs,fits=fits,audio_onset_threshold_sensitivity=sensitivity,
                held_out_group_validation=validation,
                group_sizes=[split,len(pairs)-split],
                group_gap_seconds=float(max(gaps)) if len(gaps) else None,
                expected_group_sizes=expected,group_pattern_matches=(split==expected[0] and len(pairs)==sum(expected)),
                candidates=dict(audio=[float(at[j]) for j in ap],imu=[float(it[i]) for i in ip]),
                thresholds=dict(audio_peak_rms_difference=50000,audio_onset=40000,
                                imu_peak_accel_difference=300,imu_onset=150,
                                nms_separation_seconds=.18,onset_lookback_seconds=.030,
                                host_arrival_pair_gate_seconds=.100),
                quality=dict(audio_flagged_blocks=len(bad_audio),
                             audio_qpc_timing=audio_qpc_diagnostic(audio),
                             imu_dt_median_seconds=float(np.median(np.diff(it))),
                             imu_dt_max_seconds=float(max(np.diff(it))),
                             imu_intervals_over_8ms=int(np.sum(np.diff(it)>.008)),
                             imu_masks=sorted(set(e['mask'] for e in imu)),
                             pcm_clipped_counts_per_channel=np.sum((x<=-8388608)|(x>=8388607),axis=0).tolist()),
                limits=['Event pairing is provisional and threshold-dependent',
                        'Some audio channels clipped during taps; use gentler taps for validation',
                        'IMU has irregular device-tick intervals; no full sample-counter loss proof',
                        'Mechanical/acoustic propagation and sensor filtering are included',
                        'Raw IMU is sampled about 250 Hz; sub-millisecond accuracy is not established',
                        'Short time span does not establish long-term clock drift',
                        'No fused-pose latency or full acoustic calibration is established'])
    (p/'tap-alignment.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    with (p/'paired-taps.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=list(pairs[0]));writer.writeheader();writer.writerows(pairs)
    print(json.dumps(result,indent=2))
    return result


if __name__=='__main__': analyze(sys.argv[1])
