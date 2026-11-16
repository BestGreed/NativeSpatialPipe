"""One native context owns A/B; clients consume its spool, never reopen HID."""
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

from .receiver_session import DLL,DLL_SHA,ROOT

RECEIVERS=["4E482CC871-2YX","C0531DBB07-2YX"]
TRACKERS={"A":"LHR-C9F2A107","B":"LHR-7712DB4C"}


def atomic_json(path,data):
    path=Path(path); temp=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
    temp.write_text(json.dumps(data,indent=2,allow_nan=False),encoding="utf-8")
    # Windows readers/antivirus can briefly deny replacement of an open file.
    # Keep acquisition independent of that short telemetry contention.
    deadline=time.monotonic()+1
    while True:
        try: temp.replace(path); return
        except PermissionError:
            if time.monotonic()>=deadline: raise
            time.sleep(.01)


class EventTail:
    """Binary cursor preserves UTF-8 and partial JSON lines across writes."""
    def __init__(self,path,at_end=False):
        self.path=Path(path); self.offset=self.path.stat().st_size if at_end and self.path.exists() else 0
        self.partial=b""

    def read(self):
        if not self.path.exists(): return []
        with self.path.open("rb") as source:
            source.seek(self.offset); data=source.read(); self.offset=source.tell()
        lines=(self.partial+data).split(b"\n"); self.partial=lines.pop()
        return [json.loads(line) for line in lines if line.strip()]


def worker(folder,seconds):
    import pylsl
    from .tracking import Tracker
    folder=Path(folder).resolve(); os.chdir(folder)
    rig=json.loads((folder/"rig.json").read_text(encoding="utf-8"))
    handles=[os.add_dll_directory(str(DLL.parent)),os.add_dll_directory(str(ROOT/"work/lsac-toolchain/llvm-mingw-20261006-ucrt-x86_64/bin"))]
    stop=threading.Event(); pending=queue.Queue(16384); counts=Counter(); errors=[]
    state=dict(world_id=rig["world_id"],worker_pid=os.getpid(),phase="starting",pairing_enabled=False,
               receiver_serials=RECEIVERS,trackers={s:dict(role=r,counts={},last_pose=None) for r,s in TRACKERS.items()})
    def submit(e,raw=None):
        try: pending.put_nowait(e); return True
        except queue.Full: errors.append("callback_queue_overflow"); stop.set(); return False
    def write():
        try:
            last=0
            with (folder/"events.jsonl").open("x",encoding="utf-8",buffering=1) as target:
                while True:
                    e=pending.get()
                    if e is None: break
                    counts[e["kind"]]+=1
                    serial=e.get("serial")
                    if serial in state["trackers"]:
                        device=state["trackers"][serial]
                        device["counts"][e["kind"]]=device["counts"].get(e["kind"],0)+1
                        if e["kind"]=="pose": device["last_pose"]=e
                        if e["kind"]=="tracker_ready": device["config_ready"]=e
                    if e["kind"]=="tracker_callback_error": errors.append(e.get("error","callback_error")); stop.set()
                    target.write(json.dumps(e,allow_nan=False)+"\n")
                    now=time.monotonic()
                    if now-last>.5:
                        live=pylsl.local_clock()
                        state.update(updated_unix=time.time(),updated_lsl=live,counts=dict(counts),errors=list(errors))
                        fresh=[d["last_pose"] and d["last_pose"].get("valid") and 0<=live-d["last_pose"]["arrival_lsl"]<.5 for d in state["trackers"].values()]
                        state["phase"]="both_pose_streaming_unvalidated" if all(fresh) else "waiting_for_both_poses"
                        atomic_json(folder/"status.json",state); last=now
        except BaseException as exc: errors.append(repr(exc)); stop.set()
    writer=threading.Thread(target=write,name="rig-spool"); writer.start()
    start=time.monotonic()
    def watch():
        while not stop.wait(.05):
            if (folder/"STOP").exists() or time.monotonic()-start>=seconds: stop.set(); break
            for path in sorted((folder/"commands").glob("*.json")):
                try:
                    command=json.loads(path.read_text(encoding="utf-8"))
                    if command.get("kind")!="operator_marker" or not isinstance(command.get("label"),str):
                        raise ValueError("Only operator_marker commands supported")
                    event=dict(kind="operator_marker",label=command["label"],request_id=path.stem,
                               arrival_lsl=pylsl.local_clock(),request_unix=command.get("request_unix"),world_id=rig["world_id"])
                    submit(event); path.rename(folder/"handled"/path.name)
                except BaseException as exc: errors.append("command: "+repr(exc)); stop.set(); break
    threading.Thread(target=watch,daemon=True).start()
    atomic_json(folder/"status.json",state)
    try:
        Tracker(DLL,"./libsurvive-config.json",list(TRACKERS.values()),submit,pylsl.local_clock,
                diagnostic=True,receiver_serials=RECEIVERS,reviewed_sha256=DLL_SHA).run(stop)
    except BaseException as exc: errors.append(repr(exc)); raise
    finally:
        stop.set()
        if writer.is_alive(): pending.put(None,timeout=2)
        writer.join(timeout=5)
        state.update(phase="closed",errors=errors,counts=dict(counts),updated_unix=time.time())
        atomic_json(folder/"status.json",state)


