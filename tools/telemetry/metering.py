"""Batch PCM reductions in telemetry only, with an explicit reference backend."""
import ctypes as C
import math
import os
from pathlib import Path
import struct

DLL=Path(__file__).resolve().parents[2]/'build/telemetry-meter/nsp_telemetry_meter.dll'


class Metering:
    def __init__(self, backend=None):
        backend=backend or os.environ.get('NSP_METER_BACKEND','auto')
        if backend not in ('auto','python','native'):
            raise ValueError('Unknown metering backend')
        self.name='python-reference'
        self.library=None
        if backend!='python' and DLL.is_file():
            self.library=C.CDLL(str(DLL))
            self.fn=self.library.nsp_meter_f32
            self.fn.argtypes=[C.c_char_p,C.c_size_t,C.c_size_t,C.c_size_t,C.c_uint32,C.POINTER(C.c_double)]
            self.fn.restype=C.c_int
            self.buffer=(C.c_double*(160*2))()
            self.name='native-c'
        elif backend=='native':
            raise RuntimeError('Build tools/telemetry/Build-Meter.ps1 first')

    def rows(self, raw, first, stride, count):
        if not 1<=count<=160 or stride<1920 or first<0 or first+(count-1)*stride+1920>len(raw):
            raise ValueError('Invalid PCM row bounds')
        if self.library is not None:
            if not isinstance(raw,bytes):raw=bytes(raw)
            result=self.fn(raw,len(raw),first,stride,count,self.buffer)
            if result<0:raise ValueError('Invalid native PCM row bounds')
            if not result:return None
            return [(self.buffer[i*2],self.buffer[i*2+1]) for i in range(count)]
        measured=[]
        for row in range(count):
            values=struct.unpack_from('<480f',raw,first+row*stride)
            if not all(map(math.isfinite,values)):return None
            measured.append((sum(value*value for value in values),max(map(abs,values))))
        return measured
