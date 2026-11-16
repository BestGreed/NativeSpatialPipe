"""Maintain one reviewed receiver session; pairing is hard-disabled."""
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[3]
DLL=ROOT/"build/lsac/libsurvive-current/libsurvive.dll"
DLL_SHA="4ef938ada8d5ad86f172d0b9b86fdf4cf06fb41935a0fba2cc5b1f8da5721317"
RECEIVER=os.environ.get("LSAC_RECEIVER_SERIAL","4E482CC871-2YX")
if RECEIVER not in ("4E482CC871-2YX","C0531DBB07-2YX"):
    raise ValueError("Receiver serial is outside the two reviewed devices")


def validate_session(seconds,mode):
    if mode not in ("diagnostic","pose"): raise ValueError("Unknown receiver mode")
    if not math.isfinite(seconds) or not 0<seconds<=(120 if mode=="pose" else 86400):
        raise ValueError("Invalid session duration")


def worker(folder,seconds,mode="diagnostic"):
    validate_session(seconds,mode)
    import pylsl
    from .tracking import Tracker
    folder=Path(folder).resolve(); os.chdir(folder)
    directory_handles=[os.add_dll_directory(str(DLL.parent)),os.add_dll_directory(str(ROOT/"work/lsac-toolchain/llvm-mingw-20261006-ucrt-x86_64/bin"))]
    stop=threading.Event(); pending=queue.Queue(8192); counts=Counter(); serials={}; failures=[]
    start=time.monotonic()
    status=dict(receiver=RECEIVER,pairing_enabled=False,hmd_excluded=True,mode=mode,calibration_enabled=mode=="pose",phase="starting",worker_pid=os.getpid(),counts={},tracker_serials={})
    def save():
        status.update(counts=dict(counts),tracker_serials=serials,updated_unix=time.time())
        temp=folder/"status.tmp"; temp.write_text(json.dumps(status,indent=2),encoding="utf-8"); temp.replace(folder/"status.json")
    def submit(e,raw):
        try: pending.put_nowait(e); return True
        except queue.Full: failures.append("callback_queue_full"); stop.set(); return False
    def write():
        try:
            last_save=0
            with (folder/"events.jsonl").open("x",encoding="utf-8",buffering=1) as f:
                while True:
                    e=pending.get()
                    if e is None: break
                    counts[e["kind"]]+=1
                    if e["kind"]=="tracker_phase": status["phase"]=e["phase"]
                    serial=e.get("serial")
                    if serial:
                        device=serials.setdefault(serial,dict(raw_imu=0,pose=0,optical=0))
                        if e["kind"] in device: device[e["kind"]]+=1
                        device["last_event_unix"]=time.time()
                        if device["raw_imu"]>=20 and not device["pose"]:
                            status.update(phase="tracker_imu_streaming",wireless_data_verified=True)
                        if e["kind"]=="pose":
                            device["last_pose"]=e
                            device["valid_pose_count"]=device.get("valid_pose_count",0)+int(e["valid"])
                            status["phase"]="pose_observed_unvalidated"
                    if e["kind"]=="lighthouse_pose":
                        status.setdefault("lighthouse_poses",{})[str(e["lighthouse_index"])]=e
                    if e["kind"]=="ootx_received":
                        status.setdefault("ootx_received",{})[str(e["lighthouse_index"])]=e
                    if e["kind"]=="tracker_ready": status["tracker_ready"]=e
                    # Connection keep-alive is not a full measurement recording.
                    # Retain bounded diagnostic samples; continuous raw recording
                    # belongs to an explicitly started acquisition session.
                    if mode=="pose" or counts[e["kind"]]<=1000 or e["kind"] in ("tracker_phase","tracker_disconnect","tracker_callback_error"):
                        f.write(json.dumps(e,allow_nan=False)+"\n")
                    now=time.monotonic()
                    if now-last_save>=1 or e["kind"]=="tracker_phase": save(); last_save=now
        except BaseException as exc: failures.append(repr(exc)); stop.set()
    writer=threading.Thread(target=write); writer.start()
    def watch():
        while not stop.is_set():
            if (folder/"STOP").exists() or time.monotonic()-start>seconds: stop.set(); break
            time.sleep(.1)
    threading.Thread(target=watch,daemon=True).start()
    save()
    try:
        Tracker(DLL,"./libsurvive-config.json",None,submit,pylsl.local_clock,diagnostic=mode=="diagnostic",
                receiver_serials=[RECEIVER],reviewed_sha256=DLL_SHA,verbosity=10 if mode=="pose" else 0).run(stop)
    except BaseException as exc:
        failures.append(repr(exc)); raise
    finally:
        stop.set(); pending.put(None,timeout=2); writer.join(timeout=3)
        status.update(phase="closed",failures=failures); save()


def supervise(folder,seconds=600,mode="diagnostic",seed_config=None):
    validate_session(seconds,mode)
    seed=Path(seed_config).resolve() if seed_config is not None else None
    seed_bytes=seed.read_bytes() if seed else None
    folder=Path(folder).resolve(); folder.mkdir(parents=True,exist_ok=False)
    if seed_bytes is not None:
        (folder/"libsurvive-config.json").write_bytes(seed_bytes)
        (folder/"seed-config.json").write_text(json.dumps(dict(source=str(seed),sha256=hashlib.sha256(seed_bytes).hexdigest()),indent=2),encoding="utf-8")
    with (folder/"native.log").open("w",encoding="utf-8") as log:
        child=subprocess.Popen([sys.executable,"-u","-m","lsac_capture.receiver_session","worker",str(folder),str(seconds),mode],
                               stdout=log,stderr=subprocess.STDOUT,creationflags=0x08000000)
        metadata=dict(receiver=RECEIVER,supervisor_pid=os.getpid(),worker_pid=child.pid,pairing_enabled=False,mode=mode,started_unix=time.time())
        (folder/"process.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
        start=time.monotonic(); stop_at=None
        while child.poll() is None:
            if (folder/"STOP").exists() or time.monotonic()-start>seconds:
                if stop_at is None:
                    (folder/"STOP").touch(); stop_at=time.monotonic()
                elif time.monotonic()-stop_at>5:
                    child.terminate(); child.wait(timeout=5); metadata["forced_termination"]=True; break
            time.sleep(.1)
        metadata.update(exit_code=child.returncode,ended_unix=time.time())
        (folder/"process.json").write_text(json.dumps(metadata,indent=2),encoding="utf-8")
        return metadata


if __name__=="__main__":
    if sys.argv[1]=="worker": worker(sys.argv[2],float(sys.argv[3]),sys.argv[4] if len(sys.argv)>4 else "diagnostic")
    elif sys.argv[1]=="supervise": print(json.dumps(supervise(sys.argv[2],float(sys.argv[3]),sys.argv[4] if len(sys.argv)>4 else "diagnostic",sys.argv[5] if len(sys.argv)>5 else None),indent=2))
    else: raise SystemExit("worker or supervise required")
