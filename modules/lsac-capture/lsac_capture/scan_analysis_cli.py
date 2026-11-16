"""Standalone offline CLI. Importing or running never opens audio/HID devices."""
import argparse
import json
from pathlib import Path

from .scan_analysis import analyze_session, write_outputs, pose_validation, pose_imu_clock_evidence, B_SERIAL


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    commands=parser.add_subparsers(dest="command",required=True)
    scan=commands.add_parser("scan")
    scan.add_argument("session")
    scan.add_argument("output",help="New directory; existing outputs are never overwritten")
    scan.add_argument("--calibration")
    scan.add_argument("--mode",choices=("aligned","arrival-preview"),default="aligned")
    scan.add_argument("--audio-time-basis",choices=("packet-qpc","nominal-sampleclock"),default="packet-qpc",
                      help="nominal-sampleclock is a derived, unvalidated analysis axis for arrival-preview only")
    scan.add_argument("--window-seconds",type=float,default=.1)
    scan.add_argument("--max-pose-gap",type=float,default=.025)
    scan.add_argument("--serial",default=B_SERIAL)
    scan.add_argument('--playback-only',action='store_true',help='Only window centres inside one recorded playback; relative levels use this selection')
    scan.add_argument('--playback-index',type=int,help='Zero-based explicit choice if several playback intervals exist')
    scan.add_argument('--playback-edge-seconds',type=float,default=.1,help='Exclude this duration at each playback boundary (default .1)')
    pose=commands.add_parser("pose-check")
    pose.add_argument("events")
    pose.add_argument("output",help="New JSON file")
    pose.add_argument("--stages",help="JSON list of label/start_lsl/end_lsl objects; otherwise operator_marker events")
    pose.add_argument("--serial",default=B_SERIAL)
    args=parser.parse_args()
    if args.command=="scan":
        result=analyze_session(args.session,args.calibration,mode=args.mode,window_seconds=args.window_seconds,
                               max_pose_gap=args.max_pose_gap,serial=args.serial,audio_time_basis=args.audio_time_basis,
                               playback_only=args.playback_only,playback_index=args.playback_index,
                               playback_edge_seconds=args.playback_edge_seconds)
        paths=write_outputs(result,args.output)
        print(json.dumps(dict(mode=result["mode"],accepted_window_count=result["accepted_window_count"],
                              rejections=result["rejections"],outputs=paths),indent=2))
    else:
        events=[json.loads(line) for line in Path(args.events).read_text(encoding="utf-8").splitlines() if line]
        stages=json.loads(Path(args.stages).read_text(encoding="utf-8")) if args.stages else None
        result=dict(pose_validation=pose_validation(events,args.serial,stages),
                    pose_imu_clock_evidence=pose_imu_clock_evidence(events,args.serial))
        with Path(args.output).open("x",encoding="utf-8") as source:
            json.dump(result,source,indent=2,allow_nan=False)
        print(json.dumps(result,indent=2))


if __name__=="__main__": main()
