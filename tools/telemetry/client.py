"""Non-RT audio-host bridge. It forwards raw blocks; no sample metering here."""
import ctypes as C
import multiprocessing
import secrets
import socket
from service import run
from transport import fragments, MAX_BLOCK

BLOCK_BYTES=23072


class TelemetryService:
    def __init__(self, *, origins=('http://127.0.0.1:8765','http://localhost:8765'), debug=False, http_port=8766):
        ctx=multiprocessing.get_context('spawn')
        self.connection, child=ctx.Pipe()
        self.secret=secrets.token_bytes(32)
        self.process=ctx.Process(target=run,args=(child,self.secret,tuple(origins),debug,http_port),daemon=True)
        self.process.start(); child.close()
        self.healthy=True
        self.forwarded=self.dropped=0
        self.socket=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_SNDBUF,1024*1024)
        self.buffer=C.create_string_buffer(BLOCK_BYTES)
        self.object_buffer=C.create_string_buffer(MAX_BLOCK)
        self.frame_id=0
        self.stream_epoch=-1
        if not self.connection.poll(10):
            self.close(); raise RuntimeError('Telemetry service startup timeout')
        try: self.info=self.connection.recv()
        except EOFError:
            self.close(); raise RuntimeError('Telemetry service failed to start')
        if 'error' in self.info:
            message=self.info['error']
            self.close(); raise RuntimeError(message)

    def pump(self, pipeline, max_blocks=16):
        """Call on the host control/bridge thread about every 10 ms.

        Never call on an audio callback. Never race Pipeline.finish/destroy.
        Stop pumping before finish, then optionally drain using its still-live
        handle before destroy when using the raw C API.
        """
        if not self.process.is_alive() or pipeline.handle is None: return 0
        fn=pipeline.lib.dll.nsp_pipeline_tap_read
        fn.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t]; fn.restype=C.c_size_t
        count=0
        for _ in range(min(max(0,max_blocks),16)):
            size=fn(pipeline.handle,self.buffer,len(self.buffer))
            if size==0: break
            if size!=BLOCK_BYTES: raise RuntimeError('Unsupported raw tap format')
            epoch=C.c_uint64.from_buffer(self.buffer,16)
            self.stream_epoch=max(self.stream_epoch,epoch.value);epoch.value=self.stream_epoch
            try:
                self.socket.sendto(self.secret+self.buffer.raw,self.info['udp'])
                self.forwarded+=1
            except OSError:
                self.dropped+=1
            count+=1
        object_fn=getattr(pipeline.lib.dll,'nsp_pipeline_object_tap_read',None)
        if object_fn is not None:
            object_fn.argtypes=[C.c_void_p,C.c_void_p,C.c_size_t];object_fn.restype=C.c_size_t
            for _ in range(min(max(0,max_blocks),16)):
                size=object_fn(pipeline.handle,self.object_buffer,len(self.object_buffer))
                if size==0:break
                if not 32<=size<=MAX_BLOCK:raise RuntimeError('Unsupported object tap format')
                epoch=C.c_uint64.from_buffer(self.object_buffer,16)
                self.stream_epoch=max(self.stream_epoch,epoch.value);epoch.value=self.stream_epoch
                self.frame_id+=1
                for part in fragments(C.string_at(self.object_buffer,size),self.frame_id):
                    try:self.socket.sendto(self.secret+part,self.info['udp'])
                    except OSError:self.dropped+=1
        return count

    def attach(self, pipeline):
        """Explicitly start a new tap epoch, including after service restart."""
        getattr(pipeline.lib.dll,'nsp_pipeline_tap_read')
        getattr(pipeline.lib.dll,'nsp_pipeline_object_tap_read')
        pipeline.set_telemetry(False)
        pipeline.set_telemetry(True)
        # Native epochs are local to each pipeline. Give every attachment its
        # own increasing wire epoch so switching to sequence zero is accepted.
        # Only the bridge's copied headers change; native packets are untouched.
        self.stream_epoch+=2
        self.pump(pipeline) # Native reader discards queued blocks from old local epochs.

    def query(self, command, timeout=2):
        """Single control caller only. A timed-out channel is never reused."""
        if not self.healthy or not self.process.is_alive(): raise RuntimeError('Telemetry service unavailable')
        try:
            self.connection.send(command)
            if not self.connection.poll(timeout):
                self.healthy=False; raise TimeoutError('Telemetry service query timed out')
            return self.connection.recv()
        except (EOFError,BrokenPipeError,OSError):
            self.healthy=False; raise RuntimeError('Telemetry service unavailable')

    def close(self):
        if self.process.is_alive():
            try:
                if self.healthy: self.query('stop',timeout=.5)
            except (RuntimeError,TimeoutError): pass
            self.process.join(1)
            if self.process.is_alive():
                self.process.terminate(); self.process.join(2)
        self.connection.close(); self.socket.close()

    def __enter__(self): return self
    def __exit__(self,*args): self.close()
