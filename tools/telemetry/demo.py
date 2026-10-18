"""Controlled source -> real ROAR -> isolated telemetry. No Hook/audio device."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import struct
import time
from client import TelemetryService

ROOT=Path(__file__).resolve().parents[2]


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('output',type=Path,help='New directory for diagnostic ROAR file and reports')
    parser.add_argument('--seconds',type=float,default=30.)
    parser.add_argument('--debug',action='store_true',help='Explicitly expose /internal/status')
    parser.add_argument('--objects',type=int,default=16,help='Controlled dynamic objects, 0..128, plus 12 static objects')
    args=parser.parse_args()
    if not 0<args.seconds<=3600: parser.error('seconds must be in (0,3600]')
    if not 0<=args.objects<=128:parser.error('objects must be in [0,128]')
    args.output.mkdir(parents=True,exist_ok=False)
    spec=importlib.util.spec_from_file_location('demo_native',ROOT/'tools/endpoint-probe/Native-Roar.py')
    native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)
    pipeline=native.NativePipeline(args.output,ROOT/'build/telemetry-objects/release/nsp_roar_bridge.dll')
    pipeline.prepare(480)
    try:
        with TelemetryService(debug=args.debug) as service:
            service.attach(pipeline)
            print('Controlled source / real ROAR, no device playback.',flush=True)
            print('Frontend: python tools/visualization-frontend/serve.py',flush=True)
            print('WebSocket: '+service.info['url'].replace('http:','ws:')+'/v1/telemetry',flush=True)
            start=time.monotonic()
            for seq in range(int(args.seconds*100)):
                count=args.objects+12
                props=[]
                for slot in range(count):
                    dynamic=slot<args.objects;angle=slot*math.tau/max(1,args.objects)+seq*.003
                    position=(20*math.cos(angle),3+slot*.2,20*math.sin(angle)) if dynamic else (0.,0.,0.)
                    flags=(12 if seq%100==99 else 5 if seq%100==0 else 4) if dynamic else 4
                    props.append(struct.pack('<4I4f',slot+1,480,1 if dynamic else 2<<(slot-args.objects),flags,*position,1.))
                props=b''.join(props)
                samples=[(.05 if c<args.objects else .003)*math.sin((seq*480+i)*math.tau*(500+c*25)/48000) for c in range(count) for i in range(480)]
                record=dict(block='controlled-demo',sequence=seq,count=count,frames=[480]*count)
                pipeline.submit(record,props+struct.pack('<'+str(count*480)+'f',*samples))
                service.pump(pipeline)
                time.sleep(max(0,start+(seq+1)*.01-time.monotonic()))
            # Drain final render completion on the control caller before finish.
            deadline=time.monotonic()+.3
            while time.monotonic()<deadline:
                service.pump(pipeline);time.sleep(.01)
            reports=dict(snapshot=service.query('snapshot'),history=service.query('history600'),status=service.query('status'))
            (args.output/'telemetry.json').write_text(json.dumps(reports,indent=2),encoding='utf-8')
    finally:
        pipeline.finish()


if __name__=='__main__':main()
