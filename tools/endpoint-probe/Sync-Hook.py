"""Capture control host. Current C/Rust entry: Run-Native.py; legacy Frida is optional."""
import ctypes
import hashlib
import json
import pathlib
import sys
import threading
import time
import zlib
import importlib.util
import os

ROOT = pathlib.Path(__file__).resolve().parents[2]
_retained_native_resources=[]
if '--native-hook' not in sys.argv:
    if not (ROOT / 'work/frida/package/frida').is_dir():
        raise RuntimeError('Legacy Frida dependency is retired. Use Run-Native.py for the current C/Rust pipeline; see docs/dependencies.md to restore historical experiments.')
    sys.path.insert(0, str(ROOT / 'work/frida/package'))
    import frida

def main():
    out = pathlib.Path(sys.argv[1]).resolve()
    out.mkdir(parents=True, exist_ok=False)
    report = {'scope': 'temporary-user-mode-hook-no-audio-or-property-writes', 'records': [],'hostPid':os.getpid()}
    session = script = None
    native_receiver=None
    native_send='--native-send' in sys.argv
    native_hook='--native-hook' in sys.argv
    capture_override=sys.argv[sys.argv.index('--capture-worker-dll')+1] if '--capture-worker-dll' in sys.argv else None
    if capture_override and not native_hook:raise ValueError('Capture DLL override requires native hook')
    rust_receiver='--rust-receiver' in sys.argv
    native_capture='--native-capture' in sys.argv or native_hook
    if native_capture and not native_send:raise ValueError('Native capture requires native send')
    ready = threading.Event()
    lock = threading.Lock()
    unmarked = '--unmarked' in sys.argv[3:]
    streaming = '--stream' in sys.argv[3:]
    if native_send and not (unmarked and streaming):raise ValueError('Native send requires unmarked streaming')
    duration = int(sys.argv[sys.argv.index('--duration')+1]) if '--duration' in sys.argv else 15
    if not 5<=duration<=1200:raise ValueError('Duration must be 5..1200 seconds')
    stop_file=pathlib.Path(sys.argv[sys.argv.index('--stop-file')+1]) if '--stop-file' in sys.argv else None
    split_streams='--split-streams' in sys.argv
    pack_mode=sys.argv[sys.argv.index('--capture-pack')+1] if '--capture-pack' in sys.argv else 'js'
    if pack_mode not in ('js','native'):raise ValueError('Invalid capture pack mode')
    event_drain='--event-drain' in sys.argv
    compact_descriptor='--compact-descriptor' in sys.argv
    spec=importlib.util.spec_from_file_location('packet_descriptor',ROOT/'tools/endpoint-probe/Packet-Descriptor.py')
    descriptor_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(descriptor_module);descriptors=descriptor_module.Descriptors()
    stage_timing='--stage-timing' in sys.argv
    tail_trace='--tail-trace' in sys.argv
    tail_source=tail_trace and '--tail-host-only' not in sys.argv
    if native_capture and tail_source:raise ValueError('Native capture supports host-only tail tracing; add --tail-host-only')
    native_wake_trace='--tail-no-native-wake' not in sys.argv
    if tail_trace:stage_timing=True
    tail=None
    if tail_trace:
        spec=importlib.util.spec_from_file_location('tail_trace',ROOT/'tools/endpoint-probe/Tail-Trace.py')
        tail_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(tail_module);tail=tail_module.TailTrace()
    if (pack_mode!='js' or event_drain or stage_timing or compact_descriptor) and not unmarked:raise ValueError('Capture optimization requires unmarked mode')
    timings=None
    if stage_timing:
        spec=importlib.util.spec_from_file_location('stage_timing',ROOT/'tools/endpoint-probe/Stage-Timing.py')
        timing_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(timing_module);timings=timing_module.Timings()
    consumer_capacity=int(sys.argv[sys.argv.index('--consumer-capacity')+1]) if '--consumer-capacity' in sys.argv else 32
    if not 1<=consumer_capacity<=256:raise ValueError('Consumer capacity must be 1..256 packets')
    consumers={}
    prepared=None
    prewarm_frames=int(sys.argv[sys.argv.index('--native-prewarm-frames')+1]) if '--native-prewarm-frames' in sys.argv else None
    roar_pipelines={}
    native_enabled='--native-roar-dll' in sys.argv
    if prewarm_frames is not None and (not native_enabled or not 1<=prewarm_frames<=4096):raise ValueError('Prewarm requires native backend and 1..4096 frames')
    native_module=None
    if native_enabled:
        if not streaming or not split_streams:raise ValueError('Native ROAR requires --stream --split-streams')
        if '--roar-gain' not in sys.argv:raise ValueError('Native ROAR requires explicit --roar-gain')
        if '--roar-dll' in sys.argv or '--roar-map' in sys.argv:raise ValueError('Select one ROAR consumer backend')
        native_mode=sys.argv[sys.argv.index('--native-roar-mode')+1] if '--native-roar-mode' in sys.argv else 'individual'
        if native_mode not in ('individual','grouped'):raise ValueError('Invalid native ROAR mode')
        native_gain=sys.argv[sys.argv.index('--roar-gain')+1]
        if native_gain not in ('apply','passthrough'):raise ValueError('Invalid native gain policy')
        native_dll=sys.argv[sys.argv.index('--native-roar-dll')+1]
        spec=importlib.util.spec_from_file_location('native_roar',ROOT/'tools/endpoint-probe/Native-Roar.py')
        native_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(native_module)
    roar_enabled='--roar-map' in sys.argv or '--roar-dll' in sys.argv
    if roar_enabled and (not streaming or not split_streams):raise ValueError('ROAR requires --stream --split-streams')
    if roar_enabled and '--roar-gain' not in sys.argv:raise ValueError('ROAR requires explicit --roar-gain apply|passthrough')
    roar_module=None
    if roar_enabled:
        spec=importlib.util.spec_from_file_location('roar_adapter',ROOT/'tools/endpoint-probe/Roar-Adapter.py')
        roar_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(roar_module)
        roar_gain=sys.argv[sys.argv.index('--roar-gain')+1]
        roar_dll=sys.argv[sys.argv.index('--roar-dll')+1] if '--roar-dll' in sys.argv else None
    render_mode=sys.argv[sys.argv.index('--render-mode')+1] if '--render-mode' in sys.argv else 'none'
    if render_mode not in ('none','diagnostic'):raise ValueError('Invalid render mode')
    if native_enabled and render_mode!='none':raise ValueError('Native backend requires --render-mode none')
    if rust_receiver and (not native_hook or not native_send or not native_enabled or prewarm_frames is None or '--archive-dir' in sys.argv):
        raise ValueError('Rust receiver requires native hook/send, prewarmed native renderer and no raw archive; one stream only')
    drain_ms=float(sys.argv[sys.argv.index("--drain-ms")+1]) if "--drain-ms" in sys.argv else 5
    if drain_ms not in (.25,1,2,4,5):raise ValueError("Unsupported drain interval")
    report["configuration"]={"durationSeconds":duration,"drainMs":drain_ms,"consumerCapacity":consumer_capacity,"renderMode":render_mode}
    if native_enabled:report['configuration']['nativeRoar']={'dll':str(pathlib.Path(native_dll).resolve()),'mode':native_mode,'gain':native_gain,'packetProcessing':'Rust processing; Python transport/control'}
    waitable="--waitable-drain" in sys.argv
    if drain_ms<1 and not waitable:raise ValueError("Fractional interval requires waitable scheduler")
    report["configuration"]["waitableDrain"]=waitable
    report['configuration']['captureImplementation']='minhook' if native_hook else ('pure-c' if native_capture else 'js-hook')
    report['configuration'].update(capturePack=('pure-c' if native_capture else pack_mode),eventDrain=event_drain,stageTiming=stage_timing,compactDescriptor=compact_descriptor,tailTrace=tail_trace,tailSourceTrace=tail_source,nativeWakeTrace=tail_source and native_wake_trace)
    priority=None
    priority_name=sys.argv[sys.argv.index("--host-priority")+1] if "--host-priority" in sys.argv else "Normal"
    if priority_name not in ("Normal","AboveNormal","High"):raise ValueError("Unsupported host priority")
    consumer=None
    recorder=None
    try:
        image = pathlib.Path('C:/Windows/System32/audiodg.exe')
        if hashlib.sha256(image.read_bytes()).hexdigest() != '3dd24fe6faf0b56f99d5b00f24687b5f3b3e76e15b219e23f0963114a0dce9a5':
            raise RuntimeError('audiodg version mismatch')
        pid = int(sys.argv[2])
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        k.OpenProcess.restype = ctypes.c_void_p
        k.GetProcessInformation.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        k.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = k.OpenProcess(0x1000, False, pid)
        if not handle: raise OSError(ctypes.get_last_error(), 'OpenProcess')
        level = ctypes.c_uint32()
        try:
            k.QueryFullProcessImageNameW.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_uint32)]
            path = ctypes.create_unicode_buffer(32768)
            length = ctypes.c_uint32(len(path))
            if not k.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(length)) or pathlib.Path(path.value).resolve() != image.resolve():
                raise RuntimeError('PID image is not the verified system audiodg')
            if not k.GetProcessInformation(handle, 7, ctypes.byref(level), 4) or level.value != 0xfffffffe:
                raise RuntimeError('Protected target or failed protection query')
        finally:
            k.CloseHandle(handle)
        report['pid'] = pid
        spec=importlib.util.spec_from_file_location('host_priority',ROOT/'tools/endpoint-probe/Host-Priority.py')
        priority_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(priority_module)
        priority=priority_module.HostPriority(priority_name)
        report['hostPriority']=priority.apply()
        if streaming:
            if '--archive-dir' in sys.argv:
                spec=importlib.util.spec_from_file_location('packet_recorder',ROOT/'tools/endpoint-probe/Packet-Recorder.py')
                recorder_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(recorder_module)
                archive_seconds=float(sys.argv[sys.argv.index('--archive-seconds')+1]) if '--archive-seconds' in sys.argv else 60
                if not 5<=archive_seconds<=120:raise ValueError('Archive seconds must be 5..120')
                recorder=recorder_module.PacketRecorder(sys.argv[sys.argv.index('--archive-dir')+1],sys.argv[sys.argv.index('--archive-trigger')+1],seconds=archive_seconds)
                recorder.trace=tail
            if not unmarked:raise ValueError('Streaming requires unmarked mode')
            if not native_enabled:
                spec=importlib.util.spec_from_file_location('stream_consumer',ROOT/'tools/endpoint-probe/Stream-Consumer.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                if not split_streams:consumer=module.StreamConsumer(out,capacity=consumer_capacity,render_mode=render_mode)
        if prewarm_frames is not None:
            begin=time.perf_counter_ns()
            prepared_out=out/'stream-0';prepared_out.mkdir()
            prepared=native_module.NativePipeline(prepared_out,native_dll,capacity=consumer_capacity,mode=native_mode,gain=native_gain)
            prepared.prepare(prewarm_frames)
            report['nativePrewarm']={'frames':prewarm_frames,'durationMs':(time.perf_counter_ns()-begin)/1e6,'scope':'First stream only, before attaching; frame mismatch fails closed'}
        if not native_hook:session = frida.attach(pid)
        def message(msg, data):
            nonlocal prepared
            received_ns=time.perf_counter_ns() if timings else 0
            thread_cpu=time.thread_time_ns() if tail else 0
            with lock:
                lock_ns=time.perf_counter_ns() if tail else 0
                if msg.get('type') != 'send':
                    report.setdefault('agentErrors', []).append(msg)
                    return
                payload = msg['payload']
                if payload['kind'] == 'ready':
                    report['ready'] = payload
                    (out/'ready.txt').write_text(str(pid))
                    ready.set()
                elif payload['kind'] == 'stopped': report['timerStats'] = payload['stats']
                elif payload['kind'] == 'snapshot':
                    if unmarked:
                        try:r=descriptors.expand(payload['record'])
                        except Exception as exc:
                            report.setdefault('agentErrors',[]).append({'descriptorError':repr(exc)});return
                        expected=r['count']*32+sum(r['frames'])*4
                        if data is None or len(data)!=expected or not 1<=r['count']<=160:
                            report.setdefault('agentErrors',[]).append({'packetError':'invalid length/count'})
                            return
                        if streaming:
                            if timings:
                                timings.add('copy',round(r['copyDoneQpcNs']-r['captureQpcNs']))
                                timings.add('ownedCopyToSend',round(r['sendQpcNs']-r['copyDoneQpcNs']))
                                timings.add('sendToPython',round(received_ns-r['sendQpcNs']))
                                timings.add('captureToPython',round(received_ns-r['captureQpcNs']))
                            recorder_begin=time.perf_counter_ns() if tail else 0
                            if recorder:recorder.submit(r,data)
                            recorder_end=time.perf_counter_ns() if tail else 0
                            target=consumer
                            if split_streams:
                                block=r['block']
                                if block not in consumers:
                                    if len(consumers)>=16:raise RuntimeError('Too many distinct streams')
                                    stream_out=out/('stream-'+str(len(consumers)))
                                    if prepared is None:stream_out.mkdir()
                                    sink=None
                                    if roar_enabled:
                                        pipeline=roar_module.LivePipeline(stream_out,roar_gain,dll=roar_dll,capacity=consumer_capacity)
                                        roar_pipelines[block]=pipeline;sink=pipeline.sink.submit
                                    if native_enabled:
                                        consumers[block]=prepared if prepared is not None else native_module.NativePipeline(stream_out,native_dll,capacity=consumer_capacity,mode=native_mode,gain=native_gain)
                                        prepared=None
                                    else:
                                        consumers[block]=module.StreamConsumer(stream_out,capacity=consumer_capacity,record_states=True,render_mode=render_mode,packet_sink=sink)
                                    report.setdefault('streamDirectories',{})[block]=stream_out.name
                                target=consumers[block]
                            submit_ns=time.perf_counter_ns() if timings else 0
                            try:target.submit(r,data)
                            except Exception as exc:
                                report.setdefault('agentErrors',[]).append({'consumerSubmitError':repr(exc)})
                                return
                            report['streamPacketsReceived']=report.get('streamPacketsReceived',0)+1
                            if timings:
                                end_ns=time.perf_counter_ns()
                                timings.add('nativeSubmit',end_ns-submit_ns)
                                timings.add('pythonCallback',end_ns-received_ns)
                                timings.add('captureToSubmit',round(end_ns-r['captureQpcNs']))
                                if tail and (end_ns-r['captureQpcNs']>=5_000_000 or end_ns-received_ns>=2_000_000):
                                    tail.add('slowPacket',received_ns,end_ns,sequence=r['sequence'],block=r['block'],captureQpcNs=r['captureQpcNs'],copyDoneQpcNs=r['copyDoneQpcNs'],sendQpcNs=r['sendQpcNs'],lockAcquiredQpcNs=lock_ns,recorderBeginQpcNs=recorder_begin,recorderEndQpcNs=recorder_end,submitBeginQpcNs=submit_ns,threadCpuMs=(time.thread_time_ns()-thread_cpu)/1e6,source={k:r.get(k) for k in ['wakeQpcNs','drainEnterQpcNs','prepareQpcNs','arrayBeginQpcNs','arrayEndQpcNs','previousSendReturnQpcNs','queueDepth','drainThreadId']})
                            return
                        r['file']='packet-'+str(r['ordinal']).zfill(6)+'.bin.zlib'
                        r['encoding']='zlib';r['uncompressedBytes']=len(data)
                        (out/r['file']).write_bytes(zlib.compress(data,1))
                        report['records'].append(r)
                        return
                    if data is None or len(data) != 7808: raise ValueError('Wrong packet length')
                    r = payload['record']
                    prefix = 'block-' + r['block'][2:].upper() + '-buffer-' + str(r['bufferIndex']) + '-cycle-' + str(r['cycle'])
                    r['prefix'] = prefix
                    (out/(prefix+'-properties.bin')).write_bytes(data[:128])
                    for i in range(4): (out/(prefix+'-object-'+str(i)+'.f32')).write_bytes(data[128+i*1920:128+(i+1)*1920])
                    report['records'].append(r)
        agent = 'Sync-Hook-Unmarked.js' if unmarked else ('Sync-Hook-Ring.js' if '--ring' in sys.argv[3:] else 'Sync-Hook.js')
        if native_capture:agent='Native-Capture.js'
        report['agent'] = agent
        source='' if native_hook else (ROOT/'tools/endpoint-probe'/agent).read_text(encoding='utf-8-sig')
        if native_capture:
            capture_dll=pathlib.Path(capture_override).resolve(strict=True) if capture_override else ROOT/'build/native-capture/nsp_native_capture.dll'
            report['configuration']['nativeCaptureDllSha256']=hashlib.sha256(capture_dll.read_bytes()).hexdigest()
            source='const STREAM_NATIVE_CAPTURE_DLL='+json.dumps(str(capture_dll))+';\n'+source
        if native_send:
            spec=importlib.util.spec_from_file_location('native_send',ROOT/'tools/endpoint-probe'/('Native-Receiver.py' if rust_receiver else 'Native-Send.py'))
            transport=importlib.util.module_from_spec(spec);spec.loader.exec_module(transport)
            if rust_receiver:
                native_receiver=transport.Receiver(prepared,target_pid=pid,timing=stage_timing,mmcss='--receiver-mmcss' in sys.argv,full_timing='--full-timing' in sys.argv)
                consumers['native-receiver']=prepared;prepared=None
                report['configuration']['receiver']='rust-shared-memory-borrow'
                report['configuration']['nativeReceiverDllSha256']=hashlib.sha256(native_receiver.receiver_dll_path.read_bytes()).hexdigest()
            else:native_receiver=transport.Receiver(target_pid=pid)
            native_receiver.start(message)
            report['configuration']['nativeSendDllSha256']=hashlib.sha256(native_receiver.dll_path.read_bytes()).hexdigest()
            if not native_hook:source='const STREAM_NATIVE_SEND_CONFIG='+json.dumps(native_receiver.config)+';\n'+(ROOT/'tools/endpoint-probe/Native-Send.js').read_text()+'\n'+source
        report['configuration']['transport']='native-shared-memory' if native_send else 'frida-send'
        if pack_mode=='native' and not native_hook:source=(ROOT/'tools/endpoint-probe/Native-Pack.js').read_text()+'\n'+source
        if event_drain and not native_hook:source=(ROOT/'tools/endpoint-probe/Event-Drain.js').read_text()+'\n'+source
        source='const STREAM_NATIVE_PACK='+str(pack_mode=='native').lower()+';const STREAM_EVENT_DRAIN='+str(event_drain).lower()+';const STREAM_STAGE_TIMING='+str(stage_timing).lower()+';const STREAM_COMPACT_DESCRIPTOR='+str(compact_descriptor).lower()+';\n'+source
        source='const STREAM_TAIL_TRACE='+str(tail_source).lower()+';\n'+source
        source='const STREAM_NATIVE_WAKE_TRACE='+str(native_wake_trace).lower()+';\n'+source
        if unmarked:source='const STREAM_DURATION_MS='+str(duration*1000)+';const STREAM_DRAIN_MS='+str(drain_ms)+';\n'+source
        if waitable and not native_hook:source='const STREAM_WAITABLE=true;\n'+(ROOT/'tools/endpoint-probe/Waitable-Drain.js').read_text()+'\n'+source
        if native_hook:
            spec=importlib.util.spec_from_file_location('native_hook',ROOT/'tools/endpoint-probe/Native-Hook.py');hook_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(hook_module)
            script=hook_module.Controller(pid,native_receiver.config,duration=duration,capture_dll=capture_override)
            report['agent']='Native-Hook.c';report['configuration']['fridaImported']='frida' in sys.modules
        else:script = session.create_script(source)
        script.on('message', message)
        script.load()
        if not ready.wait(5): raise RuntimeError('Hook did not become ready')
        def cpu_snapshot():
            h=k.OpenProcess(0x1000,False,pid)
            if not h:raise OSError('CPU query OpenProcess')
            try:
                k.GetProcessTimes.argtypes=[ctypes.c_void_p]+[ctypes.POINTER(ctypes.c_uint64)]*4
                fields=[ctypes.c_uint64() for _ in range(4)]
                if not k.GetProcessTimes(h,*[ctypes.byref(v) for v in fields]):raise OSError('GetProcessTimes')
                return {'wallNs':time.perf_counter_ns(),'hostCpuSeconds':time.process_time(),'audioHostCpuSeconds':(fields[2].value+fields[3].value)/1e7}
            finally:k.CloseHandle(h)
        cpu_start=cpu_snapshot()
        deadline=time.monotonic()+(duration+1 if unmarked else 13)
        while time.monotonic()<deadline:
            if rust_receiver:report['streamPacketsReceived']=native_receiver.received
            if recorder:recorder.tick()
            if stop_file and stop_file.exists():
                report['stopReason']='external-stop-file';break
            if report.get('agentErrors') or (native_receiver and native_receiver.errors):break
            time.sleep(.1)
        cpu_end=cpu_snapshot();wall=(cpu_end['wallNs']-cpu_start['wallNs'])/1e9
        report['cpu']={'wallSeconds':wall,'scope':'Total process CPU including Sonic/other streams; not incremental overhead; 100 percent equals one logical core'}
        for field in ('hostCpuSeconds','audioHostCpuSeconds'):
            report['cpu'][field]=cpu_end[field]-cpu_start[field]
            report['cpu'][field+'OneCorePercent']=(cpu_end[field]-cpu_start[field])/wall*100
    except Exception as exc:
        report['error'] = repr(exc)
    finally:
        if script:
            try: report['finalStats'] = script.exports_sync.stop()
            except Exception as exc: report['stopError'] = repr(exc)
            if streaming and unmarked and report.get('finalStats'):
                pending_deadline=time.monotonic()+5
                expected=report['finalStats'].get('copies',0)
                while report.get('streamPacketsReceived',0)<expected and not report.get('agentErrors') and time.monotonic()<pending_deadline:
                    if rust_receiver:
                        report['streamPacketsReceived']=native_receiver.received
                        if native_receiver.errors:break
                    time.sleep(.005)
                if report.get('streamPacketsReceived',0)!=expected:report['tailDrainError']='Capture/host packet count mismatch'
            try:
                script.unload()
                report['nativeControlStopped' if native_hook else 'scriptUnloaded'] = True
            except Exception as exc: report['unloadError'] = repr(exc)
        if session:
            try: session.detach(); report['sessionDetached'] = True
            except Exception as exc: report['detachError'] = repr(exc)
        if prepared is not None:
            try:report['unusedNativePrewarm']=prepared.finish()
            except Exception as exc:report['prewarmFinishError']=repr(exc)
        if native_receiver is not None:
            try:report['nativeTransport']=native_receiver.finish()
            except Exception as exc:report['nativeTransportError']=repr(exc)
        if rust_receiver and native_receiver is not None:
            transport_report=report.get('nativeTransport',{})
            if transport_report.get('threadStopped'):
                report['streamPacketsReceived']=transport_report['received']
                report['stageTiming']=transport_report['timing']
                report['nativeReceiverTrace']={'events':transport_report['tails'],'overflow':transport_report['tailOverflow']}
                block=transport_report.get('block')
                if block and 'native-receiver' in consumers:
                    consumers[block]=consumers.pop('native-receiver');report['streamDirectories']={block:'stream-0'}
            else:
                # Do not free a renderer while the receiver can still call it.
                _retained_native_resources.append((native_receiver,list(consumers.values())))
                consumers.clear()
        if recorder:report['archive']=recorder.finish()
        if consumer:
            try:report['streamSummary']=consumer.finish()
            except Exception as exc:report['consumerFinishError']=repr(exc)
        if split_streams:
            summaries={}
            for block,target in consumers.items():
                try:summaries[block]=target.finish()
                except Exception as exc:report.setdefault('consumerFinishErrors',[]).append(repr(exc))
            report['streamSummaries']=summaries
            report['streamSummary']={'passed':bool(summaries) and all(s['passed'] for s in summaries.values()) and not report.get('consumerFinishErrors')}
            if native_enabled:report['nativeSummaries']=summaries
        if roar_enabled:
            report['roarSummaries']={}
            for block,pipeline in roar_pipelines.items():
                try:report['roarSummaries'][block]=pipeline.finish()
                except Exception as exc:report.setdefault('roarFinishErrors',[]).append(repr(exc))
        if priority:
            try:report['hostPriorityRestore']=priority.restore()
            except Exception as exc:report['priorityRestoreError']=repr(exc)
        if timings and not rust_receiver:report['stageTiming']=timings.report()
        if tail:report['tailTrace']=tail.finish()
        with lock: (out/'observer.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    stats=report.get('finalStats', {})
    failed=('error' in report or not stats.get('hookBytesRestored') or not (report.get('scriptUnloaded') or report.get('nativeControlStopped'))
            or not (report.get('sessionDetached') or report.get('nativeControlStopped')) or report.get('agentErrors') or stats.get('errors') or report.get('tailDrainError') or report.get('prewarmFinishError'))
    if unmarked:
        failed=failed or not stats.get('copies') or stats.get('queueDropped') or stats.get('budgetRejected') or stats.get('queueRemaining')
    if native_send:failed=failed or report.get('nativeTransportError') or not report.get('nativeTransport',{}).get('passed') or not stats.get('nativeSendStats',{}).get('libraryReleased') or stats.get('nativeSendStats',{}).get('rejected')
    if priority:failed=failed or not report.get('hostPriorityRestore',{}).get('restored')
    if streaming:failed=failed or not report.get('streamSummary',{}).get('passed')
    if roar_enabled:failed=failed or report.get('roarFinishErrors') or not report.get('roarSummaries') or not all(s['passed'] for s in report['roarSummaries'].values())
    if recorder:failed=failed or not report.get('archive',{}).get('passed') or not report.get('archive',{}).get('threadStopped')
    return int(bool(failed))

if __name__ == '__main__': sys.exit(main())
