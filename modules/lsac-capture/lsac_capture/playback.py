"""Explicit WASAPI render route and bounded reference playback.

No default endpoint, system volume, driver, firmware or audio-effect changes.
Inventory/probe only activates audio clients and queries capabilities. Only
play(..., confirm_playback=True) may initialize/start a render stream.
"""
import ctypes as C
import hashlib
import json
import math
from pathlib import Path
import struct
import threading
import time
import uuid
from dataclasses import dataclass

from .windows import COM, GUID, DWORD, PTR, invoke, method, release, qpc, qpf

PCM_GUID="00000001-0000-0010-8000-00aa00389b71"
FLOAT_GUID="00000003-0000-0010-8000-00aa00389b71"


def third_octave_edges(center_hz=500.):
    """Exact base-2 third-octave edges; no IEC filter-class claim."""
    if not math.isfinite(center_hz) or center_hz<=0: raise ValueError("Invalid center frequency")
    return [center_hz*2**(-1/6),center_hz*2**(1/6)]


def parse_format(raw):
    if len(raw)<18: raise ValueError("Truncated WAVEFORMATEX")
    tag,channels,rate,average,align,bits,extra=struct.unpack_from("<HHIIHHH",raw)
    if len(raw)!=18+extra or not 1<=channels<=32 or not 8000<=rate<=192000:
        raise ValueError("Invalid WAVEFORMATEX dimensions")
    valid=bits; mask=0; subformat=None
    if tag==0xfffe:
        if extra<22: raise ValueError("Truncated WAVEFORMATEXTENSIBLE")
        valid,mask=struct.unpack_from("<HI",raw,18)
        subformat=str(uuid.UUID(bytes_le=raw[24:40]))
        encoding={PCM_GUID:"PCM",FLOAT_GUID:"FLOAT"}.get(subformat)
    else: encoding={1:"PCM",3:"FLOAT"}.get(tag)
    if encoding not in ("PCM","FLOAT") or bits not in (16,24,32) or not 1<=valid<=bits:
        raise ValueError("Unsupported render encoding")
    if encoding=="FLOAT" and (bits!=32 or valid!=32): raise ValueError("Only float32 supported")
    if align!=channels*(bits//8) or average!=rate*align: raise ValueError("Inconsistent frame layout")
    return dict(tag=tag,channels=channels,rate=rate,bytes_per_second=average,align=align,
                container_bits=bits,valid_bits=valid,channel_mask=mask,subformat=subformat,encoding=encoding)


def _activate(com,endpoint_id):
    # Verify flow before activation: capture endpoints must never become render routes.
    rows={r["endpoint_id"]:r for r in com.endpoints() if r["flow"]=="render"}
    if endpoint_id not in rows: raise ValueError("Explicit active render endpoint required")
    dev=com.device(endpoint_id); client=PTR()
    try:
        invoke(dev,3,(C.POINTER(GUID),DWORD,PTR,C.POINTER(PTR)),
               (C.byref(GUID("1cb9ad4c-dbfa-4c32-b178-c2f568a703b2")),23,None,C.byref(client)))
    except BaseException:
        release(dev); raise
    return dev,client,rows[endpoint_id]


def probe_render(endpoint_id):
    """Read-only capability query: never Initialize, Start or change settings."""
    with COM() as com:
        dev,client,row=_activate(com,endpoint_id)
        try:
            mix=PTR(); invoke(client,8,(C.POINTER(PTR),),(C.byref(mix),))
            try:
                header=C.string_at(mix,18); extra=struct.unpack_from("<H",header,16)[0]
                if extra>256: raise ValueError("Unreasonable endpoint format extension")
                raw=C.string_at(mix,18+extra); parsed=parse_format(raw)
            finally: com.ole.CoTaskMemFree(mix)
            formats=[]; fmt=C.create_string_buffer(raw)
            for mode,name in ((0,"shared"),(1,"exclusive")):
                close=PTR()
                try:
                    hr=method(client,7,(C.c_int,PTR,C.POINTER(PTR)))(client,mode,fmt,C.byref(close))
                    formats.append(dict(mode=name,exact=hr==0,hresult=f"0x{hr & 0xffffffff:08x}"))
                finally:
                    if close: com.ole.CoTaskMemFree(close)
            period=C.c_int64(); minimum=C.c_int64()
            invoke(client,9,(C.POINTER(C.c_int64),C.POINTER(C.c_int64)),(C.byref(period),C.byref(minimum)))
            return dict(schema=1,**row,mix_format=parsed,format_hex=raw.hex(),formats=formats,
                        default_period_100ns=period.value,minimum_period_100ns=minimum.value,
                        stream_started=False,physical_speaker_coverage_verified=False,
                        routing_note="Reference metadata declares fixed endpoint channel gains. Physical drivers/DSP routing remain unverified.")
        finally: release(client); release(dev)


def inventory_render():
    with COM() as com: rows=[r for r in com.endpoints() if r["flow"]=="render"]
    result=[]
    for row in rows:
        try: result.append(probe_render(row["endpoint_id"]))
        except (OSError,ValueError) as exc: result.append(dict(**row,error=repr(exc),stream_started=False))
    return dict(schema=1,render_endpoints=result,stream_started=False,
                system_defaults_changed=False,physical_speaker_coverage_verified=False)


def band_edges(center_hz,band_octaves=1/3):
    if not math.isfinite(band_octaves) or not 0<band_octaves<=1:raise ValueError('Band width must be in (0,1] octaves')
    if not math.isfinite(center_hz) or center_hz<=0:raise ValueError('Invalid center frequency')
    return [center_hz*2**(-band_octaves/2),center_hz*2**(band_octaves/2)]


def validate_reference_parameters(rate,channels,seconds,peak_dbfs,center_hz,band_octaves=1/3):
    if type(rate) is not int or not 8000<=rate<=192000: raise ValueError("Unsupported sample rate")
    if type(channels) is not int or not 1<=channels<=32: raise ValueError("Invalid channel count")
    if not math.isfinite(seconds) or not .1<=seconds<=600: raise ValueError("Duration must be .1..600 seconds")
    if not math.isfinite(peak_dbfs) or not -80<=peak_dbfs<=-12: raise ValueError("Pilot peak must be -80..-12 dBFS")
    if not 20<=center_hz or band_edges(center_hz,band_octaves)[1]>=rate/2:
        raise ValueError("Band must lie below Nyquist")


def reference_samples(rate=48000,channels=2,seconds=10.,peak_dbfs=-60.,seed=20261101,center_hz=500.,channel_gains=None,band_octaves=1/3):
    """Deterministic FFT-band noise; optional fixed, non-amplifying channel gains."""
    import numpy as np
    validate_reference_parameters(rate,channels,seconds,peak_dbfs,center_hz,band_octaves)
    if type(seed) is not int or not 0<=seed<2**64: raise ValueError("Expected uint64 seed")
    n=round(rate*seconds); rng=np.random.default_rng(seed)
    period=min(n,rate*60)
    bins=np.fft.rfftfreq(period,1/rate); low,high=band_edges(center_hz,band_octaves)
    spectrum=np.fft.rfft(rng.normal(size=period)); spectrum[(bins<low)|(bins>high)]=0
    mono=np.fft.irfft(spectrum,n=period)
    # FFT synthesis defines a periodic bandlimited signal. Repeat the unfaded
    # period, then fade only the outer ends: no once-a-minute silent seams.
    if n>period:mono=np.resize(mono,n)
    ramp=min(round(rate*.05),n//4)
    if ramp:
        fade=np.sin(np.linspace(0,math.pi/2,ramp))**2
        mono[:ramp]*=fade; mono[-ramp:]*=fade[::-1]
    maximum=float(np.max(np.abs(mono)))
    if maximum==0: raise ValueError("No FFT bin in requested band")
    mono*=10**(peak_dbfs/20)/maximum
    gains=np.ones(channels) if channel_gains is None else np.asarray(channel_gains,dtype=float)
    if gains.shape!=(channels,) or not np.isfinite(gains).all() or np.any(gains<0) or np.any(gains>1) or not np.any(gains>0):
        raise ValueError("Channel gains must match channels, lie in 0..1 and include a nonzero route")
    return (mono[:,None]*gains[None,:]).astype("<f4")


def prepare_reference(out,rate=48000,channels=2,seconds=10.,peak_dbfs=-60.,seed=20261101,center_hz=500.,channel_gains=None,band_octaves=1/3):
    import numpy as np
    samples=reference_samples(rate,channels,seconds,peak_dbfs,seed,center_hz,channel_gains,band_octaves)
    folder=Path(out); folder.mkdir(parents=True,exist_ok=False)
    path=folder/"reference.npy"; np.save(path,samples)
    with path.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
    result=dict(schema=1,kind="third_octave_band_noise" if band_octaves==1/3 else 'octave_band_noise',center_hz=center_hz,
                band_octaves=band_octaves,band_hz=band_edges(center_hz,band_octaves),band_definition="base2_center_times_2_pow_plus_minus_band_octaves_over_2",
                rate=rate,channels=channels,frames=len(samples),seconds=len(samples)/rate,
                peak_dbfs=peak_dbfs,seed=seed,numpy_version=np.__version__,fade_seconds=min(.05,len(samples)/rate/4),
                sha256=digest,all_channels_identical=bool(np.all(samples==samples[:,0:1])),
                periodic_base_frames=min(len(samples),rate*60),outer_fades_only=True,
                channel_gains=list(channel_gains) if channel_gains is not None else [1.]*channels,
                physical_channel_mapping_verified=False,
                status="generated_not_played",note="Fade produces spectral skirts; not an IEC filter-class or SPL calibration.")
    (folder/"reference.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    return result


def encode_samples(samples,fmt):
    """Encode float reference exactly into the requested native endpoint layout."""
    import numpy as np
    values=np.asarray(samples)
    if values.ndim!=2 or not len(values) or values.shape[1]!=fmt["channels"] or not np.all(np.isfinite(values)):
        raise ValueError("Invalid interleaved reference")
    if np.max(np.abs(values),initial=0)>1: raise ValueError("Reference clips")
    if fmt["encoding"]=="FLOAT": return values.astype("<f4",copy=False).tobytes()
    bits=fmt["container_bits"]; valid=fmt["valid_bits"]
    # WAVEFORMATEXTENSIBLE valid integer bits are left-aligned in their container.
    integers=np.rint(values.astype(np.float64)*((1<<(valid-1))-1)).astype(np.int64)
    integers <<= bits-valid
    if bits==16: return integers.astype("<i2").tobytes()
    if bits==32: return integers.astype("<i4").tobytes()
    return np.stack((integers&255,(integers>>8)&255,(integers>>16)&255),axis=-1).astype(np.uint8).tobytes()


def _load_reference(folder,plan):
    import numpy as np
    folder=Path(folder); metadata=json.loads((folder/"reference.json").read_text(encoding="utf-8"))
    path=folder/"reference.npy"
    with path.open('rb') as source:digest=hashlib.file_digest(source,'sha256').hexdigest()
    if digest!=metadata["sha256"]: raise ValueError("Reference hash mismatch")
    fmt=parse_format(bytes.fromhex(plan["format_hex"]))
    samples=np.load(path,allow_pickle=False,mmap_mode='r')
    if metadata.get('kind')=='music_noise_bandlimited':
        from .music_noise import validate_metadata
        validate_metadata(metadata)
    else:
        validate_reference_parameters(metadata["rate"],metadata["channels"],metadata["seconds"],metadata["peak_dbfs"],metadata["center_hz"],metadata.get('band_octaves',1/3))
        if not np.allclose(metadata['band_hz'],band_edges(metadata['center_hz'],metadata.get('band_octaves',1/3))):raise ValueError('Band metadata disagrees')
    if metadata["rate"]!=fmt["rate"] or samples.shape!=(metadata["frames"],fmt["channels"]):
        raise ValueError("Reference and endpoint format differ; resampling/downmix refused")
    if metadata["channels"]!=fmt["channels"] or len(samples)!=round(metadata["seconds"]*fmt["rate"]):
        raise ValueError("Reference dimensions disagree")
    gains=np.asarray(metadata.get("channel_gains",[1.]*fmt["channels"]),dtype=float)
    if gains.shape!=(fmt['channels'],) or not np.isfinite(gains).all() or np.any(gains<0) or np.any(gains>1) or not np.any(gains>0):
        raise ValueError("Invalid fixed channel gain metadata")
    basis=int(np.argmax(gains))
    for start in range(0,len(samples),metadata['rate']):
        chunk=samples[start:start+metadata['rate']]
        expected=chunk[:,basis:basis+1].astype(float)*gains[None,:]/gains[basis]
        if not np.allclose(chunk,expected,rtol=5e-7,atol=1e-12):raise ValueError("Reference does not match fixed channel gains")
    peak=float(np.max(np.abs(samples),initial=0))
    if not math.isfinite(peak) or peak>10**(metadata["peak_dbfs"]/20)*(1+1e-6):
        raise ValueError("Reference exceeds declared pilot level")
    return metadata,fmt,encode_samples(samples,fmt)


@dataclass(frozen=True)
class PreparedReference:
    folder: str
    format_hex: str
    metadata: dict
    fmt: dict
    raw: bytes


def prepare_playback_reference(plan,folder):
    """File validation/large encoding only, before any live Session exists."""
    metadata,fmt,raw=_load_reference(folder,plan)
    return PreparedReference(str(Path(folder).resolve()),plan['format_hex'],metadata,fmt,raw)


def play(plan,reference_folder,out,mode="shared",confirm_playback=False,stop=None,submit=None,prepared_reference=None):
    """Bounded render; explicit confirmation checked before file/native side effects.

    plan is the saved probe_render result. STOP in out interrupts immediately.
    submit, when supplied, receives (event,None) and must be nonblocking.
    """
    if not confirm_playback: raise ValueError("Playback requires explicit --confirm-playback")
    if mode not in ("shared","exclusive"): raise ValueError("Choose shared or exclusive mode")
    if not plan.get("endpoint_id") or plan.get("flow")!="render": raise ValueError("Explicit render plan required")
    if not any(r.get("mode")==mode and r.get("exact") for r in plan.get("formats",[])):
        raise ValueError("Plan does not support exact requested format/mode")
    if prepared_reference is None:
        metadata,fmt,raw=_load_reference(reference_folder,plan)
    else:
        prepared=prepared_reference
        if not isinstance(prepared,PreparedReference) or prepared.folder!=str(Path(reference_folder).resolve()) or prepared.format_hex!=plan['format_hex']:
            raise ValueError('Prepared reference does not match route/source')
        current=json.loads((Path(reference_folder)/'reference.json').read_text(encoding='utf-8'))
        if current!=prepared.metadata:raise ValueError('Reference metadata changed after preparation')
        metadata,fmt,raw=prepared.metadata,prepared.fmt,prepared.raw
        if len(raw)!=metadata['frames']*fmt['align']:raise ValueError('Prepared reference length mismatch')
    out=Path(out); out.mkdir(parents=True,exist_ok=False); stop=stop or threading.Event()
    result=dict(schema=1,status="starting",endpoint_id=plan["endpoint_id"],mode=mode,reference=metadata,
                format=fmt,physical_speaker_coverage_verified=False,queued_frames=0,
                observed_empty_buffer_before_all_frames_queued=0,
                timestamp_basis="host_QPC_at_API_calls_not_acoustic_DAC_start",qpc_frequency=qpf())
    (out/"playback-plan.json").write_text(json.dumps(dict(route=plan,reference=metadata,mode=mode),indent=2),encoding="utf-8")
    with (out/"playback-events.jsonl").open("x",encoding="utf-8",buffering=1) as log:
        def emit(event,unused=None):
            event=dict(event); event.setdefault("qpc_i64",qpc()); log.write(json.dumps(event,allow_nan=False)+"\n")
            if submit and submit(event,None) is False: stop.set()
            return not stop.is_set()
        try:
            from .scheduling import CaptureMMCSS
            with CaptureMMCSS("wasapi_reference_render",emit):
                _render(plan,fmt,raw,metadata,out,mode,stop,emit,result)
        except BaseException as exc:
            result.update(status="failed",error=repr(exc)); raise
        finally:
            result["ended_qpc_i64"]=qpc()
            (out/"playback-result.json").write_text(json.dumps(result,indent=2,allow_nan=False),encoding="utf-8")
    return result


def _render(plan,fmt,raw,metadata,out,mode,stop,emit,result):
    with COM() as com:
        dev,client,_=_activate(com,plan["endpoint_id"]); writer=PTR(); started=False
        try:
            wave=C.create_string_buffer(bytes.fromhex(plan["format_hex"])); share=0 if mode=="shared" else 1
            closest=PTR()
            try:
                hr=method(client,7,(C.c_int,PTR,C.POINTER(PTR)))(client,share,wave,C.byref(closest))
                if hr!=0: raise ValueError("Native endpoint no longer supports exact planned format")
            finally:
                if closest: com.ole.CoTaskMemFree(closest)
            period=C.c_int64(); minimum=C.c_int64()
            invoke(client,9,(C.POINTER(C.c_int64),C.POINTER(C.c_int64)),(C.byref(period),C.byref(minimum)))
            if period.value<=0: raise ValueError("Invalid endpoint period")
            duration=math.ceil(max(1000000,period.value*4)/period.value)*period.value
            invoke(client,3,(C.c_int,DWORD,C.c_int64,C.c_int64,PTR,PTR),
                   (share,0,duration,period.value if share else 0,wave,None))
            capacity=DWORD(); latency=C.c_int64()
            invoke(client,4,(C.POINTER(DWORD),),(C.byref(capacity),))
            invoke(client,5,(C.POINTER(C.c_int64),),(C.byref(latency),))
            if not capacity.value: raise ValueError("Empty render buffer")
            invoke(client,14,(C.POINTER(GUID),C.POINTER(PTR)),
                   (C.byref(GUID("f294acfc-3146-4483-a7bf-addca7c260e2")),C.byref(writer)))
            result.update(buffer_capacity_frames=capacity.value,reported_stream_latency_100ns=latency.value,
                          requested_buffer_duration_100ns=duration,device_period_100ns=period.value)
            emit(dict(kind="playback_stream_configuration",**result))
            cursor=0; total=metadata["frames"]; align=fmt["align"]
            def fill(frames,padding):
                nonlocal cursor
                data=PTR(); invoke(writer,3,(DWORD,C.POINTER(PTR)),(frames,C.byref(data)))
                try:
                    if not data: raise RuntimeError("Render GetBuffer returned NULL")
                    C.memmove(data,raw[cursor*align:(cursor+frames)*align],frames*align)
                except BaseException:
                    invoke(writer,4,(DWORD,DWORD),(frames,2)); raise
                invoke(writer,4,(DWORD,DWORD),(frames,0))
                emit(dict(kind="playback_queued",sample_start_i64=cursor,frames=frames,padding_before_frames=padding))
                cursor+=frames; result["queued_frames"]=cursor
            if stop.is_set() or (out/"STOP").exists(): result["status"]="cancelled"; return
            fill(min(capacity.value,total),0)
            if stop.is_set(): result["status"]="cancelled"; return
            before=qpc(); invoke(client,10); started=True; after=qpc()
            result.update(status="playing",start_call_before_qpc_i64=before,start_call_after_qpc_i64=after)
            emit(dict(kind="playback_started",start_call_before_qpc_i64=before,start_call_after_qpc_i64=after))
            deadline=time.monotonic()+metadata["seconds"]+3
            while True:
                if stop.is_set() or (out/"STOP").exists(): result["status"]="cancelled"; break
                if time.monotonic()>deadline: raise TimeoutError("Render drain exceeded bounded duration")
                padding=DWORD(); invoke(client,6,(C.POINTER(DWORD),),(C.byref(padding),))
                if padding.value>capacity.value: raise RuntimeError("Invalid render padding")
                if cursor==total and padding.value==0:
                    result["status"]="completed_not_acoustically_verified"; break
                if cursor<total:
                    if padding.value==0:
                        result["observed_empty_buffer_before_all_frames_queued"]+=1
                        emit(dict(kind="playback_starvation_observed",sample_cursor_i64=cursor))
                    frames=min(capacity.value-padding.value,total-cursor)
                    if frames: fill(frames,padding.value)
                time.sleep(.002)
            result["drained_qpc_i64"]=qpc()
            emit(dict(kind="playback_finished",status=result["status"],queued_frames=cursor))
        finally:
            try:
                if started: invoke(client,11)
            finally:
                release(writer); release(client); release(dev)
