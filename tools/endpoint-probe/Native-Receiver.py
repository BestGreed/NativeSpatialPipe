"""Control only. Rust borrows shared slots and calls the unchanged renderer ABI."""
import ctypes as C, importlib.util, json, pathlib
ROOT=pathlib.Path(__file__).resolve().parents[2]

class Receiver:
    def __init__(self,pipeline,target_pid=None,timing=True,mmcss=False,full_timing=False,receiver_dll_path=None):
        if pipeline.handle is None:raise ValueError('Prewarm native pipeline before starting receiver')
        spec=importlib.util.spec_from_file_location('receiver_transport',ROOT/'tools/endpoint-probe/Native-Send.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.transport=module.Receiver(target_pid)
        self.config=self.transport.config;self.dll_path=self.transport.dll_path
        self.pipeline=pipeline;self.timing=int(timing)|(2 if mmcss else 0);self.mmcss=mmcss;self.handle=None;self.summary=None
        self.receiver_dll_path=ROOT/('build/native-receiver-scheduler' if mmcss else 'build/native-receiver')/'release/nsp_native_receiver.dll'
        self.full_timing=full_timing
        if full_timing:self.receiver_dll_path=ROOT/'build/native-receiver-full-timing/release/nsp_native_receiver.dll'
        if receiver_dll_path is not None:self.receiver_dll_path=pathlib.Path(receiver_dll_path).resolve()
        self.lib=C.CDLL(str(self.receiver_dll_path))
        signatures={
            'nsp_receiver_start':([C.c_wchar_p,C.c_wchar_p,C.c_void_p,C.c_void_p,C.c_uint32,C.c_uint32],C.c_void_p),
            'nsp_receiver_received':([C.c_void_p],C.c_uint64),
            'nsp_receiver_failed':([C.c_void_p],C.c_int),
            'nsp_receiver_finish':([C.c_void_p,C.c_void_p,C.c_size_t],C.c_size_t),
            'nsp_receiver_destroy':([C.c_void_p],C.c_int),
        }
        for name,(args,result) in signatures.items():
            fn=getattr(self.lib,name);fn.argtypes=args;fn.restype=result
        self.start_error=getattr(self.lib,'nsp_receiver_start_error',None)
        if self.start_error is not None:
            self.start_error.argtypes=[];self.start_error.restype=C.c_uint64
        if full_timing:
            self.lib.nsp_receiver_start_full.argtypes=self.lib.nsp_receiver_start.argtypes
            self.lib.nsp_receiver_start_full.restype=C.c_void_p
    def start(self,callback=None):
        if self.handle or self.summary is not None:raise RuntimeError('Receiver already started/stopped')
        # Actual native function pointer: no ctypes/Python callback trampoline.
        submit=C.cast(getattr(self.pipeline.lib.dll,'nsp_pipeline_submit_timed' if self.full_timing else 'nsp_pipeline_submit'),C.c_void_p)
        start=self.lib.nsp_receiver_start_full if self.full_timing else self.lib.nsp_receiver_start
        self.handle=start(self.config['name'],self.config['event'],self.pipeline.handle,submit,self.pipeline.frames,self.timing)
        if not self.handle:
            detail='diagnostic unavailable in this DLL; rebuild the receiver'
            if self.start_error is not None:
                error=self.start_error();stage=error>>32;code=error&0xffffffff
                stages={1:'arguments',2:'OpenFileMappingW',3:'OpenEventW',4:'MapViewOfFile',5:'ring header',6:'QueryPerformanceFrequency',7:'worker spawn',8:'panic'}
                detail=f'{stages.get(stage,"unknown")} (stage={stage}, Win32={code})'
                if code:detail+=': '+C.FormatError(code).strip()
            raise RuntimeError(f'Rust receiver start failed: {detail}; DLL={self.receiver_dll_path}')
    @property
    def received(self):
        return self.summary['received'] if self.summary is not None else (self.lib.nsp_receiver_received(self.handle) if self.handle else 0)
    @property
    def errors(self):
        return self.summary['errors'] if self.summary is not None else (['Native receiver failed; inspect final report'] if self.handle and self.lib.nsp_receiver_failed(self.handle) else [])
    def finish(self):
        if self.summary is not None:return self.summary
        if not self.handle:
            self.transport.finish();raise RuntimeError('Rust receiver was not started')
        size=self.lib.nsp_receiver_finish(self.handle,None,0)
        if not 0<size<1024*1024:raise RuntimeError('Receiver stop incomplete; retain pipeline, DLL and mapping')
        buf=C.create_string_buffer(size)
        if self.lib.nsp_receiver_finish(self.handle,buf,size)!=size:raise RuntimeError('Receiver report mismatch')
        result=json.loads(buf.value)
        result['mmcssRequested']=self.mmcss
        result['fullTiming']=self.full_timing
        if self.lib.nsp_receiver_destroy(self.handle):raise RuntimeError('Receiver remains active')
        self.handle=None
        self.transport.finish()
        self.pipeline.block=result['block'];self.pipeline.first_seq=result['firstSequence'];self.pipeline.last_seq=result['lastSequence']
        self.summary=result
        return result
