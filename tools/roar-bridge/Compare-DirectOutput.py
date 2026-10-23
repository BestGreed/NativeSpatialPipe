"""Bounded offline before/after comparison, same 140-slot packets, MMCSS."""
import ctypes as C,hashlib,importlib.util,json,os,statistics,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];OUT=ROOT/'build/roar-direct-output-20261022'
spec=importlib.util.spec_from_file_location('audit',ROOT/'tools/endpoint-probe/Analyze-RoarKernel.py');a=importlib.util.module_from_spec(spec);spec.loader.exec_module(a)
packets,source=a.packets();search=os.add_dll_directory(str(ROOT/'work/rust-bootstrap/llvm-fast/llvm-mingw-20261006-ucrt-x86_64/bin'))
libs={};hashes={}
for name in ['reference','candidate']:
 p=OUT/(name+'.dll');lib=C.CDLL(str(p));lib.nsp_packet_create.argtypes=[C.c_uint32]*5+[C.c_double];lib.nsp_packet_create.restype=C.c_void_p
 lib.nsp_packet_render.argtypes=[C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t,C.c_void_p,C.c_size_t];lib.nsp_packet_destroy.argtypes=[C.c_void_p];libs[name]=lib;hashes[name]=hashlib.sha256(p.read_bytes()).hexdigest()
avrt=C.WinDLL('avrt');avrt.AvSetMmThreadCharacteristicsW.argtypes=[C.c_wchar_p,C.POINTER(C.c_uint32)];avrt.AvSetMmThreadCharacteristicsW.restype=C.c_void_p
avrt.AvSetMmThreadPriority.argtypes=[C.c_void_p,C.c_int];avrt.AvRevertMmThreadCharacteristics.argtypes=[C.c_void_p]
i=C.c_uint32();task=avrt.AvSetMmThreadCharacteristicsW('Pro Audio',C.byref(i));assert task and avrt.AvSetMmThreadPriority(task,0)
rows=[];pcmhash=None
try:
 for round in range(3):
  for name in (['reference','candidate'] if round%2==0 else ['candidate','reference']):
   lib=libs[name];h=lib.nsp_packet_create(480,48000,0,0,1,1.);assert h;out=(C.c_float*(480*12))();times=[];digest=hashlib.sha256()
   try:
    for seq,p in enumerate(packets):
     start=time.perf_counter_ns();assert lib.nsp_packet_render(h,seq,140,p,len(p),out,480*12)==0;times.append((time.perf_counter_ns()-start)/1e6);digest.update(bytes(out))
   finally:lib.nsp_packet_destroy(h)
   d=digest.hexdigest();assert pcmhash is None or pcmhash==d;pcmhash=d
   hot=sorted(times[8:]);row=dict(round=round+1,variant=name,firstCallMs=times[0],steadyMedianMs=statistics.median(hot),steadyP99Ms=hot[int((len(hot)-1)*.99)],steadyMaxMs=max(hot),callMs=times);rows.append(row)
finally:assert avrt.AvRevertMmThreadCharacteristics(task)
result=dict(passed=True,mmcss=True,source=source,dllSha256=hashes,pcmSha256=pcmhash,rows=rows,scope='3 alternating offline rounds, 800 same-source packets each, 140 slots/128 dynamic. Whole packet API latency, not live capture-to-DSP or ASIO latency. First 8 calls excluded from steady stats. No synthetic load; cold process effects are not isolated.')
(OUT/'comparison.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(json.dumps([{k:v for k,v in r.items() if k!='callMs'} for r in rows],indent=2))
