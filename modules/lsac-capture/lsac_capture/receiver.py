"""One-receiver Watchman pairing plan and guarded one-shot request.

No libsurvive/HMD startup. Plan only reads OS HID attributes/caps. Actual send
is deliberately a separate, confirmed command after one physical tracker is
put in pairing mode. Pairing success requires subsequent identity validation.
Protocol source: audited libsurvive driver_vive.c (MIT), FF AD 03 01 10 27.
"""
import ctypes as C
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time

from .windows import hid_inventory,PTR,DWORD

PAIR_PREFIX=bytes.fromhex("ff ad 03 01 10 27")


class Caps(C.Structure):
    _fields_=[("usage",C.c_ushort),("usage_page",C.c_ushort),
              ("input_bytes",C.c_ushort),("output_bytes",C.c_ushort),("feature_bytes",C.c_ushort),
              ("reserved",C.c_ushort*17),*( (name,C.c_ushort) for name in (
                  "links","input_buttons","input_values","input_indices","output_buttons","output_values",
                  "output_indices","feature_buttons","feature_values","feature_indices"))]


def api():
    k=C.WinDLL("kernel32",use_last_error=True); h=C.WinDLL("hid",use_last_error=True)
    k.CreateFileW.argtypes=[C.c_wchar_p,DWORD,DWORD,PTR,DWORD,DWORD,PTR]; k.CreateFileW.restype=PTR
    k.CloseHandle.argtypes=[PTR]
    h.HidD_GetPreparsedData.argtypes=[PTR,C.POINTER(PTR)]
    h.HidD_FreePreparsedData.argtypes=[PTR]
    h.HidP_GetCaps.argtypes=[PTR,C.POINTER(Caps)]; h.HidP_GetCaps.restype=C.c_int32
    h.HidD_SetFeature.argtypes=[PTR,PTR,DWORD]; h.HidD_SetFeature.restype=C.c_ubyte
    h.HidD_GetSerialNumberString.argtypes=[PTR,PTR,DWORD]
    h.HidD_GetAttributes.argtypes=[PTR,PTR]
    return k,h


def open_handle(k,path,access=0):
    f=k.CreateFileW(path,access,3,None,3,0,None)
    if f==PTR(-1).value: raise C.WinError(C.get_last_error())
    return f


def get_caps(path):
    k,h=api(); f=open_handle(k,path); data=PTR()
    try:
        if not h.HidD_GetPreparsedData(f,C.byref(data)): raise C.WinError(C.get_last_error())
        caps=Caps()
        if h.HidP_GetCaps(data,C.byref(caps))<0: raise RuntimeError("HID descriptor capability query failed")
        return {name:getattr(caps,name) for name in ("usage","usage_page","input_bytes","output_bytes","feature_bytes")}
    finally:
        if data: h.HidD_FreePreparsedData(data)
        k.CloseHandle(f)


def choose(rows,serial):
    selected=[r for r in rows if r.get("vid")=="0x28de" and r.get("pid")=="0x2102" and r.get("serial")==serial]
    if len(selected)!=1: raise ValueError("Need exactly one 28DE:2102 HID path with the full selected receiver serial")
    return selected[0]


def packet(feature_bytes):
    if not 6 <= feature_bytes <= 4096: raise ValueError("Unusable feature report length")
    return PAIR_PREFIX+bytes(feature_bytes-len(PAIR_PREFIX))


def plan(serial):
    selected=choose(hid_inventory(("vid_28de&pid_2102",)),serial); caps=get_caps(selected["interface_path"])
    payload=packet(caps["feature_bytes"])
    result=dict(schema=1,operation="one_shot_watchman_pair_request",receiver=selected,caps=caps,
                report_prefix_hex=PAIR_PREFIX.hex(" "),report_bytes=len(payload),reports_to_send=1,
                exclude_other_receivers=True,hmd_2300_opened=False,sent=False,
                caveat="Vendor protocol request derived from libsurvive; descriptor/target validated but pairing not yet hardware tested")
    result["target_token"]=token(result)
    return result


def token(p):
    return hashlib.sha256(json.dumps(dict(receiver=p["receiver"],caps=p["caps"],report_prefix_hex=p["report_prefix_hex"]),sort_keys=True).encode()).hexdigest()


def send_worker(attempt):
    path=Path(attempt); request=json.loads(path.read_text(encoding="utf-8")); selected=request["plan"]
    current=plan(selected["receiver"]["serial"])
    if current["target_token"]!=selected["target_token"]: raise ValueError("Receiver identity/path/caps changed; re-plan")
    k,h=api(); f=open_handle(k,current["receiver"]["interface_path"],0xc0000000)
    try:
        # Recheck serial on the SAME handle used for the report (no mapper).
        serial=C.create_unicode_buffer(256)
        if not h.HidD_GetSerialNumberString(f,serial,C.sizeof(serial)) or serial.value!=selected["receiver"]["serial"]:
            raise ValueError("Handle serial mismatch")
        raw=packet(current["caps"]["feature_bytes"]); buffer=C.create_string_buffer(raw,len(raw))
        request["status"]="send_entered_outcome_pending"
        path.write_text(json.dumps(request,indent=2),encoding="utf-8")
        ok=bool(h.HidD_SetFeature(f,buffer,len(raw)))
        request.update(status="request_accepted_not_pairing_verified" if ok else "feature_report_failed",
                       winerror=0 if ok else C.get_last_error(),pairing_verified=False)
        path.write_text(json.dumps(request,indent=2),encoding="utf-8")
        if not ok: raise C.WinError(request["winerror"])
    finally: k.CloseHandle(f)


