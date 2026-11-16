"""Short simultaneous H3-VR PCM24 + receiver-B raw IMU timestamp recording.

No excitation playback, pairing, receiver A or HMD initialization. User taps
only after READY. Raw native spool and unsmoothed device timestamps retained.
"""
import json
from pathlib import Path
import sys
import threading
import time

from .session import Session,replay_check
from .windows import capture
from .tracker_worker import supervised
from .receiver_session import DLL,DLL_SHA,ROOT

RECEIVER_B="C0531DBB07-2YX"
TRACKER="LHR-7712DB4C"
LAB=ROOT/"work/lsac-binaries/LabRecorder-1.18.0/LabRecorder-1.18.0-Win_amd64/LabRecorderCLI.exe"


def run(out,seconds=150):
    out=Path(out)
    policy_path=Path(__file__).resolve().parents[1]/"tap-policy.json"
    policy=json.loads(policy_path.read_text(encoding="utf-8")) if policy_path.exists() else dict(start_count=3,end_count=5)
    config=dict(schema=1,capture_purpose="audio_raw_imu_time_alignment",tracking=dict(serials=[TRACKER],receiver_serial=RECEIVER_B,pairing=False),
                audio=dict(endpoint_id="{0.0.1.00000000}.{324d001e-318b-4f8c-b208-5cc6a9324821}",
                           rate=48000,channels=4,container_bits=24,valid_bits=24,format="PCM",mode="exclusive",
                           channel_order_verified=False,normalization="unverified",mic_position="unchanged_not_read"),
                tapping_plan=dict(start_count=policy['start_count'],end_count=policy['end_count'],
                                  minimum_group_separation_seconds=20,policy=policy),
                note="Raw channel order retained; no pose-timing/absolute acoustic calibration asserted")
    session=Session(out,config,LAB)
    ready=threading.Event(); counters={"audio_block":0,"raw_imu":0}; failures=[]
    def submit(event,raw=None):
        kind=event["kind"]
        if kind in counters: counters[kind]+=1
        return session.submit(event,raw)
    def guard(fn):
        def target():
            try: fn()
            except BaseException as exc: failures.append(repr(exc)); session.stop.set()
        t=threading.Thread(target=target); t.start(); return t
    audio=guard(lambda:capture(config["audio"],session.stop,submit,ready))
    tracker=guard(lambda:supervised(out/"tracking",DLL,[TRACKER],True,session.stop,submit,seconds+20,
                                    receiver_serials=[RECEIVER_B],reviewed_sha256=DLL_SHA))
    phase="waiting_for_both_streams"; started=None
    try:
        deadline=time.monotonic()+15
        while time.monotonic()<deadline and not session.stop.is_set():
            if ready.is_set() and counters["audio_block"]>=10 and counters["raw_imu"]>=25: break
            time.sleep(.05)
        else: raise RuntimeError("Both live streams not ready: "+str(counters)+str(failures))
        phase="READY_FOR_USER_TAPS"; started=time.time()
        (out/"live-status.json").write_text(json.dumps(dict(phase=phase,ready_unix=started,counts=counters,
              target_tracker=TRACKER,receiver=RECEIVER_B,duration_seconds=seconds),indent=2),encoding="utf-8")
        print("READY_FOR_USER_TAPS",flush=True)
        end=time.monotonic()+seconds
        while time.monotonic()<end and not session.stop.is_set() and not (out/"STOP").exists():
            (out/"live-status.json").write_text(json.dumps(dict(phase=phase,ready_unix=started,counts=counters,
                  elapsed_seconds=time.time()-started,target_tracker=TRACKER,receiver=RECEIVER_B),indent=2),encoding="utf-8")
            time.sleep(.25)
    except BaseException as exc:
        failures.append(repr(exc)); raise
    finally:
        session.stop.set(); audio.join(timeout=8); tracker.join(timeout=10)
        if audio.is_alive() or tracker.is_alive(): failures.append("Producer did not stop")
        result=session.close(); result.update(capture_failures=failures,ready_unix=started)
        if failures: result["status"]="incomplete"
        (out/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        (out/"live-status.json").write_text(json.dumps(dict(phase="closed",counts=counters,failures=failures),indent=2),encoding="utf-8")
    result["replay"]=replay_check(out)
    if result["replay"]["errors"]: result["status"]="incomplete"
    (out/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2),flush=True)


if __name__=="__main__": run(sys.argv[1],float(sys.argv[2]) if len(sys.argv)>2 else 150)
