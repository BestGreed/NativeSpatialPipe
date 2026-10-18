"""Identical full-function 140-object workload; no live audio or network."""
import argparse
import cProfile
import json
from pathlib import Path
import pstats
import statistics
import time
from soundscape import SoundscapeEngine
from test_engine import block
from test_soundscape import objects


def exercise(samples, backend):
    now=[0.]
    engine=SoundscapeEngine(clock=lambda:now[0],meter_backend=backend)
    for i,(output,raw) in enumerate(samples):
        now[0]=i*.01
        engine.ingest(output);engine.ingest_objects(raw);engine.flush()
    engine.flush(True)
    return engine


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile',action='store_true')
    args=parser.parse_args()
    samples=[(block(i),objects(i,count=140)) for i in range(200)]
    results={}
    for backend in ('python','native'):
        times=[]
        for _ in range(3):
            start=time.process_time();exercise(samples,backend);times.append(time.process_time()-start)
        seconds=statistics.median(times)
        results[backend]=dict(cpuSecondsForTwoAudioSeconds=seconds,oneCorePercent=seconds/2*100,runs=times)
    results['speedup']=results['python']['cpuSecondsForTwoAudioSeconds']/results['native']['cpuSecondsForTwoAudioSeconds']
    print(json.dumps(results,indent=2))
    root=Path(__file__).resolve().parents[2]
    (root/'build/telemetry-meter/benchmark.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    if args.profile:
        profile=cProfile.Profile();profile.runcall(exercise,samples,'native')
        pstats.Stats(profile).strip_dirs().sort_stats('tottime').print_stats(12)
