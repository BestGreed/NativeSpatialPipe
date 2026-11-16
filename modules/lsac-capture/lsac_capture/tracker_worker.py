"""Native libsurvive isolation: callbacks -> bounded queue -> spool writer.

Parent supervises only this child; native startup/close cannot hang the audio
process indefinitely. The spool retains raw arrival timestamps before LSL.
"""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time


def worker(folder,dll,serials,diagnostic,receiver_serials=None,reviewed_sha256=None):
    import pylsl
    from .tracking import Tracker
    folder=Path(folder).resolve(); os.chdir(folder)
    directory_handles=[]
    if reviewed_sha256:
        root=Path(__file__).resolve().parents[3]
        directory_handles=[os.add_dll_directory(str(Path(dll).parent)),os.add_dll_directory(str(root/"work/lsac-toolchain/llvm-mingw-20261006-ucrt-x86_64/bin"))]
    pending=queue.Queue(4096); stop=threading.Event(); dropped=[0]
    def submit(event,raw):
        try: pending.put_nowait(event); return True
        except queue.Full: dropped[0]+=1; stop.set(); return False
    def write():
        with (folder/"tracker-events.jsonl").open("x",encoding="utf-8",buffering=1) as f:
            while True:
                e=pending.get()
                if e is None: break
                f.write(json.dumps(e,allow_nan=False)+"\n")
    writer=threading.Thread(target=write,name="tracker-spool"); writer.start()
    def watch():
        while not stop.is_set():
            if (folder/"STOP").exists(): stop.set(); break
            time.sleep(.02)
    watcher=threading.Thread(target=watch,daemon=True); watcher.start()
    try:
        Tracker(dll,"./libsurvive-config.json",serials,submit,pylsl.local_clock,diagnostic=diagnostic,
                receiver_serials=receiver_serials,reviewed_sha256=reviewed_sha256).run(stop)
    finally:
        stop.set(); pending.put(None,timeout=2); writer.join(timeout=3)
        (folder/"worker-result.json").write_text(json.dumps(dict(dropped=dropped[0])),encoding="utf-8")


def supervised(folder,dll,serials,diagnostic,stop,submit,seconds,receiver_serials=None,reviewed_sha256=None):
    folder=Path(folder).resolve(); folder.mkdir(parents=True,exist_ok=False)
    settings=folder/"worker-config.json"
    settings.write_text(json.dumps(dict(folder=str(folder),dll=str(Path(dll).resolve()),serials=serials,diagnostic=diagnostic,
                                       receiver_serials=receiver_serials,reviewed_sha256=reviewed_sha256)),encoding="utf-8")
    log=(folder/"native.log").open("w",encoding="utf-8")
    child=subprocess.Popen([sys.executable,"-m","lsac_capture.tracker_worker",str(settings)],
                           stdout=log,stderr=subprocess.STDOUT,creationflags=0x08000000)
    deadline=time.monotonic()+seconds; cursor=0; partial=""; forced=False
    def drain():
        nonlocal cursor,partial
        path=folder/"tracker-events.jsonl"
        if path.exists():
            with path.open("r",encoding="utf-8") as f:
                f.seek(cursor); content=f.read(); cursor=f.tell()
            lines=(partial+content).split("\n"); partial=lines.pop()
            for line in lines:
                if line: submit(json.loads(line),None)
    try:
        while child.poll() is None and not stop.is_set() and time.monotonic()<deadline:
            drain(); time.sleep(.02)
        (folder/"STOP").write_text("stop only this session worker",encoding="utf-8")
        try: child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.terminate(); child.wait(timeout=5); forced=True
        drain()
    finally:
        if child.poll() is None:
            child.terminate(); child.wait(timeout=5); forced=True
        log.close()
        result=dict(child_pid=child.pid,exit_code=child.returncode,forced_termination=forced,
                    pairing_enabled=False,diagnostic=diagnostic)
        (folder/"supervisor-result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    if forced or child.returncode:
        raise RuntimeError("libsurvive worker incomplete; native logs/raw spool retained: "+str(folder))
    return result


if __name__=="__main__":
    settings=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    worker(**settings)
