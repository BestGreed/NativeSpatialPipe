"""Three-tap fixed-rate offset; no drift or fused-pose latency claim."""
import argparse
import json
import math
from pathlib import Path
import statistics

from .tap_analysis import peaks,onset,validate_audio_timing,validate_imu_timing,audio_qpc_diagnostic
from .session import unpack24


def fit_triplet(audio_seconds,imu_seconds,tolerance=.004):
    if not math.isfinite(tolerance) or tolerance<=0: raise ValueError("Tolerance must be finite and positive")
    if len(audio_seconds)!=3 or len(imu_seconds)!=3: raise ValueError("Exactly three explicitly paired taps required")
    if not all(math.isfinite(x) for x in [*audio_seconds,*imu_seconds]): raise ValueError("Nonfinite tap time")
    if any(b<=a for values in (audio_seconds,imu_seconds) for a,b in zip(values,values[1:])):
        raise ValueError("Tap order must be strictly increasing")
    offsets=[a-i for a,i in zip(audio_seconds,imu_seconds)]; offset=statistics.median(offsets)
    residuals=[x-offset for x in offsets]
    return dict(offset_seconds=offset,rate=1.,drift_ppm=None,residuals_seconds=residuals,
                rms_seconds=math.sqrt(statistics.mean(x*x for x in residuals)),
                max_absolute_seconds=max(abs(x) for x in residuals),
                within_operational_tolerance=max(abs(x) for x in residuals)<=tolerance,
                tolerance_seconds=tolerance,interpretation="audio_to_raw_imu_only_not_fused_pose_latency",
                note="One triplet estimates offset at fixed unit rate; drift requires separated start/end groups")


def imu_gap_report(imu,max_interval=.020):
    """Retain whole-record interval issues without differencing across them."""
    issues=[]; maximum=None
    for index,(a,b) in enumerate(zip(imu,imu[1:]),1):
        dt=(b['unwrapped_u64']-a['unwrapped_u64'])/48000000
        if dt>0: maximum=dt if maximum is None else max(maximum,dt)
        reasons=[]
        if a.get('epoch',0)!=b.get('epoch',0): reasons.append('epoch_change')
        if dt<=0: reasons.append('nonincreasing_device_ticks')
        if dt>max_interval: reasons.append('device_tick_gap')
        if b.get('reordered',False): reasons.append('reordered')
        if reasons:
            issues.append(dict(later_imu_index=index,earlier_arrival_lsl=a['arrival_lsl'],
                later_arrival_lsl=b['arrival_lsl'],earlier_tick=a['unwrapped_u64'],later_tick=b['unwrapped_u64'],
                earlier_epoch=a.get('epoch',0),later_epoch=b.get('epoch',0),interval_seconds=dt,reasons=reasons))
    return dict(interval_limit_seconds=max_interval,maximum_positive_device_interval_seconds=maximum,
                issue_count=len(issues),issues=issues,interpretation='All-record issues are reported; only the selected triplet window plus margin is differenced and hard-validated.')


