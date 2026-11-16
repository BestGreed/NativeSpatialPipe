"""Read-only live/replay coverage observer. Never opens audio, HID or playback.

Coverage is a geometric/recording progress measure, not a localisation accuracy
or acoustic SNR certificate. Host-arrival pose association is a live preview.
"""
import argparse
from collections import Counter, deque
import json
import math
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .calibration import B_SERIAL, checked_pose
from .core import rotate
from .field_viewer import render_geometry
from .rig_session import EventTail, atomic_json
from .trajectory_coverage import TrajectoryCoverage

CENTRES = (125, 250, 500, 1000, 2000, 4000, 8000, 16000)
STAGES = (0, *CENTRES)  # 0 is one shared broadband trajectory, not eight acoustic certificates.
ROUTES = ('L', 'R', 'LR')
DEFAULT_PLAN = dict(schema=1, layer_edges_m=[.35, .55, .75, .95], target_radii_m=[.45, .65, .85],
                    layer_names=['near', 'middle', 'far'], azimuth_bins=18,
                    elevation_bins=9, recording_target_seconds=.15,
                    angular_coverage_target=.70, pose_max_age_seconds=.05,
                    angular_region_confirmed=False,
                    trajectory_cell_m=.03,
                    head_centre_palm_m=None,
                    head_half_width_m=.20, head_half_height_m=.10, head_half_depth_m=.20,
                    scan_window=None,
                    switch_guard_seconds=.15,
                    note='Proposed 45/65/85cm use-distance layers, not yet user-confirmed; progress targets are not acoustic acceptance thresholds.')


def validate_plan(plan):
    p = dict(DEFAULT_PLAN, **plan)
    edges = p['layer_edges_m']
    if len(edges) != 4 or any(not math.isfinite(x) or x < 0 for x in edges) or any(a >= b for a, b in zip(edges, edges[1:])):
        raise ValueError('Three increasing finite radial intervals required')
    if p['layer_names'] != ['near', 'middle', 'far']:
        raise ValueError('Layer names must be near/middle/far')
    radii = p['target_radii_m']
    if len(radii) != 3 or any(not math.isfinite(r) or not edges[i] <= r < edges[i+1] for i, r in enumerate(radii)):
        raise ValueError('Each target radius must lie inside its layer')
    if any(type(p[k]) is not int or not 1 <= p[k] <= 180 for k in ('azimuth_bins', 'elevation_bins')):
        raise ValueError('Angular grid size out of range')
    for k in ('recording_target_seconds', 'pose_max_age_seconds', 'switch_guard_seconds'):
        if not math.isfinite(p[k]) or p[k] <= 0:
            raise ValueError('Positive finite progress/timing settings required')
    centre = p['head_centre_palm_m']
    if centre is not None and (len(centre) != 3 or any(not math.isfinite(v) for v in centre)):
        raise ValueError('Head centre must be finite palm-frame x/y/z metres')
    for k in ('head_half_width_m','head_half_height_m','head_half_depth_m'):
        if not math.isfinite(p[k]) or not 0 < p[k] <= .5:
            raise ValueError('Head-region half extents must be in (0,.5] metres')
    if not 0 < p['angular_coverage_target'] <= 1:
        raise ValueError('Coverage target must be in (0,1]')
    return p


