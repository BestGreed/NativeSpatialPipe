"""Thread-scoped MMCSS registration; no process/registry priority changes."""
import ctypes as C
import json
import threading
import time


class ThreadBasicInformation(C.Structure):
    _fields_=[('exit_status',C.c_int32),('teb',C.c_void_p),
              ('process_id',C.c_void_p),('thread_id',C.c_void_p),
              ('affinity',C.c_size_t),('priority',C.c_int32),('base_priority',C.c_int32)]


def snapshot():
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentThread.restype=C.c_void_p
    query=C.WinDLL('ntdll').NtQueryInformationThread
    query.argtypes=[C.c_void_p,C.c_int32,C.c_void_p,C.c_uint32,C.c_void_p]
    query.restype=C.c_int32
    info=ThreadBasicInformation()
    status=query(kernel.GetCurrentThread(),0,C.byref(info),C.sizeof(info),None)
    result=dict(thread_id=threading.get_native_id(),query_status=int(status))
    if status>=0: result.update(current_priority=info.priority,base_priority=info.base_priority)
    return result


class CaptureMMCSS:
    """Register the calling acquisition thread as Pro Audio / AVRT HIGH.

    MMCSS owns dynamic absolute priorities. A snapshot is observation, never a
    promise that the thread stays above 15 or at exactly 20 under CPU pressure.
    Registration/priority failure aborts acquisition instead of silent fallback.
    """
    def __init__(self,role,submit=None):
        self.role,self.submit=role,submit
        self.handle=None; self.owner=None

    def report(self,phase,**extra):
        event=dict(kind='capture_scheduling',role=self.role,phase=phase,
                   task='Pro Audio',avrt_priority='HIGH',avrt_priority_value=1,
                   **snapshot(),**extra)
        if self.submit is not None: self.submit(event,None)
        return event

    def __enter__(self):
        self.owner=threading.get_native_id()
        self.avrt=C.WinDLL('avrt',use_last_error=True)
        self.avrt.AvSetMmThreadCharacteristicsW.argtypes=[C.c_wchar_p,C.POINTER(C.c_uint32)]
        self.avrt.AvSetMmThreadCharacteristicsW.restype=C.c_void_p
        self.avrt.AvSetMmThreadPriority.argtypes=[C.c_void_p,C.c_int32]
        self.avrt.AvSetMmThreadPriority.restype=C.c_int32
        self.avrt.AvRevertMmThreadCharacteristics.argtypes=[C.c_void_p]
        self.avrt.AvRevertMmThreadCharacteristics.restype=C.c_int32
        self.before=snapshot()
        index=C.c_uint32()
        self.handle=self.avrt.AvSetMmThreadCharacteristicsW('Pro Audio',C.byref(index))
        if not self.handle:
            error=C.get_last_error(); self.report('registration_failed',winerror=error)
            raise C.WinError(error)
        if not self.avrt.AvSetMmThreadPriority(self.handle,1):
            error=C.get_last_error()
            self.avrt.AvRevertMmThreadCharacteristics(self.handle); self.handle=None
            self.report('priority_failed',winerror=error)
            raise C.WinError(error)
        self.task_index=index.value
        try: self.registered=self.report('registered',task_index=index.value,before=self.before)
        except BaseException:
            self.avrt.AvRevertMmThreadCharacteristics(self.handle); self.handle=None; raise
        return self

    def __exit__(self,*exc):
        if threading.get_native_id()!=self.owner: raise RuntimeError('MMCSS must revert on its owning thread')
        if self.handle:
            if not self.avrt.AvRevertMmThreadCharacteristics(self.handle): raise C.WinError(C.get_last_error())
            self.handle=None
            self.report('reverted')


if __name__=='__main__':
    records=[]
    with CaptureMMCSS('software_probe',lambda event,raw:records.append(event)):
        for _ in range(5):
            time.sleep(.02); records.append(dict(phase='observed',**snapshot()))
    print(json.dumps(records,indent=2))
