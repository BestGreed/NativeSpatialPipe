"""Capture H3-VR with the existing A/B broker; never opens another HID handle."""
import argparse
from collections import Counter
import json
from pathlib import Path
import threading
import time

from .rig_session import EventTail,TRACKERS,ROOT,atomic_json

LAB=ROOT/"work/lsac-binaries/LabRecorder-1.18.0/LabRecorder-1.18.0-Win_amd64/LabRecorderCLI.exe"
DEFAULT_AUDIO=dict(endpoint_id="{0.0.1.00000000}.{324d001e-318b-4f8c-b208-5cc6a9324821}",
                   rate=48000,channels=4,container_bits=24,valid_bits=24,format="PCM",mode="exclusive",
                   channel_order_verified=False,normalization="unverified",mic_position="unchanged_not_read")


def live_state(folder,max_age=3):
    state=json.loads((Path(folder)/"status.json").read_text(encoding="utf-8"))
    if state["phase"]=="closed" or time.time()-state.get("updated_unix",0)>max_age:
        raise ValueError("Rig broker is closed/stale; do not open a second receiver")
    return state


def streams_ready(counts,device_counts,last_pose_lsl,current_lsl,required_serials=None):
    """A traffic can never satisfy B's capture readiness (or vice versa)."""
    return counts["audio_block"]>=10 and all(device_counts[(serial,"raw_imu")]>=20 and
          device_counts[(serial,"pose")]>=10 and last_pose_lsl.get(serial) is not None and
          0<=current_lsl-last_pose_lsl[serial]<.5 for serial in (required_serials or TRACKERS.values()))


def playback_outcome(playback,operator_finished):
    if playback.get('status')=='completed_not_acoustically_verified':return 'completed'
    if operator_finished and playback.get('status') in (None,'cancelled'):
        return 'operator_finished' if 'start_call_before_qpc_i64' in playback else 'operator_finished_before_playback'
    return 'incomplete'