class Coverage:
    def __init__(self, calibration, plan=None):
        self.plan = validate_plan(plan or {})
        self.calibration = calibration
        self.geometry = render_geometry({'provenance': {'world_id': calibration['world_id']}}, calibration)
        self.world = calibration['world_id']
        self.cells = {}
        centre = self.plan['head_centre_palm_m']
        region = None
        if centre is not None:
            x,y,z = centre
            # Input height uses measured palm plane; trajectory origin is screen
            # centre. The screen tilt must not become a head-height reference.
            y -= self.geometry['screen_center_height_m']
            w,h,d = self.plan['head_half_width_m'],self.plan['head_half_height_m'],self.plan['head_half_depth_m']
            if z+d <= 0:
                raise ValueError('Head region must intersect the front of the notebook')
            region = [[x-w,x+w],[y-h,y+h],[max(0.,z-d),z+d]]
        from .scan_window import ScanWindow
        self.window = ScanWindow(self.geometry,self.plan['scan_window']) if self.plan['scan_window'] is not None else None
        self.trajectory = TrajectoryCoverage(self.plan['trajectory_cell_m'], self.plan['layer_edges_m'], region,self.window)
        self.previous_trajectory_pose = None
        self.recent_segments = deque()
        self.active = None
        self.last_stage = None
        self.stage_start = None
        self.pose = None
        self.last_position = None
        self.latest_pose_valid = False
        self.last_audio_end = {}
        self.diagnostics = Counter()

    def set_stage(self, hz, route, timestamp, basis):
        if hz not in STAGES or route not in ROUTES or not math.isfinite(timestamp):
            raise ValueError('Unknown frequency/route or invalid stage timestamp')
        self.active = (hz, route)
        self.last_stage = self.active
        self.stage_start = timestamp
        self.stage_basis = basis
        self.previous_trajectory_pose = None
        self.recent_segments.clear()

    def position(self, pose):
        p = checked_pose(pose)
        # Only the mechanical centre offset affects position; microphone heading
        # is not needed to assign radial/angular coverage.
        offset = self.calibration['probe']['known_offset_tracker_m']
        delta = rotate(p['quaternion_wxyz'], offset)
        world = [a + b for a, b in zip(p['position_m'], delta)]
        origin = self.geometry['screen_world_pose']['position_m']
        return [sum(a * (b - c) for a, b, c in zip(axis, world, origin))
                for axis in self.geometry['basis_world_columns']]

    def locate(self, xyz):
        x, y, z = xyz
        # Front/upper quarter-space, relative to the measured palm plane.
        if not all(math.isfinite(v) for v in xyz) or self.trajectory.layer(xyz) is None:
            return None
        radius = math.sqrt(x*x + y*y + z*z)
        edges = self.plan['layer_edges_m']
        if not edges[0] <= radius < edges[-1]:
            return None
        layer = next(i for i in range(3) if edges[i] <= radius < edges[i+1])
        az = math.degrees(math.atan2(x, z))
        el = math.degrees(math.atan2(y, math.hypot(x, z)))
        a = min(self.plan['azimuth_bins']-1, int((az+90)/180*self.plan['azimuth_bins']))
        e = max(0,min(self.plan['elevation_bins']-1, int(el/90*self.plan['elevation_bins'])))
        return layer, a, e, radius, az, el

    def _cell(self, location):
        key = (*self.active, *location[:3])
        return self.cells.setdefault(key, dict(seen=False, seconds=0., blocks=0))

    def feed_pose(self, event):
        if event.get('serial') != B_SERIAL:
            return
        self.latest_pose_valid = False
        if event.get('source_world_id', self.world) != self.world:
            self.pose = None
            self.previous_trajectory_pose = None
            self.recent_segments.clear()
            self.diagnostics['world_mismatch'] += 1
            return
        if not event.get('valid'):
            self.pose = None
            self.previous_trajectory_pose = None
            self.recent_segments.clear()
            self.diagnostics['invalid_pose'] += 1
            return
        try:
            xyz = self.position(event)
        except (ValueError, KeyError, TypeError):
            self.pose = None
            self.previous_trajectory_pose = None
            self.recent_segments.clear()
            self.diagnostics['malformed_pose'] += 1
            return
        self.pose = event
        self.latest_pose_valid = True
        self.last_position = xyz
        timestamp = event.get('arrival_lsl')
        if not isinstance(timestamp, (int,float)) or not math.isfinite(timestamp):
            self.pose = None
            self.previous_trajectory_pose = None
            self.latest_pose_valid = False
            return
        if self.active and timestamp-self.stage_start >= self.plan['switch_guard_seconds']:
            previous = self.previous_trajectory_pose
            epoch = event.get('epoch', 0)
            if previous and previous[3] == epoch and 0 < timestamp-previous[0] <= self.plan['pose_max_age_seconds']:
                t0, a, _, _ = previous
                self.trajectory.add(self.active, a, xyz)
                self.recent_segments.append((t0,timestamp,a,xyz,self.active))
            else:
                self.trajectory.add(self.active, xyz, xyz)
                if previous:
                    self.diagnostics['trajectory_gap_not_connected'] += 1
                self.recent_segments.clear()
            self.previous_trajectory_pose = (timestamp,xyz,self.active,epoch)
            while self.recent_segments and self.recent_segments[0][1] < timestamp-1:
                self.recent_segments.popleft()
        else:
            self.previous_trajectory_pose = None
            self.recent_segments.clear()
        location = self.locate(xyz)
        if self.active and location:
            self._cell(location)['seen'] = True

    def feed_audio(self, event, clipped=False):
        if not self.active:
            return
        timestamp = event.get('arrival_lsl')
        if not isinstance(timestamp, (float, int)) or not math.isfinite(timestamp):
            self.diagnostics['bad_audio_time'] += 1
            return
        if timestamp - self.stage_start < self.plan['switch_guard_seconds']:
            self.diagnostics['switch_guard'] += 1
            return
        epoch = event.get('epoch', 0)
        start = event.get('sample_start_i64')
        frames = event.get('frames', 0)
        rate = event.get('rate', 0)
        if type(start) is not int or type(frames) is not int or frames <= 0 or rate != 48000 or event.get('channels') != 4:
            self.diagnostics['bad_audio_format'] += 1
            return
        if start < self.last_audio_end.get(epoch, -1):
            self.diagnostics['duplicate_audio'] += 1
            return
        self.last_audio_end[epoch] = start + frames
        if clipped or any(event.get(k) for k in ('discontinuity', 'timestamp_error', 'device_gap_frames', 'silent')):
            self.diagnostics['audio_quality_rejected'] += 1
            return
        if not self.pose or not 0 <= timestamp - self.pose['arrival_lsl'] <= self.plan['pose_max_age_seconds']:
            self.diagnostics['no_fresh_pose'] += 1
            return
        location = self.locate(self.last_position)
        if not location:
            self.diagnostics['outside_region'] += 1
        else:
            cell = self._cell(location)
            cell['seen'] = True
            cell['seconds'] += frames / rate
            cell['blocks'] += 1
        # Match the audio block's host-arrival interval against the full-rate
        # pose polyline, not against one angle bin or one representative pose.
        begin = max(timestamp-frames/rate, self.stage_start+self.plan['switch_guard_seconds'])
        for t0,t1,a,b,group in self.recent_segments:
            lo,hi = max(begin,t0),min(timestamp,t1)
            if hi <= lo or group != self.active:
                continue
            u,v = (lo-t0)/(t1-t0),(hi-t0)/(t1-t0)
            start_point = [x+u*(y-x) for x,y in zip(a,b)]
            end_point = [x+v*(y-x) for x,y in zip(a,b)]
            self.trajectory.add(group,start_point,end_point,hi-lo,recorded=True)

    def snapshot(self):
        p = self.plan
        weights = [math.sin(math.radians((e+1)*90/p['elevation_bins'])) -
                   math.sin(math.radians(e*90/p['elevation_bins'])) for e in range(p['elevation_bins'])]
        groups = []
        for hz in STAGES:
            for route in ROUTES:
                for layer in range(3):
                    cells = []
                    for e in range(p['elevation_bins']):
                        for a in range(p['azimuth_bins']):
                            cell = self.cells.get((hz, route, layer, a, e), {})
                            seconds = cell.get('seconds', 0.)
                            cells.append(dict(a=a, e=e, seen=cell.get('seen', False), seconds=seconds,
                                              target_met=seconds+1e-12 >= p['recording_target_seconds']))
                    coverage = sum(weights[c['e']] for c in cells if c['target_met']) / p['azimuth_bins']
                    groups.append(dict(hz=hz, route=route, layer=layer,
                                       seen_cells=sum(c['seen'] for c in cells),
                                       recorded_cells=sum(c['seconds'] > 0 for c in cells),
                                       target_met_cells=sum(c['target_met'] for c in cells),
                                       angular_recording_coverage=coverage,
                                       recording_seconds=sum(c['seconds'] for c in cells),
                                       progress_target_met=p['angular_region_confirmed'] and coverage >= p['angular_coverage_target'],
                                       acoustic_snr_status='not_assessed', cells=cells))
        current=None
        if self.last_position is not None:
            distance=math.sqrt(sum(v*v for v in self.last_position))
            layer=next((i for i in range(3) if p['layer_edges_m'][i]<=distance<p['layer_edges_m'][i+1]),None)
            current=dict(distance_m=distance,layer=layer)
            if self.window is not None and layer is not None:
                current.update(self.window.evaluate(self.last_position,layer))
            else:current.update(inside=False,reason='outside_distance_layers')
        # The old angular cells remain internal diagnostics, not a live payload
        # or completion criterion. Send small summaries at UI refresh frequency.
        for g in groups:g.pop('cells',None)
        return dict(schema=1, kind='live_coverage_preview', plan=p, groups=groups,
                    scan_window=self.window.snapshot() if self.window else None,current_location=current,
                    primary_coverage_basis='metric_3d_trajectory', trajectory=self.trajectory.snapshot(),
                    angular_view_role='navigation_projection_only_not_spatial_completeness',
                    active=list(self.active) if self.active else None,
                    last_stage=list(self.last_stage) if self.last_stage else None,
                    position_palm_m=self.last_position, latest_pose_valid=self.latest_pose_valid,
                    geometry=self.geometry, diagnostics=dict(self.diagnostics),
                    acoustic_validity_verified=False, synchronized=False,
                    position_association='host_arrival_preview_not_dynamic_delay_calibrated',
                    region='front_upper_quarter_space_about_measured_screen_centre',
                    distance_origin='H3-VR acoustic centre to measured screen centre, Euclidean distance',
                    time_basis='recorded_audio_frames_with_fresh_host_arrival_pose',
                    note='Recording coverage targets are configurable workflow aids. Band SNR/direction validity is not certified.')


