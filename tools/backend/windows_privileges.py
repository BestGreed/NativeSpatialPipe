"""Enable an already-granted Windows privilege; never elevates a process."""
import ctypes as C


def enable_debug():
    class Luid(C.Structure):
        _fields_ = [('low', C.c_uint32), ('high', C.c_int32)]
    class Privilege(C.Structure):
        _fields_ = [('count', C.c_uint32), ('luid', Luid), ('attributes', C.c_uint32)]
    kernel = C.WinDLL('kernel32', use_last_error=True)
    advapi = C.WinDLL('advapi32', use_last_error=True)
    kernel.CloseHandle.argtypes = [C.c_void_p]
    advapi.OpenProcessToken.argtypes = [C.c_void_p, C.c_uint32, C.POINTER(C.c_void_p)]
    advapi.LookupPrivilegeValueW.argtypes = [C.c_wchar_p, C.c_wchar_p, C.POINTER(Luid)]
    advapi.AdjustTokenPrivileges.argtypes = [C.c_void_p, C.c_int, C.POINTER(Privilege), C.c_uint32, C.c_void_p, C.c_void_p]
    token = C.c_void_p()
    if not advapi.OpenProcessToken(C.c_void_p(-1), 0x28, C.byref(token)):
        return False
    try:
        value = Privilege(count=1, attributes=2)
        if not advapi.LookupPrivilegeValueW(None, 'SeDebugPrivilege', C.byref(value.luid)):
            return False
        C.set_last_error(0)
        return bool(advapi.AdjustTokenPrivileges(token, False, C.byref(value), 0, None, None)) and C.get_last_error() == 0
    finally:
        kernel.CloseHandle(token)
