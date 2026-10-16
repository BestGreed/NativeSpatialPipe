"""Current single-stream C/Rust capture entry; Python is control-plane only."""
import argparse
import os
import pathlib
import runpy
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('output', help='New output directory on a drive with sufficient free space')
p.add_argument('pid', type=int, help='Version-verified audiodg process')
p.add_argument('--duration', type=int, default=90)
p.add_argument('--stop-file')
p.add_argument('--normal-scheduling', action='store_true', help='Comparison fallback: disable capture, receiver and renderer MMCSS')
p.add_argument('--full-timing', action='store_true', help='Use diagnostic DLLs to measure through rendering and buffered file output')
a = p.parse_args()
mode = 'normal' if a.normal_scheduling else 'mmcss'
capture = ROOT / f'build/capture-scheduler-{mode}/nsp_native_capture.dll'
renderer = ROOT / ('build/roar-full-timing' if a.full_timing else 'build/roar-native') / 'release/nsp_roar_bridge.dll'
for path in (capture, renderer):
    if not path.is_file():
        p.error(f'Missing native build: {path}')
sys.argv = [str(ROOT / 'tools/endpoint-probe/Sync-Hook.py'), a.output, str(a.pid),
            '--native-hook', '--native-send', '--native-capture', '--rust-receiver',
            '--unmarked', '--stream', '--split-streams', '--capture-pack', 'native',
            '--event-drain', '--compact-descriptor', '--native-prewarm-frames', '480',
            '--drain-ms', '1', '--waitable-drain',
            '--consumer-capacity', '64', '--native-roar-dll', str(renderer),
            '--native-roar-mode', 'individual', '--roar-gain', 'passthrough',
            '--capture-worker-dll', str(capture), '--duration', str(a.duration),
            '--stage-timing', '--tail-trace', '--tail-host-only']
if not a.normal_scheduling:
    sys.argv.append('--receiver-mmcss')
if a.full_timing:
    sys.argv.append('--full-timing')
if a.stop_file:
    sys.argv += ['--stop-file', a.stop_file]
# Set explicitly in both modes: an inherited environment must not silently
# downgrade the default or enable renderer MMCSS in the normal comparison.
previous_render_mmcss = os.environ.get('NSP_RENDER_MMCSS')
os.environ['NSP_RENDER_MMCSS'] = '0' if a.normal_scheduling else '1'
try:
    runpy.run_path(sys.argv[0], run_name='__main__')
finally:
    if previous_render_mmcss is None:
        os.environ.pop('NSP_RENDER_MMCSS', None)
    else:
        os.environ['NSP_RENDER_MMCSS'] = previous_render_mmcss
