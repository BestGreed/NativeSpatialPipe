import argparse
import hashlib
import json
import math
from pathlib import Path
import threading
import time

from .core import validate_config,fit_taps,fit_common_axis

ROOT=Path(__file__).resolve().parents[3]
LAB=ROOT/"work/lsac-binaries/LabRecorder-1.18.0/LabRecorder-1.18.0-Win_amd64/LabRecorderCLI.exe"
SURVIVE=ROOT/"work/lsac-binaries/libsurvive-v1.01/bin/libsurvive.dll"


def run_record(config,out,seconds,diagnostic=False,synthetic=False):
    import pylsl
    from .session import Session,replay_check
    from .windows import capture,qpc,qpf
    if not synthetic and not diagnostic: validate_config(config)
    if not 0 < seconds <= 600: raise ValueError("Duration must be >0 and <=600 seconds")
    session=Session(out,config,LAB)
    errors=[]; workers=[]
    def guard(fn):
        def run():
            try: fn()
            except BaseException as exc:
                errors.append(repr(exc)); session.stop.set()
        t=threading.Thread(target=run,name="lsac-acquisition"); t.start(); workers.append(t)
    try:
        if synthetic:
            import numpy as np
            rate=48000; first=qpc()/qpf(); logical=0
            for i in range(20):
                raw=np.arange(480*4,dtype=np.int32).reshape(480,4)+(i*7)
                packed=np.stack((raw&255,(raw>>8)&255,(raw>>16)&255),axis=-1).astype(np.uint8).tobytes()
                e=dict(kind="audio_block",sample_start_i64=logical,frames=480,device_position_u64=logical,
                       device_qpc_100ns_u64=round((first+logical/rate)*1e7),arrival_qpc_i64=qpc(),flags=0,
                       epoch=0,rate=rate,channels=4,container_bits=24,valid_bits=24,device_gap_frames=0,
                       discontinuity=False,timestamp_error=False,synthetic=True)
                session.submit(e,packed)
                for serial in ("synthetic-probe","synthetic-reference"):
                    session.submit(dict(kind="pose",serial=serial,position_m=[i*.01,0,0],quaternion_wxyz=[1,0,0,0],
                                        pose_timecode_u64=i*480000,epoch=0,valid=True,synthetic=True))
                    session.submit(dict(kind="raw_imu",serial=serial,raw_accel_gyro=[0,0,1,0,0,0],raw_u32=i*480000,
                                        unwrapped_u64=i*480000,epoch=0,synthetic=True))
                logical+=480; time.sleep(.01)
        else:
            ready=threading.Event()
            guard(lambda:capture(config["audio"],session.stop,session.submit,ready))
            deadline=time.monotonic()+8
            while not ready.is_set() and not session.stop.is_set() and time.monotonic()<deadline: time.sleep(.02)
            if not ready.is_set(): raise RuntimeError("Audio did not start: "+str(errors))
            if not diagnostic:
                from .tracker_worker import supervised
                guard(lambda:supervised(Path(out)/"tracking",SURVIVE,config["tracking"]["serials"],False,
                                        session.stop,session.submit,seconds+8))
            deadline=time.monotonic()+seconds
            while not session.stop.is_set() and time.monotonic()<deadline: time.sleep(.02)
    finally:
        session.stop.set()
        for t in workers:
            t.join(timeout=8)
            if t.is_alive(): errors.append("Acquisition thread did not stop")
        result=session.close()
        result["acquisition_errors"]=errors
        if errors: result["status"]="incomplete"
        (Path(out)/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    result["replay"]=replay_check(out)
    if result["replay"]["errors"] and not diagnostic: result["status"]="incomplete"
    (Path(out)/"replay-check.json").write_text(json.dumps(result["replay"],indent=2),encoding="utf-8")
    (Path(out)/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def excitation(out,kind,frequency,band,peak_dbfs,seconds,seed):
    """Prepare exact reference only. No output device opened."""
    import numpy as np
    import wave
    if not -80 <= peak_dbfs <= -40 or not .1 <= seconds <= 60:
        raise ValueError("Pilot bounds: -80..-40 dBFS, .1..60 seconds")
    if not 20 <= frequency <= 18000 or not 20 <= band[0] < band[1] <= 18000:
        raise ValueError("Invalid frequency/band")
    p=Path(out); p.mkdir(parents=True,exist_ok=False); count=round(seconds*48000)
    if kind=="sine": samples=np.sin(2*np.pi*frequency*np.arange(count)/48000)
    else:
        rng=np.random.default_rng(seed); noise=rng.normal(size=count)
        spectrum=np.fft.rfft(noise); bins=np.fft.rfftfreq(count,1/48000)
        spectrum[(bins<band[0]) | (bins>band[1])]=0
        samples=np.fft.irfft(spectrum,n=count)
    ramp=min(2400,count//4); envelope=np.ones(count)
    envelope[:ramp]=np.linspace(0,1,ramp); envelope[-ramp:]=np.linspace(1,0,ramp)
    samples*=envelope; samples*=10**(peak_dbfs/20)/np.max(np.abs(samples))
    stereo=np.column_stack((samples,samples)).astype("<f4")
    np.save(p/"reference.npy",stereo)
    counts=np.rint(stereo*8388607).astype(np.int32)
    raw=np.stack((counts&255,(counts>>8)&255,(counts>>16)&255),axis=-1).astype(np.uint8).tobytes()
    with wave.open(str(p/"reference.wav"),"wb") as f:
        f.setparams((2,3,48000,0,"NONE","not compressed")); f.writeframes(raw)
    result=dict(kind=kind,frequency=frequency,band_hz=band,peak_dbfs=peak_dbfs,seconds=seconds,seed=seed,
                sample_rate=48000,channels=2,generated_sample_start=0,frames=count,
                sha256=hashlib.sha256((p/"reference.npy").read_bytes()).hexdigest(),
                output_route=None,output_clock=None,status="generated_not_played")
    (p/"reference.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def main():
    p=argparse.ArgumentParser(description="LSAC continuous scan research core. No playback by default.")
    sub=p.add_subparsers(dest="cmd",required=True)
    sub.add_parser("inventory")
    a=sub.add_parser("receiver-plan"); a.add_argument("serial"); a.add_argument("output_json")
    a=sub.add_parser("receiver-pair"); a.add_argument("plan_json"); a.add_argument("attempt_json")
    a.add_argument("--confirm-single-receiver-and-one-tracker",action="store_true")
    a=sub.add_parser("receiver-observe"); a.add_argument("plan_json"); a.add_argument("output_json")
    a=sub.add_parser("probe-audio"); a.add_argument("endpoint_id")
    a=sub.add_parser("simulate"); a.add_argument("output")
    a=sub.add_parser("record"); a.add_argument("config"); a.add_argument("output"); a.add_argument("--seconds",type=float,default=10)
    a.add_argument("--confirm-feature-reports",action="store_true")
    a=sub.add_parser("audio-diagnostic"); a.add_argument("endpoint_id"); a.add_argument("output"); a.add_argument("--seconds",type=float,default=3)
    a=sub.add_parser("tracker-diagnostic"); a.add_argument("output"); a.add_argument("--seconds",type=float,default=6)
    a.add_argument("--confirm-feature-reports",action="store_true")
    a=sub.add_parser("check"); a.add_argument("folder")
    a=sub.add_parser("window"); a.add_argument("folder"); a.add_argument("start",type=int); a.add_argument("end",type=int)
    a=sub.add_parser("tap-fit"); a.add_argument("pairs_json")
    a=sub.add_parser("axis-fit"); a.add_argument("directions_json")
    a=sub.add_parser("prepare-excitation"); a.add_argument("output"); a.add_argument("--kind",choices=("sine","band-noise"),default="sine")
    a.add_argument("--frequency",type=float,default=1000); a.add_argument("--band",nargs=2,type=float,default=[500,1000])
    a.add_argument("--peak-dbfs",type=float,default=-60); a.add_argument("--seconds",type=float,default=2); a.add_argument("--seed",type=int,default=1)
    a=p.parse_args()
    if a.cmd=="inventory":
        from .windows import inventory
        r=inventory()
    elif a.cmd=="receiver-plan":
        from .receiver import plan
        r=plan(a.serial)
        with Path(a.output_json).open("x",encoding="utf-8") as f: json.dump(r,f,indent=2,ensure_ascii=False)
    elif a.cmd=="receiver-pair":
        from .receiver import pair
        r=pair(a.plan_json,a.attempt_json,a.confirm_single_receiver_and_one_tracker)
    elif a.cmd=="receiver-observe":
        from .receiver import observe
        r=observe(a.plan_json,a.output_json)
        r=dict(r); r.pop("reports",None)
    elif a.cmd=="probe-audio":
        from .windows import probe_format
        r=probe_format(a.endpoint_id)
    elif a.cmd=="simulate": r=run_record(dict(schema=1,synthetic=True),a.output,1,synthetic=True)
    elif a.cmd=="record":
        if not a.confirm_feature_reports: raise ValueError("libsurvive start configures HID; require explicit --confirm-feature-reports after OS inventory")
        r=run_record(json.loads(Path(a.config).read_text(encoding="utf-8")),a.output,a.seconds)
    elif a.cmd=="audio-diagnostic":
        c=dict(schema=1,diagnostic_audio_only=True,tracking=dict(serials=[]),audio=dict(endpoint_id=a.endpoint_id,
               rate=48000,channels=4,container_bits=24,valid_bits=24,format="PCM",mode="exclusive",
               ambi_order="unverified",normalization="unverified",mic_position="not_read_from_device"))
        r=run_record(c,a.output,a.seconds,diagnostic=True)
    elif a.cmd=="tracker-diagnostic":
        if not a.confirm_feature_reports: raise ValueError("Requires --confirm-feature-reports; pairing remains disabled")
        if not 0<a.seconds<=30: raise ValueError("Diagnostic limited to 30 seconds")
        from .tracker_worker import supervised
        from collections import Counter
        out=Path(a.output); out.mkdir(parents=True,exist_ok=False)
        records=[]; stop=threading.Event(); dropped=[0]
        def submit(event,raw):
            if len(records)>=100000:
                dropped[0]+=1; stop.set(); return False
            records.append(event); return True
        failure=None
        try: supervised(out/"native-worker",SURVIVE,None,True,stop,submit,a.seconds)
        except RuntimeError as exc: failure=str(exc)
        finally:
            stop.set()
            (out/"events.jsonl").write_text("".join(json.dumps(e,allow_nan=False)+"\n" for e in records),encoding="utf-8")
        r=dict(pairing_enabled=False,calibration_enabled=False,recorded_events=len(records),dropped=dropped[0],
               counts=dict(Counter(e["kind"] for e in records)),serials=sorted({e["serial"] for e in records if "serial" in e}),
               failure=failure,note="Diagnostic feature reports/start allowed; no pairing, firmware or driver changes; optical quality not calibrated")
        (out/"result.json").write_text(json.dumps(r,indent=2),encoding="utf-8")
    elif a.cmd=="check":
        from .session import replay_check
        r=replay_check(a.folder)
    elif a.cmd=="window":
        from .session import associate
        r=associate(a.folder,a.start,a.end)
    elif a.cmd=="tap-fit":
        pairs=json.loads(Path(a.pairs_json).read_text(encoding="utf-8"))
        if not all(3 <= pairs["groups"].count(group) <=5 for group in ("start","end")): raise ValueError("Need 3–5 taps at each end")
        r=fit_taps(pairs["audio_seconds"],pairs["imu_seconds"])
    elif a.cmd=="axis-fit":
        data=json.loads(Path(a.directions_json).read_text(encoding="utf-8"))
        r=fit_common_axis(data["measured_nominal_local"],data["reference_local"],data["common_axis_local"])
    else: r=excitation(a.output,a.kind,a.frequency,a.band,a.peak_dbfs,a.seconds,a.seed)
    print(json.dumps(r,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
