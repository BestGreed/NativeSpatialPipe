"""Temporary priority class for this observer process only."""
import ctypes

class HostPriority:
    CLASSES={'Normal':0x20,'AboveNormal':0x8000,'High':0x80}
    def __init__(self,name):
        self.name=name;self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.k.GetCurrentProcess.restype=ctypes.c_void_p
        self.k.GetPriorityClass.argtypes=[ctypes.c_void_p];self.k.GetPriorityClass.restype=ctypes.c_uint32
        self.k.SetPriorityClass.argtypes=[ctypes.c_void_p,ctypes.c_uint32];self.k.SetPriorityClass.restype=ctypes.c_int
        self.handle=self.k.GetCurrentProcess();self.before=self.k.GetPriorityClass(self.handle)
        if not self.before:raise ctypes.WinError(ctypes.get_last_error())
        self.target=self.CLASSES[name]
    def apply(self):
        if not self.k.SetPriorityClass(self.handle,self.target):raise ctypes.WinError(ctypes.get_last_error())
        current=self.k.GetPriorityClass(self.handle)
        if current!=self.target:raise RuntimeError('Priority verification failed')
        return {'scope':'Observer process only; audiodg and Wwise unchanged','requested':self.name,'before':self.before,'during':current}
    def restore(self):
        if not self.k.SetPriorityClass(self.handle,self.before):raise ctypes.WinError(ctypes.get_last_error())
        current=self.k.GetPriorityClass(self.handle)
        return {'after':current,'restored':current==self.before}
