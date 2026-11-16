"""Cooperative process-wide and Windows cross-process receiver ownership."""
import ctypes as C
import hashlib
import os
import threading

_held=set()
_lock=threading.Lock()


class ReceiverLease:
    def __init__(self,serials):
        self.serials=tuple(sorted(set(serials)))
        self.handles=[]; self.claimed=False

    def acquire(self):
        with _lock:
            if _held.intersection(self.serials): raise RuntimeError("Receiver already owned in this process")
            _held.update(self.serials); self.claimed=True
        try:
            if os.name!="nt": raise OSError("Receiver ownership requires Windows")
            self.api=C.WinDLL("kernel32",use_last_error=True)
            self.api.CreateMutexW.argtypes=[C.c_void_p,C.c_int,C.c_wchar_p]; self.api.CreateMutexW.restype=C.c_void_p
            self.api.WaitForSingleObject.argtypes=[C.c_void_p,C.c_uint32]; self.api.WaitForSingleObject.restype=C.c_uint32
            self.api.ReleaseMutex.argtypes=[C.c_void_p]; self.api.CloseHandle.argtypes=[C.c_void_p]
            for serial in self.serials:
                name="Local\\LSAC-receiver-"+hashlib.sha256(serial.encode()).hexdigest()
                handle=self.api.CreateMutexW(None,False,name)
                if not handle: raise C.WinError(C.get_last_error())
                result=self.api.WaitForSingleObject(handle,0)
                if result not in (0,0x80):
                    self.api.CloseHandle(handle)
                    raise RuntimeError("Receiver owned by another LSAC process: "+serial)
                self.handles.append(handle)
            return self
        except BaseException:
            self.close(); raise

    def close(self):
        for handle in reversed(self.handles):
            self.api.ReleaseMutex(handle); self.api.CloseHandle(handle)
        self.handles.clear()
        if self.claimed:
            with _lock: _held.difference_update(self.serials)
            self.claimed=False
