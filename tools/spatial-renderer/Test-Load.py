"""Fresh process: load the standalone build without adding a compiler DLL path."""
import ctypes as C
import json
from pathlib import Path
import sys

directory=Path(sys.argv[1]).absolute()
kernel=C.WinDLL('kernel32',use_last_error=True)
kernel.GetModuleHandleW.argtypes=[C.c_wchar_p];kernel.GetModuleHandleW.restype=C.c_void_p
assert not kernel.GetModuleHandleW('libc++.dll'),'run this check in a fresh Python process'
lib=C.CDLL(str(directory/'NSP.SpatialRenderer.dll'),winmode=0x1100)
lib.nsp_spatial_abi.restype=C.c_uint32
lib.nsp_spatial_create.restype=C.c_void_p
lib.nsp_spatial_flush.argtypes=[C.c_void_p,C.c_void_p,C.c_uint32]
lib.nsp_spatial_destroy.argtypes=[C.c_void_p]
assert lib.nsp_spatial_abi()==1
handle=lib.nsp_spatial_create();assert handle
try:
    output=(C.c_float*(512*12))()
    assert lib.nsp_spatial_flush(handle,output,512)==0
finally:lib.nsp_spatial_destroy(handle)
report=dict(passed=True,abi=1,freshProcess=True,compilerSearchPathAdded=False,emptyTailFrames=0)
(directory/'load-check.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
