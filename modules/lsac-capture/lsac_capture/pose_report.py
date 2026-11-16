"""Offline evidence report; does not open hardware or certify pose accuracy."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys


def report(folder):
    folder=Path(folder)
    counts=Counter(); coverage=defaultdict(Counter); poses=[]; identities=[]
    with (folder/"events.jsonl").open(encoding="utf-8") as source:
        for line in source:
            e=json.loads(line); counts[e["kind"]]+=1
            if e["kind"] in ("tracker_identity","tracker_ready"): identities.append(e)
            if e["kind"]=="pose": poses.append(e)
            if e["kind"]=="optical":
                args=e["decoded_event_args"]
                if len(args)==6:  # gen1 angle: sensor, acode, tick, length, angle, LH index
                    sensor,acode,_,_,_,lh=args
                    coverage[f"lh{lh}_axis{acode & 1}"][sensor]+=1
    intervals=[b["arrival_lsl"]-a["arrival_lsl"] for a,b in zip(poses,poses[1:])
               if a["serial"]==b["serial"] and a["epoch"]==b["epoch"]]
    tick_backsteps=sum(b["pose_timecode_u64"]<a["pose_timecode_u64"] for a,b in zip(poses,poses[1:])
                       if a["serial"]==b["serial"] and a["epoch"]==b["epoch"])
    native=(folder/"native.log").read_text(encoding="utf-8",errors="replace")
    result=dict(counts=dict(counts),identities=identities,
                gen1_optical_coverage={key:dict(event_count=sum(value.values()),sensor_count=len(value),
                                              sensor_events=dict(value)) for key,value in sorted(coverage.items())},
                valid_pose_count=sum(bool(e["valid"]) for e in poses),
                max_pose_arrival_gap_seconds=max(intervals,default=None),
                pose_tick_backsteps=tick_backsteps,
                pose_span_seconds=poses[-1]["arrival_lsl"]-poses[0]["arrival_lsl"] if poses else None,
                ootx_bad_sync_messages=native.count("OOTX Decoder: Bad sync bit"),
                status="pose_observed_requires_physical_validation" if poses else "no_pose",
                limitations=["Optical coverage is aggregate, not simultaneous solver input.",
                             "Pose validity checks finite values, unit quaternion and recent optical only.",
                             "No static precision, return accuracy, recovery or audio-pose timing certification."])
    return result


if __name__=="__main__":
    result=report(sys.argv[1])
    (Path(sys.argv[1])/"pose-report.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