def run(rig_folder,out,seconds=45,purpose="taps",config=None,playback_plan=None,reference_folder=None,
        confirm_playback=False,playback_delay=3.,play_on_marker=None,stop_after_playback=False):
    from .session import Session,replay_check
    from .windows import capture
    if not 0<seconds<=3600: raise ValueError("Capture duration must be 0..3600 seconds")
    if purpose not in ("taps","scan-preview","channel-check"): raise ValueError("Unknown purpose")
    if stop_after_playback and playback_plan is None:raise ValueError('Auto stop requires playback')
    if play_on_marker is not None and (not isinstance(play_on_marker,str) or not play_on_marker.strip() or playback_plan is None):
        raise ValueError("Playback trigger requires an explicit marker label and playback plan")
    if playback_plan is not None:
        if not confirm_playback: raise ValueError("Combined playback requires explicit confirmation")
        if purpose!="scan-preview" or reference_folder is None: raise ValueError("Playback requires scan-preview and an explicit reference")
        if not 0<=playback_delay<=10: raise ValueError("Playback delay must be 0..10 seconds")
        metadata=json.loads((Path(reference_folder)/"reference.json").read_text(encoding="utf-8"))
        if seconds<playback_delay+metadata["seconds"]+1: raise ValueError("Capture must cover reference playback and drain margin")
        # A long reference can hold the GIL during the final bytes conversion.
        # Finish that work before creating Session, queues or audio producers.
        from .playback import prepare_playback_reference
        print('PREPARING_REFERENCE_BEFORE_CAPTURE',flush=True)
        prepared_reference=prepare_playback_reference(playback_plan,reference_folder)
    source=Path(rig_folder).resolve(); out=Path(out).resolve()
    state=live_state(source)
    # A stays attached to the rig after its plane snapshots, but B is the only
    # required moving probe. A occlusion must not block B's time calibration.
    required_serials=[TRACKERS["B"]]
    deadline=time.monotonic()+3
    while True:
        fresh=all((p:=state["trackers"][s].get("last_pose")) and p.get("valid") and
                  0<=state["updated_lsl"]-p["arrival_lsl"]<.5 for s in required_serials)
        if fresh: break
        if time.monotonic()>=deadline: raise ValueError("Required B pose is not fresh before recording")
        time.sleep(.1); state=live_state(source)
    settings=json.loads((source/"rig.json").read_text(encoding="utf-8"))
    policy=json.loads((Path(__file__).resolve().parents[1]/"tap-policy.json").read_text(encoding="utf-8"))
    user_config=dict(config or {})
    user_config.update(schema=1,capture_purpose="audio_raw_imu_time_alignment" if purpose=="taps" else "continuous_scan_preview",
                       operation=purpose,world_id=settings["world_id"],rig_source=str(source),
                       tracking=dict(backend="libsurvive",serials=required_serials,observed_serials=list(TRACKERS.values()),pair_device=False,
                                     receiver_serials=settings["receiver_serials"],source="existing_broker_spool"),
                       alignment_tracker_serial=TRACKERS["B"],
                       tapping_plan=dict(start_count=3,end_count=3,policy=policy),
                       audio=user_config.get("audio",DEFAULT_AUDIO),
                       note="Raw channel semantics unverified; no automatic playback; no precision geometry certification")
    if playback_plan is not None:
        user_config["playback"]=dict(route_plan=playback_plan,reference_folder=str(Path(reference_folder).resolve()),
                                     reference=metadata,delay_after_ready_seconds=playback_delay,
                                     trigger_operator_marker=play_on_marker,
                                     timestamp_basis="API_QPC_not_acoustic_DAC_start")
    session=Session(out,user_config,LAB); ready=threading.Event(); trigger=threading.Event()
    counts=Counter(); device_counts=Counter(); last_pose_lsl={}; failures=[]
    # Session may spend seconds waiting for LabRecorder subscriptions. Start
    # following only now: replaying that backlog can overflow the live queue
    # before audio has even started and can falsely satisfy readiness.
    tail=EventTail(source/"events.jsonl",at_end=True)
    def submit(e,raw=None):
        counts[e["kind"]]+=1
        if e.get("serial") in TRACKERS.values() and (e["kind"]!="pose" or e.get("valid")):
            device_counts[(e["serial"],e["kind"])]+=1
        if e.get("serial") in TRACKERS.values() and e["kind"]=="pose":
            last_pose_lsl[e["serial"]]=e.get("arrival_lsl") if e.get("valid") else None
        return session.submit(e,raw)
    def guard(fn):
        def target():
            try: fn()
            except BaseException as exc: failures.append(repr(exc)); session.stop.set()
        thread=threading.Thread(target=target); thread.start(); return thread
    def forward():
        last_check=0
        while not session.stop.is_set():
            for e in tail.read():
                e["source_world_id"]=settings["world_id"]
                if play_on_marker is not None and e.get("kind")=="operator_marker" and e.get("label")==play_on_marker:
                    trigger.set()
                if not submit(e): return
            if time.monotonic()-last_check>1:
                fresh=live_state(source)
                if fresh["world_id"]!=settings["world_id"]: raise ValueError("Rig world changed during capture")
                last_check=time.monotonic()
            session.stop.wait(.005)
    tracker=guard(forward); audio=guard(lambda:capture(user_config["audio"],session.stop,submit,ready))
    started=None; playback_thread=None; playback_result={};render_stop=threading.Event()
    operator_finished=False;finish_at=None
    try:
        deadline=time.monotonic()+15
        while not session.stop.is_set() and time.monotonic()<deadline:
            if ready.is_set() and streams_ready(counts,device_counts,last_pose_lsl,session.lsl.local_clock(),required_serials): break
            time.sleep(.05)
        else: raise RuntimeError("Audio/tracker streams failed readiness: "+repr(failures))
        started=time.time(); deadline=time.monotonic()+seconds
        if playback_plan is not None:
            from .playback import play
            def render():
                while play_on_marker is not None and not trigger.wait(.05):
                    if session.stop.is_set() or render_stop.is_set(): return
                if render_stop.wait(playback_delay) or session.stop.is_set(): return
                playback_result.update(play(playback_plan,reference_folder,out/"playback",mode="shared",
                                            confirm_playback=True,stop=render_stop,submit=submit,
                                            prepared_reference=prepared_reference))
            playback_thread=guard(render)
        print("READY_FOR_USER_ACTION",flush=True)
        playback_done_at=None
        while not session.stop.is_set() and time.monotonic()<deadline and not (out/"STOP").exists():
            atomic_json(out/"live-status.json",dict(phase="ready",purpose=purpose,ready_unix=started,
                                                   elapsed_seconds=time.time()-started,counts=dict(counts),failures=failures,
                                                   playback_triggered=trigger.is_set(),playback_status=playback_result.get("status"),
                                                   operator_finishing=operator_finished))
            if (out/'FINISH.json').exists() and finish_at is None:
                request=json.loads((out/'FINISH.json').read_text(encoding='utf-8'))
                if request.get('kind')!='operator_finish':raise ValueError('Unknown finish request')
                operator_finished=True;finish_at=time.monotonic();render_stop.set()
                submit(dict(kind='operator_finish',arrival_lsl=session.lsl.local_clock(),request_unix=request.get('request_unix')))
            if finish_at is not None and time.monotonic()-finish_at>=3:break
            if stop_after_playback and playback_result.get('status')=='completed_not_acoustically_verified':
                if playback_done_at is None:playback_done_at=time.monotonic()
                if time.monotonic()-playback_done_at>=3:break
            time.sleep(.2)
    except BaseException as exc: failures.append(repr(exc)); raise
    finally:
        render_stop.set();session.stop.set(); audio.join(timeout=8); tracker.join(timeout=8)
        if playback_thread: playback_thread.join(timeout=8)
        if audio.is_alive() or tracker.is_alive(): failures.append("producer_not_stopped")
        if playback_thread and playback_thread.is_alive(): failures.append("playback_not_stopped")
        result=session.close(); result.update(capture_failures=failures,ready_unix=started,world_id=settings["world_id"])
        if playback_plan is not None:
            result["playback"]=playback_result
            outcome=playback_outcome(playback_result,operator_finished)
            result['scan_completion']=outcome
            result['operator_finished']=operator_finished
            if outcome=='incomplete':failures.append('playback_incomplete')
            if outcome=='operator_finished_before_playback' and not failures:result['status']='recorded_background_only'
        if failures: result["status"]="incomplete"
        atomic_json(out/"result.json",result)
        atomic_json(out/"live-status.json",dict(phase="closed",counts=dict(counts),failures=failures))
    result["replay"]=replay_check(out)
    if result["replay"]["errors"]: result["status"]="incomplete"
    atomic_json(out/"result.json",result)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("rig_folder"); p.add_argument("output")
    p.add_argument("--seconds",type=float,default=45); p.add_argument("--purpose",choices=("taps","scan-preview","channel-check"),default="taps")
    p.add_argument("--config")
    p.add_argument("--playback-plan");p.add_argument("--reference-folder")
    p.add_argument("--confirm-playback",action="store_true");p.add_argument("--playback-delay",type=float,default=3.)
    p.add_argument("--play-on-marker",help="Wait for this live rig operator marker; permits initial/end tap groups in one recording")
    p.add_argument('--stop-after-playback',action='store_true',help='Finish capture three seconds after normal playback completion')
    a=p.parse_args(); config=json.loads(Path(a.config).read_text(encoding="utf-8")) if a.config else None
    plan=json.loads(Path(a.playback_plan).read_text(encoding="utf-8")) if a.playback_plan else None
    print(json.dumps(run(a.rig_folder,a.output,a.seconds,a.purpose,config,plan,a.reference_folder,
                         a.confirm_playback,a.playback_delay,a.play_on_marker,a.stop_after_playback),indent=2))


if __name__=="__main__": main()
