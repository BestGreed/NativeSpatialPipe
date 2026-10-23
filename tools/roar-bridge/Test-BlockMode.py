"""Compare block sizes and grouping with the pre-whole-block adapter DLL."""
import argparse
import ctypes as C
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import struct

ROOT = Path(__file__).resolve().parents[2]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--reference',type=Path,default=ROOT/'build/roar-block-mode-20261022/reference.dll')
parser.add_argument('--candidate',type=Path,default=ROOT/'build/roar-native/release/nsp_roar_bridge.dll')
parser.add_argument('--output',type=Path,default=ROOT/'build/roar-block-mode-20261022')
args=parser.parse_args();OUT=args.output;OUT.mkdir(parents=True,exist_ok=True)
spec = importlib.util.spec_from_file_location('audit', ROOT/'tools/endpoint-probe/Analyze-RoarKernel.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
packets, source = audit.packets()
search = os.add_dll_directory(str(audit.LLVM))
paths = [args.reference,args.candidate]
libs = []
for path in paths:
    lib = C.CDLL(str(path))
    lib.nsp_packet_create.argtypes = [C.c_uint32]*5+[C.c_double]
    lib.nsp_packet_create.restype = C.c_void_p
    lib.nsp_packet_render.argtypes = [C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t,C.c_void_p,C.c_size_t]
    lib.nsp_packet_destroy.argtypes = [C.c_void_p]
    libs.append(lib)
checks = []
for frames in [32,120,240,480,960]:
    for grouped in [0,1]:
        for scaled in [0,1]:
            handles = [lib.nsp_packet_create(frames,48000,grouped,0,scaled,1.) for lib in libs]
            assert all(handles)
            outputs = [(C.c_float*(frames*12))() for _ in libs]
            digest = hashlib.sha256()
            try:
                for seq, packet in enumerate(packets[:8]):
                    properties = bytearray(packet[:140*32])
                    for i in range(140):
                        struct.pack_into('<I',properties,i*32+4,frames)
                    pcm = b''.join((packet[140*32+i*480*4:140*32+(i+1)*480*4]*((frames+479)//480))[:frames*4] for i in range(140))
                    payload = bytes(properties)+pcm
                    for lib, h, output in zip(libs, handles, outputs):
                        assert lib.nsp_packet_render(h,seq,140,payload,len(payload),output,frames*12)==0
                    assert bytes(outputs[0])==bytes(outputs[1]), (frames,grouped,scaled,seq)
                    digest.update(bytes(outputs[1]))
            finally:
                for lib,h in zip(libs,handles):lib.nsp_packet_destroy(h)
            checks.append(dict(frames=frames,grouped=grouped,scaled=scaled,packets=8,byteIdentical=True,sha256=digest.hexdigest()))
result = dict(passed=True,source=source,checks=checks,dllSha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
              note='Synthetic block sizes from the same captured PCM, not new endpoint captures. Selected reference and candidate adapter DLLs.')
(OUT/'block-sizes.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
print(f'{len(checks)} block-size/group/radius cases passed; all PCM byte-identical')
