"""LSL + upstream LabRecorder XDF integration; disk/analysis off capture threads."""
from array import array
from collections import Counter
import json
import queue
from pathlib import Path
import subprocess
import threading
import time
import uuid

from .core import first_to_last


def unpack24(raw,container_bits):
    import numpy as np
    if container_bits==32:
        return (np.frombuffer(raw,dtype="<i4") >> 8).reshape(-1,4).copy()
    b=np.frombuffer(raw,dtype=np.uint8).reshape(-1,3).astype(np.int32)
    x=b[:,0] | (b[:,1]<<8) | (b[:,2]<<16)
    return ((x ^ 0x800000)-0x800000).reshape(-1,4)


class Session:
    def __init__(self,out,config,labrecorder):
        import pylsl
        from .windows import qpc,qpf
        self.lsl=pylsl; self.out=Path(out); self.out.mkdir(parents=True,exist_ok=False)
        self.id="lsac-"+uuid.uuid4().hex
        # Capacity absorbs short scheduling stalls; the writer still drains
        # immediately. Full queues remain a fatal, recorded acquisition error.
        self.stop=threading.Event(); self.failed=None; self.dropped=0; self.items=queue.Queue(4096)
        self.queue_max_observed=0
        self.config=config; self.labrecorder=Path(labrecorder)
        if not self.labrecorder.is_file(): raise FileNotFoundError(self.labrecorder)
        # Anchor QPC and LSL on the same thread; retain sandwich uncertainty.
        before=qpc(); local=pylsl.local_clock(); after=qpc(); frequency=qpf()
        self.anchor=dict(qpc_midpoint=(before+after)/2,qpc_frequency=frequency,
                         lsl=local,sandwich_seconds=(after-before)/frequency)
        (self.out/"session.json").write_text(json.dumps(dict(schema=1,id=self.id,config=config,clock_anchor=self.anchor,
              status="recording",synthetic=bool(config.get("synthetic"))),indent=2),encoding="utf-8")
        info=pylsl.StreamInfo(self.id+"-audio","Audio",4,48000,"int32",self.id+"-audio")
        desc=info.desc(); desc.append_child_value("encoding","signed_24bit_values_in_int32_no_downmix")
        desc.append_child_value("config_json",json.dumps(config)); desc.append_child_value("clock_anchor_json",json.dumps(self.anchor))
        channels=desc.append_child("channels")
        labels=("W","Y","Z","X") if config.get("audio",{}).get("channel_order_verified") else ("USB_ch1","USB_ch2","USB_ch3","USB_ch4")
        for label in labels:
            c=channels.append_child("channel"); c.append_child_value("label",label); c.append_child_value("unit","ADC_count")
        self.audio=pylsl.StreamOutlet(info,chunk_size=0,max_buffered=60)
        info=pylsl.StreamInfo(self.id+"-events","LSAC_Metadata",1,0,"string",self.id+"-events")
        info.desc().append_child_value("timestamp_policy","raw timestamps inside JSON; LSL pose/IMU timestamps are arrival only until calibrated")
        self.events=pylsl.StreamOutlet(info,max_buffered=60)
        self.log=(self.out/"labrecorder.log").open("w",encoding="utf-8")
        query=f"source_id='{self.id}-audio' or source_id='{self.id}-events'"
        self.recorder=subprocess.Popen([str(self.labrecorder),str((self.out/"raw.xdf").resolve()),query],
                         stdin=subprocess.PIPE,stdout=self.log,stderr=subprocess.STDOUT,creationflags=0x08000000)
        deadline=time.monotonic()+15
        while not (self.audio.have_consumers() and self.events.have_consumers()):
            if self.recorder.poll() is not None or time.monotonic()>deadline:
                self.finish_recorder(); raise RuntimeError("LabRecorder did not subscribe; see labrecorder.log")
            time.sleep(.05)
        self.worker=threading.Thread(target=self.write_loop,name="lsac-writer")
        self.worker.start()

    def submit(self,event,raw=None):
        event=dict(event)
        event.setdefault("arrival_lsl",self.lsl.local_clock())
        try:
            self.items.put_nowait((event,raw))
            self.queue_max_observed=max(self.queue_max_observed,self.items.qsize())
            return True
        except queue.Full:
            self.dropped+=1; self.stop.set(); return False

    def first_lsl(self,event):
        a=self.anchor
        return a["lsl"]+(event["device_qpc_100ns_u64"]/1e7-a["qpc_midpoint"]/a["qpc_frequency"])

    def write_loop(self):
        counts=Counter(); audio_bytes=0
        try:
            with (self.out/"events.jsonl").open("x",encoding="utf-8") as events, (self.out/"audio.pcm").open("xb") as pcm:
                while True:
                    item=self.items.get()
                    if item is None: break
                    e,raw=item; counts[e["kind"]]+=1
                    e["processed_lsl"]=self.lsl.local_clock()
                    if raw is not None:
                        e["pcm_byte_offset"]=audio_bytes; pcm.write(raw); audio_bytes+=len(raw)
                        if e.get("timestamp_error"):
                            first=e["arrival_lsl"]-(e["frames"]-1)/e["rate"]
                            e["timestamp_basis"]="arrival_fallback_invalid_hardware_timestamp"
                        else:
                            first=self.first_lsl(e); e["timestamp_basis"]="wasapi_first_frame_qpc_mapped_to_lsl"
                        e["lsl_first_frame"]=first
                        e["lsl_last_frame"]=first_to_last(first,e["frames"],e["rate"])
                        self.audio.push_chunk(unpack24(raw,e["container_bits"]),timestamp=e["lsl_last_frame"])
                    encoded=json.dumps(e,ensure_ascii=False,allow_nan=False)
                    events.write(encoded+"\n"); self.events.push_sample([encoded],timestamp=e["arrival_lsl"])
        except BaseException as exc:
            self.failed=repr(exc); self.stop.set()
        finally:
            self.counts=dict(counts); self.audio_bytes=audio_bytes

    def finish_recorder(self):
        if self.recorder.poll() is None:
            self.recorder.stdin.write(b"\n"); self.recorder.stdin.flush()
            try: self.recorder.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.recorder.terminate(); self.recorder.wait(timeout=5)
                self.failed="LabRecorder graceful shutdown timed out; XDF may be incomplete"
        self.log.close()

    def close(self):
        self.stop.set()
        if self.worker.is_alive():
            # Drain after producers stopped; wait here is never on capture threads.
            self.items.put(None,timeout=5); self.worker.join(timeout=20)
        if self.worker.is_alive(): self.failed="Writer did not stop"
        # Upstream CLI polls chunks every 500 ms and does not drain after its
        # shutdown flag. Keep outlets alive for >2 polling intervals; replay
        # subsequently verifies all samples/events rather than assuming flush.
        time.sleep(1.1); self.finish_recorder()
        result=dict(status="incomplete" if self.failed or self.dropped or self.recorder.returncode else "recorded_not_calibrated",
                    failed=self.failed,dropped_queue_items=self.dropped,counts=getattr(self,"counts",{}),
                    writer_queue_capacity=self.items.maxsize,writer_queue_max_observed=self.queue_max_observed,
                    audio_bytes=getattr(self,"audio_bytes",0),recorder_exit=self.recorder.returncode)
        (self.out/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
        return result


def replay_check(folder):
    """Raw sidecars are authoritative for gaps; XDF loaded without dejitter/sync."""
    import pyxdf
    p=Path(folder); records=[json.loads(s) for s in (p/"events.jsonl").read_text(encoding="utf-8").splitlines()]
    blocks=[r for r in records if r["kind"]=="audio_block"]
    errors=[]; expected=0
    for b in blocks:
        if b["sample_start_i64"]!=expected: errors.append("logical_audio_gap")
        expected=b["sample_start_i64"]+b["frames"]
        if b.get("device_gap_frames") or b.get("discontinuity") or b.get("timestamp_error"):
            errors.append("hardware_audio_gap_or_timestamp_flag")
    streams,header=pyxdf.load_xdf(str(p/"raw.xdf"),synchronize_clocks=False,dejitter_timestamps=False)
    audio=[s for s in streams if s["info"]["type"]==["Audio"]]
    if len(audio)!=1 or len(audio[0]["time_stamps"])!=sum(b["frames"] for b in blocks): errors.append("xdf_audio_count_mismatch")
    else:
        import numpy as np
        raw=(p/"audio.pcm").read_bytes()
        if blocks and not np.array_equal(audio[0]["time_series"],unpack24(raw,blocks[0]["container_bits"])):
            errors.append("xdf_audio_samples_not_bit_exact")
    metadata=[s for s in streams if s["info"]["type"]==["LSAC_Metadata"]]
    if len(metadata)!=1 or len(metadata[0]["time_stamps"])!=len(records): errors.append("xdf_event_count_mismatch")
    by_serial={}
    for e in records:
        if e["kind"] in ("pose","raw_imu"):
            by_serial.setdefault(e["serial"],Counter())[e["kind"]]+=1
    cfg=json.loads((p/"session.json").read_text())["config"]
    synthetic=bool(cfg.get("synthetic"))
    if not synthetic:
        if cfg.get("diagnostic_audio_only"): errors.append("diagnostic_audio_only_no_pose_or_imu")
        for s in cfg["tracking"]["serials"]:
            required=("raw_imu",) if cfg.get("capture_purpose")=="audio_raw_imu_time_alignment" else ("pose","raw_imu")
            if any(not by_serial.get(s,{}).get(kind) for kind in required): errors.append("missing_tracker_stream:"+s)
    return dict(synthetic=synthetic,audio_frames=sum(b["frames"] for b in blocks),audio_blocks=len(blocks),
                xdf_streams=len(streams),tracker_counts=by_serial,errors=errors,
                timestamp_processing="raw; synchronize_clocks=False; dejitter_timestamps=False",
                acoustic_calibrated=False)


def associate(folder,start,end,max_pose_gap=.1):
    """Return raw poses/IMU intersecting an audio window; never interpolate silently."""
    p=Path(folder); events=[json.loads(s) for s in (p/"events.jsonl").read_text(encoding="utf-8").splitlines()]
    blocks=[b for b in events if b["kind"]=="audio_block" and b["sample_start_i64"]<end and b["sample_start_i64"]+b["frames"]>start]
    if not blocks: raise ValueError("No audio in requested window")
    lo=blocks[0]["lsl_first_frame"]+(max(start,blocks[0]["sample_start_i64"])-blocks[0]["sample_start_i64"])/48000
    hi=blocks[-1]["lsl_last_frame"]
    selected=[e for e in events if e["kind"] in ("pose","raw_imu") and lo-max_pose_gap <= e["arrival_lsl"] <= hi+max_pose_gap]
    return dict(start_sample=start,end_sample=end,audio_blocks=blocks,tracker_events=selected,
                calibration_status="arrival_time_association_only_pending_audio_imu_and_pose_alignment",
                valid_for_precision_geometry=False)
