"""Windows OS enumeration and strict WASAPI PCM capture. No default changes.

GetBuffer supplies *first frame* device position and QPC in 100-ns units.
Enumeration never calls libsurvive or issues HID feature reports.
"""
import ctypes as C
import json
import queue
import struct
import sys
import time
import uuid

DWORD=C.c_uint32; U64=C.c_uint64; PTR=C.c_void_p; HRESULT=C.c_int32


class GUID(C.Structure):
    _fields_=[("bytes",C.c_ubyte*16)]
    def __init__(self, value):
        super().__init__(); self.bytes[:]=uuid.UUID(value).bytes_le


def checked(hr):
    if hr < 0:
        raise OSError(f"HRESULT 0x{hr & 0xffffffff:08x}")
    return hr


def method(obj,index,types=(),result=HRESULT):
    ptr=C.cast(obj,C.POINTER(C.POINTER(PTR)))[0][index]
    return C.WINFUNCTYPE(result,PTR,*types)(ptr)


def invoke(obj,index,types=(),args=()):
    return checked(method(obj,index,types)(obj,*args))


def release(obj):
    if obj:
        method(obj,2,(),C.c_uint32)(obj)


class COM:
    def __enter__(self):
        self.ole=C.OleDLL("ole32")
        self.ole.CoInitializeEx.argtypes=[PTR,DWORD]
        checked(self.ole.CoInitializeEx(None,0))
        self.ole.CoCreateInstance.argtypes=[C.POINTER(GUID),PTR,DWORD,C.POINTER(GUID),C.POINTER(PTR)]
        self.enum=PTR()
        checked(self.ole.CoCreateInstance(C.byref(GUID("bcde0395-e52f-467c-8e3d-c4579291692e")),None,1,
                 C.byref(GUID("a95664d2-9614-4f35-a746-de8db63617e6")),C.byref(self.enum)))
        return self
    def __exit__(self,*args):
        release(self.enum); self.ole.CoUninitialize()

    def device(self,id):
        dev=PTR()
        invoke(self.enum,5,(C.c_wchar_p,C.POINTER(PTR)),(id,C.byref(dev)))
        return dev

    def endpoints(self):
        rows=[]
        for flow,label in ((0,"render"),(1,"capture")):
            collection=PTR()
            invoke(self.enum,3,(C.c_int,DWORD,C.POINTER(PTR)),(flow,1,C.byref(collection)))
            try:
                count=DWORD(); invoke(collection,3,(C.POINTER(DWORD),),(C.byref(count),))
                for i in range(count.value):
                    dev=PTR(); invoke(collection,4,(DWORD,C.POINTER(PTR)),(i,C.byref(dev)))
                    id=PTR(); store=PTR()
                    try:
                        invoke(dev,5,(C.POINTER(PTR),),(C.byref(id),))
                        name=None
                        invoke(dev,4,(DWORD,C.POINTER(PTR)),(0,C.byref(store)))
                        # PROPERTYKEY PKEY_Device_FriendlyName, VT_LPWSTR PROPVARIANT.
                        key=C.create_string_buffer(uuid.UUID("a45c254e-df1c-4efd-8020-67d146a850e0").bytes_le+struct.pack("<I",14))
                        value=C.create_string_buffer(24)
                        invoke(store,5,(PTR,PTR),(key,value))
                        if struct.unpack_from("<H",value.raw)[0]==31:
                            name=C.wstring_at(PTR.from_buffer(value,8).value)
                        self.ole.PropVariantClear(value)
                        rows.append(dict(flow=label,endpoint_id=C.wstring_at(id),name=name))
                    finally:
                        release(store)
                        if id: self.ole.CoTaskMemFree(id)
                        release(dev)
            finally:
                release(collection)
        return rows


def qpc():
    value=C.c_int64(); C.windll.kernel32.QueryPerformanceCounter(C.byref(value)); return value.value


def qpf():
    value=C.c_int64(); C.windll.kernel32.QueryPerformanceFrequency(C.byref(value)); return value.value


