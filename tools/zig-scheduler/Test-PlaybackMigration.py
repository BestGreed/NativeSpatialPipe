"""Isolated user-path migration; no backend, audio device or real user data."""
import argparse,json,socket,subprocess,tempfile,time
from pathlib import Path
from urllib.request import urlopen

p=argparse.ArgumentParser();p.add_argument('--host',required=True,type=Path);p.add_argument('--report',required=True,type=Path);args=p.parse_args()
root=Path(__file__).resolve().parents[2];checks=[]
for case in ('empty','legacy-unset','legacy-pointer','existing-user','custom','invalid-legacy'):
 with tempfile.TemporaryDirectory(dir=root/'build',prefix='migration-') as temp:
  base=Path(temp);home=base/'user';ini=base/'fsa.ini';old=base/'playback.json';target=home/'playback.json';custom=base/'custom.json'
  data=json.dumps(dict(bridgeDll='C:/fixture/bridge.dll',asioDll='C:/fixture/asio.dll',driver='asio:fixture',master=.75))
  if case not in ('empty','custom'):old.write_text('{malformed' if case=='invalid-legacy' else data,encoding='utf-8')
  pointer=str(old) if case=='legacy-pointer' else str(custom) if case=='custom' else ''
  ini.write_text('[NSP]\n'+('PlaybackConfig='+pointer+'\n' if pointer else ''),encoding='utf-8');original_ini=ini.read_bytes()
  if case=='custom':custom.write_text(data,encoding='utf-8')
  if case=='existing-user':home.mkdir();target.write_text(data.replace('.75','.25').replace('0.75','0.25'),encoding='utf-8')
  original_target=target.read_bytes() if target.exists() else None
  with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
  command=[str(args.host.resolve()),'web-host','--root',str(root),'--home',str(home),'--fsa-config',str(ini),'--port',str(port)]
  # Run twice to check idempotence and that an existing target is never replaced.
  for attempt in range(2):
   proc=subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
   try:
    for _ in range(100):
     try:
      with urlopen(f'http://127.0.0.1:{port}/api/web/config/playback',timeout=1) as response:value=json.load(response)
      break
     except OSError:
      if proc.poll() is not None:raise AssertionError(proc.stderr.read())
      time.sleep(.04)
    else:raise AssertionError('Host startup timeout')
    assert Path(value['path'])==(custom if case=='custom' else target),(case,value)
    assert ini.read_bytes()==original_ini,'Migration must not modify installation INI'
    if case=='empty':assert not home.exists() and not value['exists']
    elif case=='custom':assert not target.exists() and custom.read_text()==data
    elif case=='existing-user':assert target.read_bytes()==original_target and old.read_text()==data
    else:
     assert target.read_bytes()==old.read_bytes()
     if case=='invalid-legacy':assert value['validationError']=='InvalidJson'
    assert not (home/'runtime.json').exists()
   finally:proc.terminate();proc.wait(timeout=5);proc.stderr.close()
  checks.append(case)
result=dict(passed=True,checks=checks,idempotent=True,audioDevicesOpened=False)
args.report.write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
