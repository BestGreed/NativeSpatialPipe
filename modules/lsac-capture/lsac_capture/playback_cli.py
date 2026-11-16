"""Standalone reference tool; run python -m lsac_capture.playback_cli --help."""
import argparse
import json
from pathlib import Path
from .playback import inventory_render, probe_render, prepare_reference, play


def main():
    parser=argparse.ArgumentParser(description="Explicit endpoint playback research. No default changes.")
    sub=parser.add_subparsers(dest="command",required=True)
    sub.add_parser("inventory")
    p=sub.add_parser("probe"); p.add_argument("endpoint_id"); p.add_argument("output_json")
    p=sub.add_parser("prepare"); p.add_argument("output"); p.add_argument("--route-plan")
    p.add_argument("--rate",type=int,default=48000); p.add_argument("--channels",type=int,default=2)
    p.add_argument("--seconds",type=float,default=10); p.add_argument("--peak-dbfs",type=float,default=-60)
    p.add_argument("--seed",type=int,default=20261101); p.add_argument("--center-hz",type=float,default=500)
    p.add_argument("--channel-gains",type=float,nargs='+',help="Fixed per-channel gains 0..1, e.g. 1 0 for first stereo channel only")
    p.add_argument('--band-octaves',type=float,default=1/3,help='Bandwidth in octaves, e.g. 1 for a full octave')
    p=sub.add_parser("play"); p.add_argument("route_plan"); p.add_argument("reference_folder"); p.add_argument("output")
    p.add_argument("--mode",choices=("shared","exclusive"),default="shared")
    p.add_argument("--confirm-playback",action="store_true")
    a=parser.parse_args()
    if a.command=="inventory": result=inventory_render()
    elif a.command=="probe":
        # Refuse to overwrite a reviewed plan before activating an audio client.
        with Path(a.output_json).open("x",encoding="utf-8") as f:
            result=probe_render(a.endpoint_id); json.dump(result,f,indent=2)
    elif a.command=="prepare":
        if a.route_plan:
            plan=json.loads(Path(a.route_plan).read_text(encoding="utf-8"))
            a.rate=plan["mix_format"]["rate"]; a.channels=plan["mix_format"]["channels"]
        result=prepare_reference(a.output,a.rate,a.channels,a.seconds,a.peak_dbfs,a.seed,a.center_hz,a.channel_gains,a.band_octaves)
    else:
        if not a.confirm_playback: parser.error("play requires --confirm-playback")
        plan=json.loads(Path(a.route_plan).read_text(encoding="utf-8"))
        result=play(plan,a.reference_folder,a.output,a.mode,a.confirm_playback)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__": main()