def pair(plan_path,attempt_path,confirmed=False):
    if not confirmed: raise ValueError("Need explicit confirmation for this single receiver and one physical tracker")
    p=json.loads(Path(plan_path).read_text(encoding="utf-8"))
    if p.get("target_token")!=token(p): raise ValueError("Invalid plan token")
    path=Path(attempt_path).resolve()
    with path.open("x",encoding="utf-8") as f:
        json.dump(dict(plan=p,status="prepared_not_sent",created_unix=time.time(),pairing_verified=False),f,indent=2)
    # One child, one write, bounded wait. Existing attempts may not be retried.
    child=subprocess.Popen([sys.executable,"-m","lsac_capture.receiver","_send",str(path)],creationflags=0x08000000)
    try: result=child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.terminate(); child.wait(timeout=3)
        raise RuntimeError("Pair request timed out: outcome uncertain; inspect saved attempt and physical connection before retry")
    if result: raise RuntimeError("Pair request child failed; inspect saved attempt, do not auto-retry")
    return json.loads(path.read_text(encoding="utf-8"))


def observe_worker(plan_path,output,seconds=2):
    """Read input reports only from one receiver; no feature writes or lib init."""
    selected=json.loads(Path(plan_path).read_text(encoding="utf-8"))
    current=plan(selected["receiver"]["serial"])
    if current["target_token"]!=selected["target_token"]: raise ValueError("Receiver changed")
    k,h=api()
    class Overlapped(C.Structure):
        _fields_=[("internal",C.c_size_t),("internal_high",C.c_size_t),("offset",DWORD),("offset_high",DWORD),("event",PTR)]
    k.CreateEventW.argtypes=[PTR,C.c_int,C.c_int,C.c_wchar_p]; k.CreateEventW.restype=PTR
    k.ResetEvent.argtypes=[PTR]
    k.WaitForSingleObject.argtypes=[PTR,DWORD]
    k.ReadFile.argtypes=[PTR,PTR,DWORD,C.POINTER(DWORD),C.POINTER(Overlapped)]
    k.GetOverlappedResult.argtypes=[PTR,C.POINTER(Overlapped),C.POINTER(DWORD),C.c_int]
    k.CancelIoEx.argtypes=[PTR,C.POINTER(Overlapped)]
    f=k.CreateFileW(current["receiver"]["interface_path"],0x80000000,3,None,3,0x40000000,None)
    if f==PTR(-1).value: raise C.WinError(C.get_last_error())
    event=k.CreateEventW(None,1,0,None)
    if not event:
        k.CloseHandle(f); raise C.WinError(C.get_last_error())
    reports=[]; timeout_count=0; deadline=time.monotonic()+seconds
    try:
        serial=C.create_unicode_buffer(256)
        if not h.HidD_GetSerialNumberString(f,serial,C.sizeof(serial)) or serial.value!=selected["receiver"]["serial"]:
            raise ValueError("Read handle serial mismatch")
        while time.monotonic()<deadline and len(reports)<256:
            k.ResetEvent(event); overlapped=Overlapped(); overlapped.event=event
            buf=C.create_string_buffer(current["caps"]["input_bytes"]); read=DWORD()
            ok=k.ReadFile(f,buf,len(buf),C.byref(read),C.byref(overlapped))
            if not ok and C.get_last_error()!=997: raise C.WinError(C.get_last_error())
            waited=k.WaitForSingleObject(event,200)
            if waited==258:
                timeout_count+=1; k.CancelIoEx(f,C.byref(overlapped))
                if k.WaitForSingleObject(event,500)!=0: raise TimeoutError("Input cancellation not completed")
                k.GetOverlappedResult(f,C.byref(overlapped),C.byref(read),0)
                continue
            if waited!=0: raise C.WinError(C.get_last_error())
            if not k.GetOverlappedResult(f,C.byref(overlapped),C.byref(read),0): raise C.WinError(C.get_last_error())
            raw=buf.raw[:read.value]
            reports.append(dict(host_perf_counter_ns=time.perf_counter_ns(),bytes=len(raw),report_id=raw[0] if raw else None,hex=raw.hex()))
    finally:
        k.CloseHandle(event); k.CloseHandle(f)
    result=dict(receiver_serial=serial.value,feature_writes=0,normal_startup_performed=False,
                reports=reports,report_count=len(reports),read_timeouts=timeout_count,
                tracker_serial=None,pairing_verified=False,
                caveat="Raw input alone is not a decoded tracker identity or optical-pose verification")
    Path(output).write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def observe(plan_path,output):
    out=Path(output).resolve()
    if out.exists(): raise FileExistsError(out)
    child=subprocess.Popen([sys.executable,"-m","lsac_capture.receiver","_observe",str(Path(plan_path).resolve()),str(out)],creationflags=0x08000000)
    try: status=child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.terminate(); child.wait(timeout=3)
        raise RuntimeError("Read-only receiver observer timed out; no feature writes, do not infer pairing")
    if status: raise RuntimeError("Receiver input observation failed")
    return json.loads(out.read_text(encoding="utf-8"))


if __name__=="__main__":
    if len(sys.argv)==3 and sys.argv[1]=="_send": send_worker(sys.argv[2])
    elif len(sys.argv)==4 and sys.argv[1]=="_observe": observe_worker(sys.argv[2],sys.argv[3])
    else: raise SystemExit("Use receiver-plan / receiver-pair via lsac_capture")
