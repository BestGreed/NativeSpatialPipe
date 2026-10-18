"""Thin experiment control shim; packet processing and queues are Rust."""
import ctypes as C,json,pathlib,sys

class Library:
    def __init__(self,path):
        self.dll=C.CDLL(str(pathlib.Path(path).resolve()))
        signatures={
            'nsp_packet_create':([C.c_uint32,C.c_uint32,C.c_uint32,C.c_uint32,C.c_uint32,C.c_double],C.c_void_p),
            'nsp_packet_destroy':([C.c_void_p],None),
            'nsp_packet_render':([C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t,C.POINTER(C.c_float),C.c_size_t],C.c_int),
            'nsp_pipeline_create':([C.c_uint32,C.c_uint32,C.c_uint32,C.c_uint32,C.c_char_p],C.c_void_p),
            'nsp_pipeline_submit':([C.c_void_p,C.c_uint64,C.c_uint32,C.c_char_p,C.c_size_t],C.c_int),
            'nsp_pipeline_finish':([C.c_void_p,C.c_void_p,C.c_size_t],C.c_size_t),
            'nsp_pipeline_destroy':([C.c_void_p],None),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.dll,name);fn.argtypes=args;fn.restype=result

class PacketRenderer:
    def __init__(self,dll,frames,mode='individual',gain='passthrough',distance='direction',reference_m=1):
        if mode not in ('individual','grouped') or gain not in ('passthrough','apply') or distance not in ('direction','scaled'):raise ValueError('Invalid native policy')
        self.lib=Library(dll);self.frames=frames;self.out=(C.c_float*(12*frames))()
        self.handle=self.lib.dll.nsp_packet_create(frames,48000,mode=='grouped',gain=='apply',distance=='scaled',reference_m)
        self.failed=False
        if not self.handle:raise RuntimeError('Native packet engine create failed')
    def render(self,record,data):
        if self.failed:raise RuntimeError('Native engine failed; rebuild required')
        if record['frames']!=[self.frames]*record['count']:raise ValueError('Descriptor frame mismatch')
        status=self.lib.dll.nsp_packet_render(self.handle,record['sequence'],record['count'],data,len(data),self.out,len(self.out))
        if status:self.failed=True;raise RuntimeError(f'Native packet failure {status}; invalid data, discontinuity or unstable grouped profile')
        return self.out
    def close(self):
        if self.handle:self.lib.dll.nsp_packet_destroy(self.handle);self.handle=None

class NativePipeline:
    def __init__(self,out,dll,capacity=64,mode='individual',gain='passthrough'):
        if mode not in ('individual','grouped') or gain not in ('passthrough','apply'):raise ValueError('Invalid native policy')
        self.out=pathlib.Path(out);self.lib=Library(dll);self.capacity=capacity;self.mode=mode;self.gain=gain
        self.handle=None;self.frames=None;self.block=None;self.errors=[];self.summary=None;self.first_seq=None;self.last_seq=None
    def prepare(self,frames):
        """Control-thread initialization; no stream is bound until its first packet."""
        if self.handle is not None or self.summary is not None or self.errors:raise RuntimeError('Pipeline already initialized or stopped')
        if type(frames) is not int or not 1<=frames<=4096:raise ValueError('Invalid prewarm frame count')
        path=str((self.out/'roar-714.f32').resolve()).encode('utf8')
        handle=self.lib.dll.nsp_pipeline_create(frames,self.capacity,self.mode=='grouped',self.gain=='apply',path)
        if not handle:raise RuntimeError('Native pipeline create failed')
        self.frames=frames;self.handle=handle
    def set_telemetry(self,enabled):
        """Control caller only, after prepare; requires a telemetry-capable DLL."""
        if type(enabled) is not bool:raise TypeError('enabled must be bool')
        if self.handle is None or self.summary is not None:raise RuntimeError('Pipeline not running')
        fn=self.lib.dll.nsp_pipeline_telemetry_enable
        fn.argtypes=[C.c_void_p,C.c_uint32];fn.restype=C.c_int
        if fn(self.handle,int(enabled)):raise RuntimeError('Telemetry unsupported or failed')
    def telemetry(self):
        """Native diagnostic status; isolated-tap builds do not meter samples."""
        if self.handle is None or self.summary is not None:raise RuntimeError('Pipeline not running')
        fn=self.lib.dll.nsp_pipeline_telemetry_read
        fn.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];fn.restype=C.c_size_t
        needed=fn(self.handle,None,0)
        for _ in range(8):
            if not 0<needed<1024*1024:raise RuntimeError('Invalid telemetry payload size')
            buf=C.create_string_buffer(needed)
            size=fn(self.handle,buf,len(buf))
            if 0<size<=len(buf):return json.loads(buf.value)
            needed=size
        raise RuntimeError('Telemetry payload changed during read; retry')
    def submit(self,record,data):
        if self.summary is not None or self.errors:raise RuntimeError('Native pipeline stopped or failed')
        try:self._submit(record,data)
        except Exception as exc:self.errors.append(repr(exc));raise
    def _submit(self,record,data):
        if self.handle is None:
            self.prepare(record['frames'][0])
        if self.block is None:self.block=record['block']
        if record['block']!=self.block or record['frames']!=[self.frames]*record['count']:raise ValueError('Native pipeline stream/frame mismatch')
        status=self.lib.dll.nsp_pipeline_submit(self.handle,record['sequence'],record['count'],data,len(data))
        if status:raise RuntimeError(f'Native submit failed: {status}')
        if self.first_seq is None:self.first_seq=record['sequence']
        self.last_seq=record['sequence']
    def finish(self,tap_drain=None):
        if self.summary is not None:return self.summary
        result={'passed':False,'enqueued':0,'consumed':0,'error':'No native packets','threadStopped':True}
        if self.handle:
            try:
                needed=self.lib.dll.nsp_pipeline_finish(self.handle,None,0)
                if not 0<needed<1024*1024:raise RuntimeError('Invalid native report')
                buf=C.create_string_buffer(needed);self.lib.dll.nsp_pipeline_finish(self.handle,buf,len(buf));result=json.loads(buf.value)
                if tap_drain is not None:
                    try:result['telemetryFinalDrain']=tap_drain(self)
                    except Exception as exc:result['telemetryFinalDrainError']=repr(exc)
            finally:self.lib.dll.nsp_pipeline_destroy(self.handle);self.handle=None
        result.update(controlErrors=self.errors,nativeMode=self.mode,gainPolicy=self.gain,firstSequence=self.first_seq,lastSequence=self.last_seq)
        if getattr(self,'allow_idle',False) and result.get('enqueued')==0:
            result['idlePrepared']=True
            result['passed']=result.get('error') is None and result.get('threadStopped') is True and not result.get('queueDropped',0)
        result['passed']=result['passed'] and not self.errors
        self.summary=result;(self.out/'native-summary.json').write_text(json.dumps(result,indent=2),encoding='utf8');return result
