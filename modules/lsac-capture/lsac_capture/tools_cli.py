"""Unified local LSAC toolkit. Commands do not imply hardware verification."""
import importlib
import json
from pathlib import Path
import sys

TOOLS={"rig":"rig_session","capture":"rig_capture","calibration":"calibration_cli",
       "playback":"playback_cli","analysis":"scan_analysis_cli","triplet":"tap_triplet","levels":"audio_levels",
       "tap-marked-groups":"tap_groups","preview":"scan_preview",
       "field-model":"field_model","field-view":"field_viewer","source-inversion":"source_inversion",
       "direction-check":"direction_validation","multisource-fit":"multisource_model","coverage":"coverage",
       "sphere-scan":"sphere_scan"}


def bundle(screen_path,heading_path,output):
    import hashlib
    screen=json.loads(Path(screen_path).read_text(encoding="utf-8"))
    heading=json.loads(Path(heading_path).read_text(encoding="utf-8"))
    if screen.get("kind")!="screen_reference_calibration" or heading.get("kind")!="probe_heading_calibration":
        raise ValueError("Explicit plane and B heading calibration artifacts required")
    if not screen.get("world_id") or screen["world_id"]!=heading.get("world_id"):
        raise ValueError("Calibration worlds differ")
    result=dict(schema=1,kind="lsac_calibration_bundle",world_id=screen["world_id"],
                screen_world_pose=screen["screen_world_pose"],reference_world_pose=screen["reference_world_pose"],
                probe=heading["probe"],plane_calibration=screen,heading_calibration=heading,
                pose_latency_validated=False,
                sources={str(Path(path).resolve()):hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in (screen_path,heading_path)})
    with Path(output).open("x",encoding="utf-8") as target:json.dump(result,target,indent=2,ensure_ascii=False)
    return result


def main():
    args=sys.argv[1:]
    if not args or args[0] in ("-h","--help"):
        print("LSAC local toolkit: rig | capture | calibration | playback | analysis | preview | field-model | field-view | source-inversion | multisource-fit | direction-check | triplet | levels | tap-groups | tap-marked-groups | coverage | sphere-scan | status | bundle")
        print("Use: Tools.ps1 <tool> --help. Shared rig owns A/B; capture consumes its spool.")
        print("bundle <planes.json> <heading.json> <new-output.json>; status <rig-folder>")
        return
    command=args.pop(0)
    if command=="status":
        from .rig_capture import live_state
        print(json.dumps(live_state(args[0]),indent=2,ensure_ascii=False)); return
    if command=="bundle":
        if len(args)!=3:raise ValueError("bundle requires planes heading new-output")
        print(json.dumps(bundle(*args),indent=2,ensure_ascii=False));return
    if command=="tap-groups":
        if len(args)!=1:raise ValueError("tap-groups requires one recorded session folder")
        from .tap_analysis import analyze
        analyze(args[0]);return
    if command not in TOOLS:raise ValueError("Unknown toolkit command: "+command)
    module=importlib.import_module("lsac_capture."+TOOLS[command])
    sys.argv=["lsac-tools "+command,*args]
    module.main()


if __name__=="__main__":main()