def pcm_format(a):
    size=a["container_bits"]//8; align=a["channels"]*size
    # WAVEFORMATEXTENSIBLE, no speaker mask: four Ambisonic channels are not quad speakers.
    raw=struct.pack("<HHIIHHHHI",0xfffe,a["channels"],a["rate"],a["rate"]*align,align,
                    a["container_bits"],22,a["valid_bits"],0)
    return C.create_string_buffer(raw+uuid.UUID("00000001-0000-0010-8000-00aa00389b71").bytes_le)


def probe_format(id):
    """Activate + IsFormatSupported only. Does not Initialize/Start a stream."""
    with COM() as com:
        dev=com.device(id); client=PTR()
        try:
            invoke(dev,3,(C.POINTER(GUID),DWORD,PTR,C.POINTER(PTR)),
                   (C.byref(GUID("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")),23,None,C.byref(client)))
            mix=PTR(); invoke(client,8,(C.POINTER(PTR),),(C.byref(mix),))
            try:
                raw=C.string_at(mix,18)
                mix_report=dict(zip(("tag","channels","rate","bytes_per_second","align","container_bits","extra"),struct.unpack_from("<HHIIHHH",raw)))
                if mix_report["tag"]==65534:
                    raw=C.string_at(mix,40)
                    mix_report.update(valid_bits=struct.unpack_from("<H",raw,18)[0],subformat=str(uuid.UUID(bytes_le=raw[24:40])))
            finally: com.ole.CoTaskMemFree(mix)
            reports=[]
            for mode in (0,1):
                for bits in (24,32):
                    fmt=pcm_format(dict(channels=4,rate=48000,container_bits=bits,valid_bits=24)); close=PTR()
                    hr=method(client,7,(C.c_int,PTR,C.POINTER(PTR)))(client,mode,fmt,C.byref(close))
                    reports.append(dict(mode="shared" if mode==0 else "exclusive",channels=4,rate=48000,container_bits=bits,valid_bits=24,
                                        hresult=f"0x{hr & 0xffffffff:08x}",exact=hr==0))
                    if close: com.ole.CoTaskMemFree(close)
            return dict(endpoint_id=id,mix_format=mix_report,formats=reports,stream_started=False)
        finally: release(client); release(dev)


def capture(a, stop, submit, ready):
    from .scheduling import CaptureMMCSS
    with CaptureMMCSS('wasapi_capture',submit):
        return _capture(a,stop,submit,ready)


