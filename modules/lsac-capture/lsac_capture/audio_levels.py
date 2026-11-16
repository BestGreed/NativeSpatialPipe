"""Recorded ADC peak/RMS/band levels; never reports sound pressure calibration."""
import argparse
import json
from pathlib import Path


def report(folder,window_seconds=.1):
    import numpy as np
    from .session import unpack24
    from .scan_analysis import third_octave_rms
    from .tap_analysis import validate_audio_timing,audio_qpc_diagnostic
    folder=Path(folder);config=json.loads((folder/"session.json").read_text(encoding="utf-8"))["config"]
    events=[json.loads(line) for line in (folder/"events.jsonl").open(encoding="utf-8") if line.strip()]
    blocks=[e for e in events if e["kind"]=="audio_block"]
    raw=(folder/"audio.pcm").read_bytes();bits=validate_audio_timing(blocks,config,len(raw))
    samples=unpack24(raw,bits).astype(float);rate=48000;frames=round(window_seconds*rate)
    if not .05<=window_seconds<=1:raise ValueError("Level window must be .05..1 seconds")
    origin=blocks[0]["lsl_first_frame"]
    starts=[e for e in events if e["kind"]=="playback_started"]
    ends=[e for e in events if e["kind"]=="playback_finished"]
    start=starts[0]["arrival_lsl"]-origin if starts else None
    end=ends[-1]["arrival_lsl"]-origin if ends else None
    values=[]
    for i in range(0,len(samples)-frames+1,frames):
        x=samples[i:i+frames];time=i/rate+window_seconds/2
        level=lambda v:(20*np.log10(np.maximum(v,1e-15)/8388608)).tolist()
        values.append(dict(sample_start=i,nominal_seconds=time,peak_dbfs=level(np.max(abs(x),axis=0)),
                           rms_dbfs=level(np.sqrt(np.mean(x*x,axis=0))),band_dbfs=level(third_octave_rms(x)),
                           during_playback=start is not None and end is not None and start+.3<time<end-.3,
                           before_playback=start is not None and time<start-.3))
    before=[v["band_dbfs"] for v in values if v["before_playback"]]
    during=[v["band_dbfs"] for v in values if v["during_playback"]]
    b=np.median(before,axis=0) if before else None;d=np.median(during,axis=0) if during else None
    return dict(schema=1,source=str(folder.resolve()),channel_labels=["USB_ch1","USB_ch2","USB_ch3","USB_ch4"],
                channel_semantics_verified=config.get("audio",{}).get("channel_order_verified",False),
                user_reported_mic_gain=config.get("audio",{}).get("user_reported_mic_gain"),
                recording_peak_dbfs=(20*np.log10(np.maximum(np.max(abs(samples),axis=0),1e-15)/8388608)).tolist(),
                clipped_counts_per_channel=np.sum((samples<=-8388608)|(samples>=8388607),axis=0).tolist(),
                median_before_band_dbfs=b.tolist() if b is not None else None,
                median_during_band_dbfs=d.tolist() if d is not None else None,
                median_band_rise_db=(d-b).tolist() if b is not None and d is not None else None,
                playback_api_interval_nominal_seconds=[start,end],window_seconds=window_seconds,
                time_basis="nominal_sample_index/48000_from_first_raw_anchor; unvalidated",audio_qpc_timing=audio_qpc_diagnostic(blocks),
                db_spl=False,raw_timestamps_modified=False,windows=values)


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("capture");p.add_argument("output")
    a=p.parse_args();r=report(a.capture)
    with Path(a.output).open("x",encoding="utf-8") as f:json.dump(r,f,indent=2)
    print(json.dumps({k:v for k,v in r.items() if k not in ("windows","audio_qpc_timing")},indent=2))


if __name__=="__main__":main()
