"""Hash-pinned libsurvive ABI adapter, raw hooks retained before default fusion.

Constructing this object configures/starts HID devices (feature reports). It is
never used by OS inventory. Pairing is always explicitly disabled. No SteamVR.
"""
import ctypes as C
import hashlib
import math
from pathlib import Path
import time

from .core import TickUnwrapper


class ObjectPrefix(C.Structure):
    # Prefix audited against the pinned headers, NOT a generic future ABI.
    _fields_=[("ctx",C.c_void_p),("codename",C.c_char*4),("driver",C.c_char*8),("serial",C.c_char*16)]


class Pose(C.Structure):
    _fields_=[("position",C.c_double*3),("wxyz",C.c_double*4)]


class Tracker:
    def __init__(self,dll_path,config_path,serials,submit,clock,diagnostic=False,receiver_serials=None,reviewed_sha256=None,verbosity=0):
        if not receiver_serials:
            raise ValueError("Explicit receiver serial allowlist required; use the shared rig broker")
        expected=reviewed_sha256 or "0204acf78c39eb4dfb58bf188640be00813d1fdcd75e699a35cf942dc2d9083b"
        if hashlib.sha256(Path(dll_path).read_bytes()).hexdigest()!=expected:
            raise ValueError("Unreviewed libsurvive binary/ABI; update provenance before use")
        self.dll=C.CDLL(str(Path(dll_path).resolve()))
        self.submit,self.clock,self.serials=submit,clock,None if serials is None else set(serials)
        serials=serials or []
        self.epochs={s:0 for s in serials}; self.ticks={}; self.optical={}; self.callbacks=[]; self.ready=set()
        d=self.dll
        d.survive_init_internal.argtypes=[C.c_int,C.POINTER(C.c_char_p),C.c_void_p,C.c_void_p]
        d.survive_init_internal.restype=C.c_void_p
        d.survive_poll.argtypes=[C.c_void_p]; d.survive_poll.restype=C.c_int
        d.survive_close.argtypes=[C.c_void_p]
        # Do not load an unrelated persistent global libsurvive configuration.
        # v1.01 treats a path starting with a drive letter as a config *name*
        # below LOCALAPPDATA. Worker cwd is its session directory; './' keeps
        # all state inside that directory instead of the user's global state.
        args=[b"lsac",b"--pair-device",b"0",b"--configfile",str(config_path).encode()]
        if verbosity:
            if not isinstance(verbosity,int) or not 0<=verbosity<=10: raise ValueError("Invalid verbosity")
            args += [b"--v",str(verbosity).encode()]
        if diagnostic: args += [b"--disable-calibrate",b"1"]
        if receiver_serials:
            if any(not s or "," in s for s in receiver_serials): raise ValueError("Invalid receiver allowlist")
            args += [b"--usb-serial-allowlist",",".join(receiver_serials).encode("ascii")]
        self.argv=(C.c_char_p*len(args))(*args)
        from .ownership import ReceiverLease
        self.lease=ReceiverLease(receiver_serials or ["unscoped-libsurvive"])
        self.lease.acquire()
        self.submit(dict(kind="tracker_phase",phase="init_enter",arrival_lsl=self.clock()),None)
        try:
            self.ctx=d.survive_init_internal(len(args),self.argv,None,None)
        except BaseException:
            self.lease.close(); raise
        self.submit(dict(kind="tracker_phase",phase="init_return",arrival_lsl=self.clock()),None)
        if not self.ctx:
            self.lease.close(); raise RuntimeError("libsurvive init failed")
        try:
            self.hook("raw_imu",[C.c_void_p,C.c_int,C.POINTER(C.c_double),C.c_uint32,C.c_int],self.imu)
            self.hook("pose",[C.c_void_p,C.c_uint64,C.POINTER(Pose)],self.pose)
            self.hook("angle",[C.c_void_p,C.c_int,C.c_int,C.c_uint32,C.c_double,C.c_double,C.c_uint32],self.angle)
            self.hook("sweep_angle",[C.c_void_p,C.c_uint8,C.c_int,C.c_uint32,C.c_int8,C.c_double],self.angle)
            self.hook("disconnect",[C.c_void_p],self.disconnect)
            self.hook("new_object",[C.c_void_p],self.new_object)
            self.hook("lighthouse_pose",[C.c_void_p,C.c_uint8,C.POINTER(Pose)],self.lighthouse_pose)
            self.hook("ootx_received",[C.c_void_p,C.c_uint8],self.ootx_received)
        except BaseException:
            try: d.survive_close(self.ctx)
            finally: self.ctx=None; self.lease.close()
            raise

    def hook(self,name,types,fn):
        signature=C.CFUNCTYPE(None,*types)
        previous=None
        def callback(*args):
            try: fn(*args)
            except BaseException as exc:
                self.submit(dict(kind="tracker_callback_error",error=repr(exc),arrival_lsl=self.clock()),None)
            finally:
                if previous: previous(*args)  # Preserve default fusion/recording pipeline.
        cb=signature(callback)
        install=getattr(self.dll,"survive_install_"+name+"_fn")
        install.argtypes=[C.c_void_p,signature]; install.restype=C.c_void_p
        old=install(self.ctx,cb)
        previous=signature(old) if old else None
        self.callbacks.extend([cb,previous])

    def identity(self,obj):
        s=C.cast(obj,C.POINTER(ObjectPrefix)).contents.serial.decode("ascii",errors="replace")
        self.epochs.setdefault(s,0)
        return s

    def accepted(self,s):
        return self.serials is None or s in self.serials

    def new_object(self,obj):
        s=self.identity(obj)
        p=C.cast(obj,C.POINTER(ObjectPrefix)).contents
        sensor_count=self.dll.survive_object_sensor_ct
        sensor_count.argtypes=[C.c_void_p]; sensor_count.restype=C.c_int8
        self.submit(dict(kind="tracker_identity",serial=s,codename=p.codename.decode(errors="replace"),sensor_count=sensor_count(obj),
                         driver=p.driver.decode(errors="replace"),epoch=self.epochs[s],arrival_lsl=self.clock()),None)

    def imu(self,obj,mask,values,tick,callback_id):
        s=self.identity(obj)
        if not self.accepted(s): return
        if s not in self.ready:
            sensor_count=self.dll.survive_object_sensor_ct
            sensor_count.argtypes=[C.c_void_p]; sensor_count.restype=C.c_int8
            self.submit(dict(kind="tracker_ready",serial=s,sensor_count=sensor_count(obj),epoch=self.epochs[s],
                             arrival_lsl=self.clock(),basis="first_raw_imu_after_device_config"),None)
            self.ready.add(s)
        unwrap=self.ticks.setdefault(s,TickUnwrapper(self.epochs[s])).update(tick)
        self.submit(dict(kind="raw_imu",serial=s,mask=mask,raw_accel_gyro=[values[i] for i in range(6)],
                         **unwrap,tick_hz=48000000,callback_id=callback_id,
                         callback_id_is_sample_counter=False,arrival_lsl=self.clock(),
                         time_basis="raw_device_ticks_and_host_arrival_unmapped"),None)

    def angle(self,obj,*args):
        s=self.identity(obj)
        if self.accepted(s):
            stamp=self.clock(); self.optical[s]=stamp
            self.submit(dict(kind="optical",serial=s,epoch=self.epochs[s],arrival_lsl=stamp,
                             decoded_event_args=list(args)),None)

    def pose(self,obj,tick,pose):
        s=self.identity(obj)
        if not self.accepted(s): return
        stamp=self.clock(); p=pose.contents
        values=list(p.position)+list(p.wxyz)
        age=stamp-self.optical.get(s,float("-inf"))
        norm=math.sqrt(sum(v*v for v in values[3:]))
        self.submit(dict(kind="pose",serial=s,position_m=values[:3],quaternion_wxyz=values[3:],
                         pose_timecode_u64=tick,epoch=self.epochs[s],arrival_lsl=stamp,
                         optical_age_seconds=age if math.isfinite(age) else None,
                         quaternion_norm=norm,
                         valid=all(math.isfinite(v) for v in values) and abs(norm-1)<.01 and 0<=age<.25,
                         quality_basis="finite_pose_and_recent_decoded_optical_not_accuracy_certificate",
                         provider_confidence=None,time_basis="provider_ticks_and_host_arrival_unmapped"),None)

    def lighthouse_pose(self,ctx,index,pose):
        p=pose.contents
        self.submit(dict(kind="lighthouse_pose",lighthouse_index=index,position_m=list(p.position),
                         quaternion_wxyz=list(p.wxyz),arrival_lsl=self.clock(),
                         quality_basis="provider_estimate_not_independent_geometry_validation"),None)

    def ootx_received(self,ctx,index):
        self.submit(dict(kind="ootx_received",lighthouse_index=index,arrival_lsl=self.clock()),None)

    def disconnect(self,obj):
        s=self.identity(obj)
        if self.accepted(s):
            self.epochs[s]+=1; self.ticks.pop(s,None); self.optical.pop(s,None)
            self.ready.discard(s)
            self.submit(dict(kind="tracker_disconnect",serial=s,epoch=self.epochs[s],arrival_lsl=self.clock()),None)

    def run(self,stop):
        from .scheduling import CaptureMMCSS
        # This Windows HIDAPI build defines HID_NONBLOCKING: survive_poll
        # performs HID reads and decoding on this same registered thread.
        try:
            with CaptureMMCSS('tracker_hid_poll_decode',self.submit):
                return self._poll(stop)
        finally:
            self.submit(dict(kind="tracker_phase",phase="close_enter",arrival_lsl=self.clock()),None)
            try: self.dll.survive_close(self.ctx)
            finally: self.ctx=None; self.lease.close()
            self.submit(dict(kind="tracker_phase",phase="close_return",arrival_lsl=self.clock()),None)

    def _poll(self,stop):
        self.submit(dict(kind="tracker_phase",phase="poll_enter",arrival_lsl=self.clock()),None)
        first=True
        while not stop.is_set():
            result=self.dll.survive_poll(self.ctx)
            if first:
                self.submit(dict(kind="tracker_phase",phase="first_poll_return",arrival_lsl=self.clock()),None); first=False
            if result: raise RuntimeError("libsurvive poll stopped: "+str(result))