def _capture(a, stop, submit, ready):
    """Capture thread: bounded copies only. submit must be nonblocking.

    No sample-rate conversion or downmix flags. Shared mode refusal fails
    closed rather than silently accepting a nearest format.
    """
    with COM() as com:
        dev=com.device(a["endpoint_id"]); client=PTR(); reader=PTR(); started=False
        try:
            invoke(dev,3,(C.POINTER(GUID),DWORD,PTR,C.POINTER(PTR)),
                   (C.byref(GUID("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")),23,None,C.byref(client)))
            fmt=pcm_format(a); closest=PTR()
            mode=1 if a.get("mode")=="exclusive" else 0
            status=invoke(client,7,(C.c_int,PTR,C.POINTER(PTR)),(mode,fmt,C.byref(closest)))
            if closest: com.ole.CoTaskMemFree(closest)
            if status != 0: raise ValueError("Exact requested 4ch PCM format unsupported; nearest format refused")
            period=C.c_int64(); minimum=C.c_int64()
            invoke(client,9,(C.POINTER(C.c_int64),C.POINTER(C.c_int64)),(C.byref(period),C.byref(minimum)))
            duration=max(period.value*4,1000000)
            duration=((duration+period.value-1)//period.value)*period.value
            invoke(client,3,(C.c_int,DWORD,C.c_int64,C.c_int64,PTR,PTR),(mode,0,duration,period.value if mode else 0,fmt,None))
            capacity=DWORD(); latency=C.c_int64()
            invoke(client,4,(C.POINTER(DWORD),),(C.byref(capacity),))
            invoke(client,5,(C.POINTER(C.c_int64),),(C.byref(latency),))
            submit(dict(kind="audio_stream_configuration",endpoint_id=a["endpoint_id"],mode=a.get("mode"),
                        requested_duration_100ns=duration,device_period_100ns=period.value,
                        buffer_capacity_frames=capacity.value,reported_stream_latency_100ns=latency.value,
                        note="Capacity and API stream latency are not independently measured acoustic/input latency"),None)
            invoke(client,14,(C.POINTER(GUID),C.POINTER(PTR)),
                   (C.byref(GUID("c8adbd64-e71e-48a0-a4de-185c395cd317")),C.byref(reader)))
            invoke(client,10); started=True; ready.set()
            logical=0; expected=None
            align=a["channels"]*(a["container_bits"]//8)
            while not stop.is_set():
                n=DWORD(); invoke(reader,5,(C.POINTER(DWORD),),(C.byref(n),))
                if not n.value:
                    time.sleep(.002); continue
                data=PTR(); frames=DWORD(); flags=DWORD(); pos=U64(); stamp=U64()
                invoke(reader,3,(C.POINTER(PTR),C.POINTER(DWORD),C.POINTER(DWORD),C.POINTER(U64),C.POINTER(U64)),
                       (C.byref(data),C.byref(frames),C.byref(flags),C.byref(pos),C.byref(stamp)))
                try:
                    host=qpc()
                    raw=bytes(frames.value*align) if flags.value & 2 else C.string_at(data,frames.value*align)
                    event=dict(kind="audio_block",sample_start_i64=logical,frames=frames.value,
                               device_position_u64=pos.value,device_qpc_100ns_u64=stamp.value,
                               arrival_qpc_i64=host,qpc_frequency=qpf(),flags=flags.value,
                               discontinuity=bool(flags.value & 1),silent=bool(flags.value & 2),
                               timestamp_error=bool(flags.value & 4),device_gap_frames=0 if expected is None else pos.value-expected,
                               epoch=0,rate=a["rate"],channels=a["channels"],container_bits=a["container_bits"],valid_bits=a["valid_bits"])
                    if not submit(event,raw): stop.set()
                    logical+=frames.value; expected=pos.value+frames.value
                finally:
                    invoke(reader,4,(DWORD,),(frames.value,))
        finally:
            if started: invoke(client,11)
            release(reader); release(client); release(dev)


class InterfaceData(C.Structure):
    _fields_=[("size",DWORD),("guid",GUID),("flags",DWORD),("reserved",C.c_size_t)]


def hid_inventory(path_filters=None):
    """SetupAPI interface paths + read-only HID attributes/serial strings.

    CreateFile desired access 0. No ReadFile loop, WriteFile, feature reports,
    pairing, firmware operation or driver binding.
    """
    setup=C.WinDLL("setupapi",use_last_error=True); hid=C.WinDLL("hid",use_last_error=True); kernel=C.WinDLL("kernel32",use_last_error=True)
    guid=GUID("00000000-0000-0000-0000-000000000000"); hid.HidD_GetHidGuid(C.byref(guid))
    setup.SetupDiGetClassDevsW.argtypes=[C.POINTER(GUID),PTR,PTR,DWORD]; setup.SetupDiGetClassDevsW.restype=PTR
    setup.SetupDiEnumDeviceInterfaces.argtypes=[PTR,PTR,C.POINTER(GUID),DWORD,C.POINTER(InterfaceData)]
    setup.SetupDiGetDeviceInterfaceDetailW.argtypes=[PTR,C.POINTER(InterfaceData),PTR,DWORD,C.POINTER(DWORD),PTR]
    setup.SetupDiDestroyDeviceInfoList.argtypes=[PTR]
    kernel.CreateFileW.argtypes=[C.c_wchar_p,DWORD,DWORD,PTR,DWORD,DWORD,PTR]; kernel.CreateFileW.restype=PTR
    kernel.CloseHandle.argtypes=[PTR]
    hid.HidD_GetAttributes.argtypes=[PTR,PTR]; hid.HidD_GetSerialNumberString.argtypes=[PTR,PTR,DWORD]
    handle=setup.SetupDiGetClassDevsW(C.byref(guid),None,None,0x12)
    invalid=PTR(-1).value
    if handle==invalid: raise C.WinError(C.get_last_error())
    rows=[]
    class Attributes(C.Structure):
        _fields_=[("size",DWORD),("vid",C.c_ushort),("pid",C.c_ushort),("version",C.c_ushort)]
    try:
        index=0
        while True:
            info=InterfaceData(); info.size=C.sizeof(info)
            if not setup.SetupDiEnumDeviceInterfaces(handle,None,C.byref(guid),index,C.byref(info)):
                if C.get_last_error()==259: break
                raise C.WinError(C.get_last_error())
            index+=1; needed=DWORD()
            setup.SetupDiGetDeviceInterfaceDetailW(handle,C.byref(info),None,0,C.byref(needed),None)
            detail=C.create_string_buffer(needed.value); struct.pack_into("<I",detail,0,8 if C.sizeof(PTR)==8 else 6)
            if not setup.SetupDiGetDeviceInterfaceDetailW(handle,C.byref(info),detail,needed.value,C.byref(needed),None):
                raise C.WinError(C.get_last_error())
            path=C.wstring_at(C.addressof(detail)+4)
            # Scope to Valve/HTC/Zoom candidates, not unrelated keyboard input.
            filters=path_filters or ("vid_28de","vid_0bb4","vid_1686")
            if not any(k in path.lower() for k in filters): continue
            row=dict(interface_path=path)
            f=kernel.CreateFileW(path,0,3,None,3,0,None)
            if f==invalid:
                row["open_error"]=C.get_last_error()
            else:
                try:
                    attr=Attributes(); attr.size=C.sizeof(attr)
                    if hid.HidD_GetAttributes(f,C.byref(attr)): row.update(vid=hex(attr.vid),pid=hex(attr.pid),version=attr.version)
                    serial=C.create_unicode_buffer(256)
                    if hid.HidD_GetSerialNumberString(f,serial,C.sizeof(serial)): row["serial"]=serial.value
                finally: kernel.CloseHandle(f)
            rows.append(row)
    finally: setup.SetupDiDestroyDeviceInfoList(handle)
    return rows


def inventory():
    if sys.platform!="win32": raise RuntimeError("Windows only")
    with COM() as com: audio=com.endpoints()
    return dict(machine=__import__("socket").gethostname(),audio=audio,hid=hid_inventory(),usb=usb_inventory(),
                note="OS enumeration only; no stream/feature report. Optical station generation/count and pairing not inferred.")


def usb_inventory():
    setup=C.WinDLL("setupapi",use_last_error=True)
    setup.SetupDiGetClassDevsW.argtypes=[C.POINTER(GUID),C.c_wchar_p,PTR,DWORD]; setup.SetupDiGetClassDevsW.restype=PTR
    setup.SetupDiEnumDeviceInfo.argtypes=[PTR,DWORD,C.POINTER(InterfaceData)]
    setup.SetupDiGetDeviceInstanceIdW.argtypes=[PTR,C.POINTER(InterfaceData),C.c_wchar_p,DWORD,C.POINTER(DWORD)]
    setup.SetupDiGetDeviceRegistryPropertyW.argtypes=[PTR,C.POINTER(InterfaceData),DWORD,C.POINTER(DWORD),PTR,DWORD,C.POINTER(DWORD)]
    setup.SetupDiDestroyDeviceInfoList.argtypes=[PTR]
    devices=setup.SetupDiGetClassDevsW(None,"USB",None,6)
    if devices==PTR(-1).value: raise C.WinError(C.get_last_error())
    rows=[]
    try:
        i=0
        while True:
            dev=InterfaceData(); dev.size=C.sizeof(dev)
            if not setup.SetupDiEnumDeviceInfo(devices,i,C.byref(dev)):
                if C.get_last_error()==259: break
                raise C.WinError(C.get_last_error())
            i+=1; id=C.create_unicode_buffer(1024); needed=DWORD()
            if not setup.SetupDiGetDeviceInstanceIdW(devices,C.byref(dev),id,1024,C.byref(needed)): continue
            row=dict(instance_id=id.value)
            for property,name in ((0,"description"),(11,"manufacturer"),(12,"friendly_name")):
                value=C.create_unicode_buffer(1024); t=DWORD()
                if setup.SetupDiGetDeviceRegistryPropertyW(devices,C.byref(dev),property,C.byref(t),value,C.sizeof(value),C.byref(needed)):
                    row[name]=value.value
            rows.append(row)
    finally: setup.SetupDiDestroyDeviceInfoList(devices)
    return rows