def analyze_recording(cfg,events,raw,start_lsl=None,end_lsl=None,audio_threshold=40000.,imu_threshold=150.,margin_seconds=.050):
    import numpy as np
    serial=cfg.get("alignment_tracker_serial","LHR-7712DB4C")
    audio=[e for e in events if e["kind"]=="audio_block"]
    all_imu=[e for e in events if e["kind"]=="raw_imu" and e["serial"]==serial and e.get("mask",3)&1]
    if len(audio)<2 or len(all_imu)<3: raise ValueError("Insufficient audio/B IMU")
    for value in (audio_threshold,imu_threshold):
        if not math.isfinite(value) or value<=0: raise ValueError("Tap thresholds must be finite and positive")
    if not math.isfinite(margin_seconds) or not 0<=margin_seconds<=.2: raise ValueError("Window margin must be 0..200ms")
    if start_lsl is not None and not math.isfinite(start_lsl) or end_lsl is not None and not math.isfinite(end_lsl):
        raise ValueError("Tap selection bounds must be finite")
    if start_lsl is not None and end_lsl is not None and end_lsl<=start_lsl: raise ValueError("Tap selection bounds must increase")
    lo=float("-inf") if start_lsl is None else start_lsl; hi=float("inf") if end_lsl is None else end_lsl
    selected_indices=[i for i,e in enumerate(all_imu) if lo-margin_seconds<=e['arrival_lsl']<=hi+margin_seconds]
    imu=[all_imu[i] for i in selected_indices]
    validate_imu_timing(imu)
    if imu[0].get('epoch',0)!=all_imu[0].get('epoch',0):
        raise ValueError('Selected window is in a different IMU epoch from the shared recording origin')
    rate=cfg["audio"]["rate"]; origin=audio[0]["lsl_first_frame"]
    bits=validate_audio_timing(audio,cfg,len(raw))
    x=unpack24(raw,bits).astype(float)
    delta=np.diff(x,axis=0); width=round(rate*.001); n=len(delta)//width*width
    av=np.sqrt(np.mean(delta[:n].reshape(-1,width,4)**2,axis=(1,2))); at=np.arange(len(av))*width/rate
    origin_tick=all_imu[0]['unwrapped_u64']
    ticks=np.array([e["unwrapped_u64"] for e in imu],dtype=np.int64); it=(ticks-origin_tick)/48e6
    iv=np.linalg.norm(np.diff(np.array([e["raw_accel_gyro"][:3] for e in imu]),axis=0),axis=1); it=it[1:]
    # Nonmaximum suppression is scoped to the selected window plus margin so
    # a strong scan event outside the triplet cannot hide a genuine boundary tap.
    audio_indices=np.where((origin+at>=lo-margin_seconds)&(origin+at<=hi+margin_seconds))[0]
    ap=[int(audio_indices[j]) for j in peaks(at[audio_indices],av[audio_indices],audio_threshold)]
    ip=peaks(it,iv,imu_threshold)
    coarse=all_imu[0]["arrival_lsl"]-origin
    ap=[j for j in ap if lo<=origin+at[j]<=hi]
    ip=[i for i in ip if lo<=imu[i+1]["arrival_lsl"]<=hi]
    used=set(); pairs=[]
    for i in ip:
        choices=[j for j in ap if abs(at[j]-it[i]-coarse)<=.1 and j not in used]
        if len(choices)!=1: continue
        j=choices[0]; used.add(j)
        ai=onset(at,av,j,audio_threshold); ii=onset(it,iv,i,imu_threshold)
        if not lo<=origin+at[ai]<=hi or not lo<=imu[ii+1]['arrival_lsl']<=hi:
            used.remove(j); continue
        pairs.append(dict(audio_seconds=float(at[ai]),imu_seconds=float(it[ii]),imu_tick=int(ticks[ii+1]),
                          imu_recording_index=selected_indices[ii+1]))
    sample_lo=0 if start_lsl is None else max(0,min(len(x),math.floor((start_lsl-origin)*rate)))
    sample_hi=len(x) if end_lsl is None else max(0,min(len(x),math.ceil((end_lsl-origin)*rate)))
    selected_x=x[sample_lo:sample_hi]
    selected_clipping=np.sum((selected_x<=-8388608)|(selected_x>=8388607),axis=0).tolist()
    result=dict(schema=1,tracker_serial=serial,audio_origin_lsl=origin,imu_origin_tick=int(origin_tick),tick_hz=48000000,
                imu_origin_epoch=all_imu[0].get('epoch',0),selected_imu_epoch=imu[0].get('epoch',0),
                selection=dict(start_lsl=start_lsl,end_lsl=end_lsl,imu_validation_margin_seconds=margin_seconds,
                               audio_sample_range=[sample_lo,sample_hi],selected_accelerometer_samples=len(imu)),
                candidates=dict(audio_seconds=[float(at[j]) for j in ap],imu_seconds=[float(it[i]) for i in ip]),
                paired_taps=pairs,thresholds=dict(audio=audio_threshold,imu=imu_threshold),
                unmatched_audio_candidates_seconds=[float(at[j]) for j in ap if j not in used],
                unmatched_audio_policy="Audio-only events are retained separately; exactly three IMU candidates must each have one unique audio partner",
                clipping_counts_per_channel=np.sum((x<=-8388608)|(x>=8388607),axis=0).tolist(),
                selected_window_clipping_counts_per_channel=selected_clipping,
                selected_window_clipping_is_quality_gate=True,
                all_record_imu_timing=imu_gap_report(all_imu),
                selected_window_imu_timing=imu_gap_report(imu),
                audio_qpc_timing=audio_qpc_diagnostic(audio),
                status="ambiguous_or_missing_triplet")
    if len(ip)==3 and len(pairs)==3:
        result["fit"]=fit_triplet([e["audio_seconds"] for e in pairs],[e["imu_seconds"] for e in pairs])
        result["status"]="provisional_triplet_offset" if result["fit"]["within_operational_tolerance"] else "residual_limit_exceeded"
        if any(selected_clipping): result["status"]="clipped_requires_repeat"
    quality_failures=[]
    if len(ip)!=3: quality_failures.append('expected_exactly_three_imu_candidates')
    if len(pairs)!=3: quality_failures.append('expected_exactly_three_unique_audio_imu_pairs')
    if any(selected_clipping): quality_failures.append('selected_triplet_window_clipped')
    if result.get('fit') and not result['fit']['within_operational_tolerance']:
        quality_failures.append('triplet_residual_limit_exceeded')
    result['quality_failures']=quality_failures
    return result


def analyze(folder,start_lsl=None,end_lsl=None,audio_threshold=40000.,imu_threshold=150.,margin_seconds=.050):
    p=Path(folder); cfg=json.loads((p/"session.json").read_text(encoding="utf-8"))["config"]
    events=[json.loads(line) for line in (p/"events.jsonl").open(encoding="utf-8") if line.strip()]
    return analyze_recording(cfg,events,(p/'audio.pcm').read_bytes(),start_lsl,end_lsl,audio_threshold,imu_threshold,margin_seconds)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("folder");p.add_argument("output")
    p.add_argument("--start-lsl",type=float);p.add_argument("--end-lsl",type=float)
    p.add_argument("--audio-threshold",type=float,default=40000);p.add_argument("--imu-threshold",type=float,default=150)
    a=p.parse_args();r=analyze(a.folder,a.start_lsl,a.end_lsl,a.audio_threshold,a.imu_threshold)
    with Path(a.output).open("x",encoding="utf-8") as f:json.dump(r,f,indent=2)
    print(json.dumps(r,indent=2))


if __name__=="__main__": main()