class Observer:
    def __init__(self, capture, calibration, plan=None):
        self.folder = Path(capture)
        session = json.loads((self.folder/'session.json').read_text(encoding='utf-8'))
        self.config = session['config']
        if self.config['world_id'] != calibration['world_id']:
            raise ValueError('Capture/calibration worlds differ')
        self.coverage = Coverage(calibration, plan)
        self.tail = EventTail(self.folder/'events.jsonl')
        self.pending = []
        self.reference = self.config.get('playback', {}).get('reference', {})
        self.last_read_unix = 0.
        self.read_events = 0
        self.lock = threading.Lock()
        self.error = None
        self.start_requested = False
        self.playback_seen = False
        self.latest_lsl = None
        self.playback_start_lsl = None
        self.playback_end_lsl = None

    def poll(self):
        with self.lock:
            self.pending.extend(self.tail.read())
            consumed = 0
            with (self.folder/'audio.pcm').open('rb') as pcm:
                for event in self.pending:
                    kind = event.get('kind')
                    if isinstance(event.get('arrival_lsl'),(int,float)):
                        self.latest_lsl=max(self.latest_lsl or event['arrival_lsl'],event['arrival_lsl'])
                    c = self.coverage
                    if kind == 'pose':
                        c.feed_pose(event)
                    elif kind == 'coverage_stage':
                        if event.get('source_world_id') != c.world:
                            raise ValueError('Stage world mismatch')
                        c.set_stage(event['center_hz'], event['route'], event['arrival_lsl'], 'recorded_scheduler_stage')
                    elif kind == 'playback_started':
                        self.playback_seen = True
                        self.playback_start_lsl=event['arrival_lsl']
                        hz = 0 if self.reference.get('kind')=='music_noise_bandlimited' else self.reference.get('center_hz')
                        gains = self.reference.get('channel_gains')
                        route = 'LR' if self.reference.get('all_channels_identical') else ('L' if gains == [1, 0] else 'R' if gains == [0, 1] else None)
                        if hz in STAGES and route:
                            c.set_stage(hz, route, event['arrival_lsl'], 'recorded_playback_and_reference_metadata')
                        else:
                            c.active = None
                            c.diagnostics['unknown_playback_context'] += 1
                    elif kind in ('playback_finished', 'coverage_pause'):
                        if kind=='playback_finished':self.playback_end_lsl=event['arrival_lsl']
                        c.active = None
                    elif kind == 'audio_block' and c.active:
                        bits = event.get('container_bits')
                        if bits not in (24, 32):
                            raise ValueError('Unsupported PCM container')
                        size = event['frames'] * 4 * (bits//8)
                        pcm.seek(event['pcm_byte_offset'])
                        raw = pcm.read(size)
                        if len(raw) != size:
                            break  # Events can become visible before buffered PCM.
                        from .session import unpack24
                        import numpy as np
                        samples = unpack24(raw, bits)
                        c.feed_audio(event, clipped=bool(np.any((samples <= -8388608) | (samples >= 8388607))))
                    consumed += 1
            del self.pending[:consumed]
            if consumed:
                self.last_read_unix = time.time()
                self.read_events += consumed

    def snapshot(self):
        with self.lock:
            result = self.coverage.snapshot()
            closed = (self.folder/'result.json').exists()
            result_data={}
            if closed:
                try:result_data=json.loads((self.folder/'result.json').read_text(encoding='utf-8'))
                except (OSError,json.JSONDecodeError):pass
            source_error=None
            if result_data.get('status')=='incomplete':
                if result_data.get('dropped_queue_items',0):source_error='录制队列溢出，本轮已停止；已保存的数据保留。'
                else:source_error='本轮异常结束：'+str(result_data.get('capture_failures') or result_data.get('failed') or '请检查录制日志')
            try:
                live=json.loads((self.folder/'live-status.json').read_text(encoding='utf-8'))
            except (FileNotFoundError,json.JSONDecodeError):live={}
            ready=live.get('phase')=='ready' and not live.get('failures')
            pose_age=(self.latest_lsl-self.coverage.pose['arrival_lsl']+max(0,time.time()-self.last_read_unix)) if self.coverage.pose and self.latest_lsl else None
            fresh_pose=bool(self.coverage.latest_pose_valid and pose_age is not None and 0<=pose_age<.25)
            finishing=(self.folder/'FINISH.json').exists()
            elapsed=max(0.,(self.playback_end_lsl or self.latest_lsl or self.playback_start_lsl or 0)-(self.playback_start_lsl or self.latest_lsl or 0)) if self.playback_start_lsl else 0.
            result['latest_pose_valid']=fresh_pose
            result.update(source_capture=str(self.folder.resolve()), source_closed=closed,
                          source_error=source_error,
                          latest_pose_age_seconds=pose_age,
                          live_source_fresh=not closed and time.time()-self.last_read_unix < 2,
                          read_events=self.read_events, pending_events=len(self.pending), observer_error=self.error,
                          playback_context_basis=getattr(self.coverage, 'stage_basis', None),
                          trial_seconds=self.reference.get('seconds'),playback_seen=self.playback_seen,
                          reference_band_hz=self.reference.get('band_hz'),
                          reference_kind=self.reference.get('kind'),analysis_centres_hz=self.reference.get('analysis_centres_hz'),
                          reference_route=('LR' if self.reference.get('all_channels_identical') else 'L' if self.reference.get('channel_gains')==[1,0] else 'R' if self.reference.get('channel_gains')==[0,1] else None),
                          final_snapshot_ready=closed and not self.pending and not self.tail.partial and self.tail.offset>=(self.folder/'events.jsonl').stat().st_size,
                          playback_elapsed_seconds=elapsed,playback_remaining_seconds=max(0.,self.reference.get('seconds',0)-elapsed),
                          operator_finishing=finishing,
                          start_requested=self.start_requested,
                          can_start=bool(ready and not closed and not finishing and not self.start_requested and not self.playback_seen
                              and fresh_pose and self.config.get('playback',{}).get('trigger_operator_marker')
                              and time.time()-self.last_read_unix<2),
                          can_stop=bool(ready and not closed and not finishing),capture_phase=live.get('phase'))
            return result

    def request_start(self):
        if not self.snapshot()['can_start']:
            raise ValueError('Capture is not ready for a new trial')
        with self.lock:
            if self.start_requested:raise ValueError('Trial already requested')
            from .rig_session import mark
            playback=self.config['playback']
            result=mark(self.config['rig_source'],playback['trigger_operator_marker'])
            self.start_requested=True
            return result

    def request_stop(self):
        if not self.snapshot()['can_stop']:
            raise ValueError('No active capture to stop')
        atomic_json(self.folder/'FINISH.json',dict(kind='operator_finish',request_unix=time.time()))
        return dict(status='operator_finish_requested_data_will_be_preserved')


def serve(observer, port, closed_idle_seconds=60):
    stop = threading.Event()
    def follow():
        while not stop.is_set():
            try:
                observer.poll()
            except Exception as exc:
                with observer.lock:
                    observer.error = repr(exc)
                break
            stop.wait(.1)
    worker = threading.Thread(target=follow, daemon=True)
    worker.start()
    html = Path(__file__).with_name('coverage.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if self.headers.get('Origin') != 'http://'+self.headers.get('Host',''):
                self.send_error(403);return
            if self.path not in ('/scan/start','/scan/stop'):
                self.send_error(404);return
            try:
                result=observer.request_start() if self.path.endswith('start') else observer.request_stop()
                body=json.dumps(result).encode();status=200
            except Exception as exc:
                body=json.dumps(dict(error=str(exc))).encode();status=409
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_GET(self):
            if self.path == '/state.json':
                body = json.dumps(observer.snapshot(), allow_nan=False).encode()
                content_type = 'application/json'
            elif self.path in ('/', '/index.html'):
                body, content_type = html, 'text/html; charset=utf-8'
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    def retire_finished():
        finished_at=None
        while not stop.wait(.2):
            if (observer.folder/'result.json').exists():
                finished_at=finished_at or time.monotonic()
                if time.monotonic()-finished_at>=closed_idle_seconds:
                    print('COVERAGE_CLOSED_SESSION_RELEASE',flush=True)
                    server.shutdown()
                    return
    threading.Thread(target=retire_finished,daemon=True).start()
    print(f'COVERAGE_VIEW http://127.0.0.1:{server.server_port}/', flush=True)
    try:
        server.serve_forever()
    finally:
        stop.set()
        worker.join(timeout=2)
        server.server_close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('capture')
    p.add_argument('--calibration', required=True)
    p.add_argument('--plan')
    p.add_argument('--port', type=int, default=8768)
    p.add_argument('--closed-idle-seconds',type=float,default=60,help='Release observer/server after saved result; browser retains final snapshot')
    p.add_argument('--export', help='Read existing data once and export JSON without serving')
    p.add_argument('--write-example-plan', help='Save the proposed default plan to a new file')
    args = p.parse_args()
    if args.write_example_plan:
        with Path(args.write_example_plan).open('x', encoding='utf-8') as target:
            json.dump(DEFAULT_PLAN, target, indent=2)
    calibration = json.loads(Path(args.calibration).read_text(encoding='utf-8'))
    plan = json.loads(Path(args.plan).read_text(encoding='utf-8')) if args.plan else None
    observer = Observer(args.capture, calibration, plan)
    if args.export:
        observer.poll()
        if observer.pending:
            raise ValueError('PCM incomplete; cannot finish offline coverage export')
        atomic_json(args.export, observer.snapshot())
    else:
        if args.closed_idle_seconds<=0:raise ValueError('Positive closed-session lifetime required')
        serve(observer, args.port,args.closed_idle_seconds)


if __name__ == '__main__':
    main()
