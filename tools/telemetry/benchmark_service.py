"""Paced whole-service A/B on random ports, isolated from the user's frontend."""
import argparse
import base64
import ctypes as C
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time

ROOT=Path(__file__).resolve().parents[2]


def prepare_reference():
    directory=ROOT/'build/telemetry-meter/reference'
    directory.mkdir(parents=True,exist_ok=True)
    revision=subprocess.run(['git','rev-parse','HEAD'],capture_output=True,text=True,check=True).stdout.strip()
    for name in ('engine.py','soundscape.py','service.py','client.py','transport.py'):
        source=subprocess.run(['git','show',revision+':tools/telemetry/'+name],capture_output=True,check=True).stdout.decode()
        if name=='engine.py':
            source=source.replace("Path(__file__).resolve().parents[1] / 'visualization-contract/contract.py'",repr(str(directory/'contract.py')))
        (directory/name).write_text(source,encoding='utf-8')
    (directory/'contract.py').write_bytes(subprocess.run(['git','show',revision+':tools/visualization-contract/contract.py'],capture_output=True,check=True).stdout)
    (directory/'revision.txt').write_text(revision)
    return directory


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare-reference',action='store_true')
    parser.add_argument('--reference-dir',type=Path)
    parser.add_argument('--seconds',type=int,default=8)
    parser.add_argument('--out',type=Path)
    args=parser.parse_args()
    if args.prepare_reference:
        print(prepare_reference());return
    if not 2<=args.seconds<=30:raise ValueError('Duration must be 2..30 seconds')
    if args.reference_dir:sys.path.insert(0,str(args.reference_dir.resolve()))
    from client import TelemetryService
    from transport import fragments
    kernel=C.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[C.c_uint32,C.c_int,C.c_uint32];kernel.OpenProcess.restype=C.c_void_p
    kernel.GetProcessTimes.argtypes=[C.c_void_p]+[C.POINTER(C.c_uint64)]*4
    kernel.CloseHandle.argtypes=[C.c_void_p]
    def cpu(pid):
        process=kernel.OpenProcess(0x1000,False,pid)
        if not process:raise C.WinError(C.get_last_error())
        try:
            fields=[C.c_uint64() for _ in range(4)]
            if not kernel.GetProcessTimes(process,*[C.byref(value) for value in fields]):raise C.WinError(C.get_last_error())
            return (fields[2].value+fields[3].value)/1e7
        finally:kernel.CloseHandle(process)
    header=struct.Struct('<4sHHIIQQ');row=struct.Struct('<IIIIQ3fIfI')
    output=header.pack(b'NSPT',1,12,480,48000,1,0)+struct.pack('<5760f',*([.5]*5760))
    objects=header.pack(b'NSPO',1,140,480,48000,1,0)+b''.join(row.pack(i,i+17,1,4,1,2.+i,3.,-4.,1,.5,1)+struct.pack('<480f',*([.25]*480)) for i in range(140))
    with TelemetryService(http_port=0) as service:
        port=int(service.info['url'].rsplit(':',1)[1])
        sock=socket.create_connection(('127.0.0.1',port),timeout=3)
        messages=[];errors=[];finished=threading.Event()
        try:
            key=base64.b64encode(os.urandom(16)).decode()
            sock.sendall((f'GET /v1/telemetry HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nOrigin: http://127.0.0.1:8765\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Key: {key}\r\n\r\n').encode())
            response=b''
            while not response.endswith(b'\r\n\r\n'):response+=sock.recv(1)
            if b' 101 ' not in response:raise RuntimeError(response)
            body=json.dumps(dict(schemaVersion='0.2.0',messageType='subscribe',routeId='native-route',mode='both',publishRateHz=10,bands=['all'])).encode()
            mask=b'abcd';sock.sendall(b'\x81\xfe'+struct.pack('!H',len(body))+mask+bytes(v^mask[i%4] for i,v in enumerate(body)))
            def exact(n):
                result=b''
                while len(result)<n:
                    chunk=sock.recv(n-len(result))
                    if not chunk:raise OSError('WebSocket closed')
                    result+=chunk
                return result
            def read_messages():
                try:
                    while not finished.is_set():
                        opcode,size=exact(2)
                        if size==126:size=struct.unpack('!H',exact(2))[0]
                        elif size==127:size=struct.unpack('!Q',exact(8))[0]
                        message=json.loads(exact(size))
                        if message['messageType']=='snapshot':messages.append((len(message['taps']['decoded-input']['objects']),message['taps']['render-ready']['quality']))
                except OSError as exc:
                    if not finished.is_set():errors.append(str(exc))
            reader=threading.Thread(target=read_messages,daemon=True);reader.start()
            before=cpu(service.info['pid']);start=time.perf_counter()
            blocks=args.seconds*100
            for sequence in range(blocks):
                due=start+sequence*.01
                if due>time.perf_counter():time.sleep(due-time.perf_counter())
                number=struct.pack('<Q',sequence)
                service.socket.sendto(service.secret+output[:24]+number+output[32:],service.info['udp'])
                raw=objects[:24]+number+objects[32:]
                for part in fragments(raw,sequence):service.socket.sendto(service.secret+part,service.info['udp'])
                if sequence%10==0:
                    snapshot=service.query('snapshot')
                    if snapshot is not None:json.dumps(snapshot,separators=(',',':'))
            time.sleep(.2)
            elapsed=time.perf_counter()-start;seconds=cpu(service.info['pid'])-before
            status=service.query('status');snapshot=service.query('snapshot')
            if status['receivedBlocks']!=blocks or status['objectBlocks']!=blocks:raise RuntimeError(status)
            if status['invalidBlocks'] or status['invalidObjectBlocks'] or status['unobservedBlocks']:raise RuntimeError(status)
            if len(snapshot['taps']['decoded-input']['objects'])!=140 or len(messages)<args.seconds*5 or errors:raise RuntimeError((len(messages),errors))
            result=dict(variant='original-HEAD' if args.reference_dir else 'optimized',servicePid=service.info['pid'],objects=140,blocks=blocks,
                        elapsedSeconds=elapsed,cpuSeconds=seconds,oneCorePercent=seconds/elapsed*100,machinePercent=seconds/elapsed*100/os.cpu_count(),
                        logicalProcessors=os.cpu_count(),websocketSnapshots=len(messages),status=status)
            if args.out:args.out.write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps(result,indent=2))
        finally:
            finished.set();sock.close()
            if 'reader' in locals():reader.join(4)


if __name__=='__main__':main()