def supervise(folder,seed_config,seconds=7200,resume_rig=None):
    if not 0<seconds<=86400: raise ValueError("Duration must be 0..86400 seconds")
    seed=Path(seed_config).resolve(); data=seed.read_bytes()
    previous=None
    if resume_rig is not None:
        previous_folder=Path(resume_rig).resolve()
        if seed!=(previous_folder/"libsurvive-config.json").resolve(): raise ValueError("Resume must use exact prior final config")
        prior_status=json.loads((previous_folder/"status.json").read_text(encoding="utf-8"))
        prior_process=json.loads((previous_folder/"process.json").read_text(encoding="utf-8"))
        if prior_status["phase"]!="closed" or prior_process.get("exit_code")!=0:
            raise ValueError("Prior rig must be confirmed normally closed before resume")
        previous=json.loads((previous_folder/"rig.json").read_text(encoding="utf-8"))
    folder=Path(folder).resolve(); folder.mkdir(parents=True,exist_ok=False)
    (folder/"commands").mkdir(); (folder/"handled").mkdir()
    (folder/"libsurvive-config.json").write_bytes(data)
    rig=dict(schema=1,world_id=previous["world_id"] if previous else "lsac-rig-"+uuid.uuid4().hex,receiver_serials=RECEIVERS,trackers=TRACKERS,
             seed_config=str(seed),seed_sha256=hashlib.sha256(data).hexdigest(),pairing_enabled=False,
             calibration_enabled=False,duration_seconds=seconds,note="Full spool; pose physical accuracy/timing unvalidated")
    if previous:
        rig.update(resumed_from=str(previous_folder),world_continuity_basis="exact_prior_saved_base_station_config; no physical base/laptop movement assumed")
    atomic_json(folder/"rig.json",rig)
    with (folder/"native.log").open("w",encoding="utf-8") as log:
        child=subprocess.Popen([sys.executable,"-u","-m","lsac_capture.rig_session","worker",str(folder),"--seconds",str(seconds)],
                               stdout=log,stderr=subprocess.STDOUT,creationflags=0x08000000)
        meta=dict(supervisor_pid=os.getpid(),worker_pid=child.pid,receiver_serials=RECEIVERS,pairing_enabled=False,started_unix=time.time())
        atomic_json(folder/"process.json",meta)
        stop_at=None; start=time.monotonic()
        while child.poll() is None:
            if (folder/"STOP").exists() or time.monotonic()-start>=seconds:
                if stop_at is None: (folder/"STOP").touch(); stop_at=time.monotonic()
                elif time.monotonic()-stop_at>6:
                    child.terminate(); child.wait(timeout=5); meta["forced_termination"]=True; break
            time.sleep(.1)
        meta.update(exit_code=child.returncode,ended_unix=time.time()); atomic_json(folder/"process.json",meta)
    return meta


def mark(folder,label):
    folder=Path(folder).resolve(); state=json.loads((folder/"status.json").read_text(encoding="utf-8"))
    if state["phase"]=="closed" or time.time()-state.get("updated_unix",0)>3: raise ValueError("Rig status is stale/closed")
    token=uuid.uuid4().hex
    path=folder/"commands"/(token+".json"); temp=path.with_suffix(".pending")
    temp.write_text(json.dumps(dict(kind="operator_marker",label=label,request_unix=time.time())),encoding="utf-8"); temp.rename(path)
    return dict(request_id=token,label=label,world_id=state["world_id"])


def main():
    p=argparse.ArgumentParser(description=__doc__); sub=p.add_subparsers(dest="cmd",required=True)
    for name in ("supervise","worker"):
        a=sub.add_parser(name); a.add_argument("folder"); a.add_argument("--seconds",type=float,default=7200)
        if name=="supervise":
            a.add_argument("--seed-config",required=True);a.add_argument("--resume-rig")
    a=sub.add_parser("mark"); a.add_argument("folder"); a.add_argument("label")
    args=p.parse_args()
    if args.cmd=="supervise": result=supervise(args.folder,args.seed_config,args.seconds,args.resume_rig)
    elif args.cmd=="worker": worker(args.folder,args.seconds); return
    else: result=mark(args.folder,args.label)
    print(json.dumps(result,indent=2))


if __name__=="__main__": main()
