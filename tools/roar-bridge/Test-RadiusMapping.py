"""N09 actual OLR radius-response measurement; no device/capture/default changes."""
import ctypes as C
import hashlib
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

ROOT=Path(__file__).absolute().parents[2]
sys.path.insert(0,str(ROOT/'work/consumer-deps'))
import numpy as np

OUT=ROOT/'build/dev16-n09-radius'
RUN=OUT/'runs'/str(time.time_ns())
RUN.mkdir(parents=True)
subprocess.run(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',
    str(ROOT/'tools/roar-bridge/Build.ps1'),'-TargetDirectory','build/dev16-n09-radius'],
    cwd=ROOT,check=True,timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)
llvm=ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'
search=os.add_dll_directory(str(llvm))
dll=OUT/'release/nsp_roar_bridge.dll'
lib=C.CDLL(str(dll))
lib.nsp_packet_create.argtypes=[C.c_uint32]*5+[C.c_double]
lib.nsp_packet_create.restype=C.c_void_p
lib.nsp_packet_render.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t,C.POINTER(C.c_float),C.c_size_t]
lib.nsp_packet_destroy.argtypes=[C.c_void_p]
labels=['L','R','C','LFE','Ls','Rs','Lrs','Rrs','Ltf','Rtf','Ltr','Rtr']
rows=[];samples={}

def measure(name,xyz,scaled=True,reference=1):
    h=lib.nsp_packet_create(480,48000,1,0,int(scaled),reference)
    if not h: raise RuntimeError('OLR create failed')
    blocks=[]
    try:
        for block in range(24):
            pcm=np.array([.01*math.sin(2*math.pi*400*(block*480+f)/48000) for f in range(480)],dtype='<f4')
            packet=struct.pack('<4I4f',1,480,1,5 if block==0 else 4,*xyz,.25)+pcm.tobytes()
            output=(C.c_float*(480*12))()
            code=lib.nsp_packet_render(h,block,1,packet,len(packet),output,len(output))
            if code: raise RuntimeError((name,'OLR render failed',code))
            if block>=16: blocks.append(np.ctypeslib.as_array(output).reshape(12,480).T.copy())
    finally: lib.nsp_packet_destroy(h)
    data=np.concatenate(blocks)
    assert np.isfinite(data).all(),name
    power=np.mean(data.astype(np.float64)**2,axis=0)
    total=float(power.sum());assert total>0,name
    share=power/total
    data.astype('<f4').tofile(RUN/(name+'.f32'))
    rows.append(dict(name=name,sourceXYZ=list(xyz),sourceRadius=math.sqrt(sum(v*v for v in xyz)),
        mode='scaled' if scaled else 'direction',reference=reference,
        olrXYZ=[xyz[0]/reference,-xyz[2]/reference,xyz[1]/reference] if scaled else None,
        totalPower=total,energyShare=share.tolist(),peak=float(np.abs(data).max()),
        steadySha256=hashlib.sha256(data.tobytes()).hexdigest()))
    samples[name]=data
    return share

radii=[0,.125,.25,.5,.75,.9,1,2,4,16,128]
directions={'front':(0,0,-1),'diagonal':(.6,0,-.8),
            'below':(.6,-.5,-.8),'above':(.6,.5,-.8),'rear':(.6,0,.8)}
for name,unit in directions.items():
    unit=np.array(unit,dtype=np.float64);unit/=np.linalg.norm(unit)
    for radius in radii:
        measure(f'{name}-r{radius}',unit*radius)
    for radius in [.125,1,128]:
        measure(f'{name}-direction-r{radius}',unit*radius,scaled=False)
measure('reference10-radius5',(0,0,-5),reference=10)
measure('origin-direction',(0,0,0),scaled=False)
# Same captured direction at two depths becomes different after a lateral
# listener translation. Synthetic input only; no production 6DoF feature.
measure('translated-near',(-.2,0,-1))
measure('translated-far',(-.2,0,-10))
checks=[]
def check(name,value):
    checks.append(dict(name=name,passed=bool(value)))
for name in directions:
    check(name+' direction discards radial variation',np.allclose(samples[name+'-direction-r0.125'],samples[name+'-direction-r128'],atol=1e-7,rtol=1e-5))
    check(name+' large radii saturate in current OLR',np.allclose(samples[name+'-r4'],samples[name+'-r128'],atol=1e-7,rtol=1e-5))
check('scaled near radius changes PCM',not np.allclose(samples['front-r0.125'],samples['front-r1'],atol=1e-7))
check('reference is dimensionless ratio',np.allclose(samples['reference10-radius5'],samples['front-r0.5'],atol=1e-7,rtol=1e-5))
check('zero has consistent handling',np.allclose(samples['origin-direction'],samples['front-r0'],atol=1e-7))
check('negative height differs from positive height',not np.allclose(samples['below-r0.5'],samples['above-r0.5'],atol=1e-7))
check('preserved depth changes lateral-translation parallax',not np.allclose(samples['translated-near'],samples['translated-far'],atol=1e-7))
check('static sweep preserves total power instead of distance attenuation',all(np.isclose(row['totalPower'],.00005,rtol=1e-4) for row in rows))

def motion(scaled):
    h=lib.nsp_packet_create(480,48000,1,0,int(scaled),1.)
    if not h: raise RuntimeError('Motion engine create failed')
    trace=[];audio=[]
    try:
        for block in range(88):
            radius=.125 if block<24 else .125+(4-.125)*(block-24)/63
            pcm=np.array([.01*math.sin(2*math.pi*400*(block*480+f)/48000) for f in range(480)],dtype='<f4')
            packet=struct.pack('<4I4f',1,480,1,5 if block==0 else 4,0.,0.,-radius,.25)+pcm.tobytes()
            output=(C.c_float*(480*12))()
            if lib.nsp_packet_render(h,block,1,packet,len(packet),output,len(output)):
                raise RuntimeError('Motion render failed')
            data=np.ctypeslib.as_array(output).reshape(12,480).T.copy()
            if block>=24:
                power=np.mean(data.astype(np.float64)**2,axis=0)
                trace.append(dict(sourceRadius=radius,energyShare=(power/power.sum()).tolist()))
                audio.append(data)
    finally: lib.nsp_packet_destroy(h)
    np.concatenate(audio).astype('<f4').tofile(RUN/('motion-scaled.f32' if scaled else 'motion-direction.f32'))
    return trace
motions=dict(scaled=motion(True),direction=motion(False))
check('radial motion is audible as distribution change in scaled path',motions['scaled'][0]['energyShare'][2]<.4 and motions['scaled'][-1]['energyShare'][2]>.999)
check('radial motion keeps direction path unchanged',all(abs(row['energyShare'][2]-1)<1e-7 for row in motions['direction']))
result=dict(passed=all(r['passed'] for r in checks),scope='Synthetic native packet -> actual OLR, grouped/gain passthrough; default unchanged',
    calibrationStatus='OLR response measured; source-world scale and reference are not calibrated',
    dllSha256=hashlib.sha256(dll.read_bytes()).hexdigest(),labels=labels,checks=checks,cases=rows,motions=motions)
(RUN/'radius-response.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps(dict(passed=result['passed'],cases=len(rows),checks=checks,evidence=str(RUN)),indent=2))
if not result['passed']: raise SystemExit('Radius response checks failed; evidence retained')
