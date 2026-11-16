"""Local three-shell speaker acquisition workbench; one existing broker owns HID.

The planner measures spherical-harmonic GEOMETRY, not calibrated acoustic
confidence. Demo never opens audio/tracking. Live recording is operator-started
and uses the existing loss-reporting four-channel capture implementation.
"""
import argparse
from collections import deque
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

from .calibration import (A_SERIAL, B_SERIAL, B_OFFSET_TRACKER_M, checked_pose,
                          summarize_static_pose)
from .core import rotate
from .rig_session import EventTail, atomic_json, mark
from .speaker_geometry import (fit_speaker_box, surface_from_static,
                               validate_speaker_model, world_to_speaker)
from .sphere_navigation import SphereNavigator

FACES = ('front', 'back', 'left', 'right', 'top', 'bottom')
FACE_NORMALS = dict(front=[0, 1, 0], back=[0, -1, 0], left=[-1, 0, 0],
                    right=[1, 0, 0], top=[0, 0, 1], bottom=[0, 0, -1])


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return default


def finite_vector(value, length=3):
    if not isinstance(value, (list, tuple)) or len(value) != length or any(
            type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ValueError('需要有限数值坐标')
    return [float(x) for x in value]


def normal_quaternion(axis):
    return {'+z': [1, 0, 0, 0], '-z': [0, 1, 0, 0],
            '+x': [math.sqrt(.5), 0, math.sqrt(.5), 0],
            '-x': [math.sqrt(.5), 0, -math.sqrt(.5), 0],
            '+y': [math.sqrt(.5), -math.sqrt(.5), 0, 0],
            '-y': [math.sqrt(.5), math.sqrt(.5), 0, 0]}[axis]


class SphereScan:
    def __init__(self, workspace, *, demo=False, rig=None, capture=None,
                 playback_plan=None, reference_folder=None, audio_config=None,
                 contact_transform=None, probe_offset_m=None):
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.demo = bool(demo)
        self.rig = Path(rig).resolve() if rig else None
        if not self.demo and self.rig is None:
            raise ValueError('实测模式需要 --rig 指向现有独立位姿服务')
        if self.demo and any((rig, capture, playback_plan, reference_folder, audio_config)):
            raise ValueError('演示模式不能绑定硬件采集或播放配置')
        settings = read_json(self.rig / 'rig.json') if self.rig else None
        if self.rig and (not settings or not settings.get('world_id')):
            raise ValueError('未找到有效 rig.json')
        self.world_id = settings['world_id'] if settings else 'sphere-demo-world'
        self.playback_plan = Path(playback_plan).resolve() if playback_plan else None
        self.reference_folder = Path(reference_folder).resolve() if reference_folder else None
        if bool(self.playback_plan) != bool(self.reference_folder):
            raise ValueError('播放路由和已知激励参考必须同时提供')
        self.audio_config = Path(audio_config).resolve() if audio_config else None
        self.capture_folder = Path(capture).resolve() if capture else None
        self.capture_process = None
        self.capture_log = None
        self.capture_owned = False
        self.capture_tail = None
        self.capture_pending = []
        self.capture_pose = None
        self.expected_audio_start = None
        self.last_audio_unix = 0.
        self.capture_pcm = None
        self.capture_ready = False
        self.playback_active = False
        self.playback_seen = False
        self.playback_start_requested = False
        self.finish_requested = False
        self.capture_error = None
        self.capture_counts = dict(audio_blocks=0, rejected_blocks=0, clipped_blocks=0)
        self.rig_tail = EventTail(self.rig / 'events.jsonl', at_end=True) if self.rig else None
        self.recent_a = deque(maxlen=4000)
        self.last_probe = None
        self.probe_seen_unix = 0.
        self.rig_status = {}
        self.error = None
        self.message = '逐面记录箱体，然后开始三层球面采集。'
        self.phase = 'calibration'
        self.faces = {}
        self.model = None
        self.contact_transform = checked_pose(contact_transform) if contact_transform else None
        self.probe_offset_m = finite_vector(probe_offset_m or B_OFFSET_TRACKER_M)
        self.plan = dict(radii_m=[.5, .65, .85], order=3, min_z_m=None,
                         max_z_m=None, segment_seconds=120)
        self.navigator = None
        self.nav_cache = None
        self.nav_updated = 0.
        self.segments = []
        self.trails = []
        self.trail_break = True
        self.demo_motion = False
        self.demo_position = [0., .5, 0.]
        self.last_poll = time.monotonic()
        self.last_save = 0.
        self.session_id = 'sphere-' + uuid.uuid4().hex[:12]
        self.recording_id = uuid.uuid4().hex[:8]
        self._restore()
        if self.capture_folder:
            self._attach_capture(self.capture_folder)
        self._save()

    def _restore(self):
        saved = read_json(self.workspace / 'scan-session.json')
        if not saved:
            return
        if saved.get('world_id') != self.world_id or saved.get('mode') != self.mode:
            raise ValueError('工作目录属于另一坐标世界或运行模式，请选择新目录')
        self.session_id = saved['session_id']
        self.recording_id = saved.get('recording_id', self.recording_id)
        self.faces = saved.get('faces', {})
        self.model = saved.get('model')
        self.contact_transform = saved.get('contact_transform', self.contact_transform)
        self.probe_offset_m = finite_vector(saved.get('probe_offset_m', self.probe_offset_m))
        self.plan.update(saved.get('plan', {}))
        self.segments = saved.get('segments', [])
        self.trails = saved.get('trails', [])
        if not self.demo:
            for attr in ('playback_plan', 'reference_folder', 'audio_config'):
                if getattr(self, attr) is None and saved.get(attr):
                    setattr(self, attr, Path(saved[attr]))
            # Reattach a surviving writer after a browser/server restart. Never
            # launch a second audio owner just because the UI was restarted.
            if self.capture_folder is None and saved.get('capture_folder'):
                prior = Path(saved['capture_folder'])
                if (prior / 'session.json').exists() and not (prior / 'result.json').exists():
                    self.capture_folder = prior
        for segment in self.segments:
            if segment['status'] in ('scanning', 'preparing'):
                segment['status'] = 'interrupted'
                segment.setdefault('end_unix', saved.get('saved_unix'))
        if self.model:
            validate_speaker_model(self.model, self.world_id)
            self._make_navigator()
            points_path = self.workspace / 'scan-points.jsonl'
            if points_path.exists():
                raw = points_path.read_bytes()
                lines = raw.splitlines(keepends=True)
                valid_bytes = 0
                for index, line in enumerate(lines):
                    try:
                        point = json.loads(line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        if index != len(lines) - 1 or line.endswith(b'\n'):
                            raise ValueError('导航点日志内部损坏，已保留原文件；请检查后再恢复') from None
                        evidence = points_path.with_name('scan-points.partial-' + uuid.uuid4().hex[:8] + '.bin')
                        evidence.write_bytes(line)
                        with points_path.open('r+b') as target:
                            target.truncate(valid_bytes)
                        break
                    valid_bytes += len(line)
                    if point.get('recording_id') == self.recording_id:
                        self.navigator.add_sample(point['position_local_m'])
                else:
                    if raw and not raw.endswith(b'\n'):
                        with points_path.open('ab') as target:
                            target.write(b'\n')
            self.phase = 'finished' if saved.get('phase') == 'finished' else 'paused' if self.segments else 'ready'
        self.message = '已恢复标定与已录轨迹。实测继续前将重新检查坐标世界和数据源。'

    @property
    def mode(self):
        return 'demo' if self.demo else 'live'

    def _save(self):
        atomic_json(self.workspace / 'scan-session.json', dict(
            schema=1, kind='lsac_sphere_session', mode=self.mode,
            session_id=self.session_id, recording_id=self.recording_id,
            world_id=self.world_id, saved_unix=time.time(), phase=self.phase,
            faces=self.faces, model=self.model, contact_transform=self.contact_transform,
            probe_offset_m=self.probe_offset_m, plan=self.plan, segments=self.segments,
            trails=self.trails,
            capture_folder=str(self.capture_folder) if self.capture_folder else None,
            playback_plan=str(self.playback_plan) if self.playback_plan else None,
            reference_folder=str(self.reference_folder) if self.reference_folder else None,
            audio_config=str(self.audio_config) if self.audio_config else None,
            confidence_basis='geometry_only', acoustic_confidence_validated=False,
            audio_pose_alignment_validated=False))
        self.last_save = time.monotonic()

    def _event(self, action, **data):
        with (self.workspace / 'scan-actions.jsonl').open('a', encoding='utf-8') as target:
            target.write(json.dumps(dict(action=action, unix=time.time(), mode=self.mode,
                                         world_id=self.world_id, **data), ensure_ascii=False,
                                    allow_nan=False) + '\n')

    def _make_navigator(self):
        self.navigator = SphereNavigator(self.plan['radii_m'], self.plan['order'],
            min_z_m=self.plan['min_z_m'], max_z_m=self.plan['max_z_m'])
        self.nav_cache = None
        self.nav_updated = 0.

    def _demo_face(self, face):
        normal = FACE_NORMALS[face]
        extent = .17 if face in ('front', 'back') else .15 if face in ('left', 'right') else .25
        return dict(schema=1, kind='speaker_face_sample', world_id=self.world_id,
                    point_m=[v * extent for v in normal], normal_world=normal,
                    synthetic=True, basis='demo_geometry_not_physical_calibration')

    def _finish_calibration(self):
        self.model = fit_speaker_box(self.faces, self.world_id)
        self.model['synthetic'] = self.demo
        radius = self.model['enclosing_radius_m']
        self.plan['radii_m'] = [round(radius + d, 3) for d in (.20, .35, .55)]
        self._make_navigator()
        self.phase = 'ready'
        self.message = '六面标定完成；扫描半径以箱体中心到探针声学中心计。'
        atomic_json(self.workspace / 'speaker-calibration.json', self.model)

    def _attach_capture(self, folder):
        session = read_json(folder / 'session.json')
        if not session:
            raise ValueError('未找到采集 session.json')
        config = session['config']
        if config.get('world_id') != self.world_id:
            raise ValueError('采集与箱体标定不属于同一坐标世界')
        if config.get('synthetic'):
            raise ValueError('实测模式不能绑定合成录音')
        if config.get('audio', {}).get('channels') != 4:
            raise ValueError('采集必须保留 H3-VR 四通道')
        if not config.get('playback', {}).get('reference'):
            raise ValueError('三层扫描需要绑定已知激励参考的采集')
        self.capture_config = config
        self.capture_tail = EventTail(folder / 'events.jsonl')
        self.capture_pending = []
        self.capture_pose = None
        self.expected_audio_start = None
        self.last_audio_unix = 0.
        self.playback_active = False
        self.playback_seen = False
        self.playback_start_requested = False

    def _launch_capture(self):
        if not self.playback_plan or not self.reference_folder:
            raise ValueError('启动实测需要 --playback-plan 与 --reference-folder，或绑定已有 --capture')
        metadata = read_json(self.reference_folder / 'reference.json')
        if not metadata or not 0 < metadata.get('seconds', 0) < 3595:
            raise ValueError('激励参考长度需在 0～3595 秒内')
        # Validate paths before launching; the existing playback code verifies
        # route identity, exact reference hashes and format before device I/O.
        if not self.playback_plan.is_file() or self.audio_config and not self.audio_config.is_file():
            raise ValueError('采集或播放配置文件不存在')
        self.capture_folder = self.workspace / ('capture-' + uuid.uuid4().hex[:12])
        marker = 'sphere-play-' + uuid.uuid4().hex
        seconds = min(3600., max(180., metadata['seconds'] + 30.))
        args = [sys.executable, '-u', '-m', 'lsac_capture.rig_capture', str(self.rig),
                str(self.capture_folder), '--purpose', 'scan-preview', '--seconds', str(seconds),
                '--playback-plan', str(self.playback_plan), '--reference-folder', str(self.reference_folder),
                '--confirm-playback', '--playback-delay', '1', '--play-on-marker', marker,
                '--stop-after-playback']
        if self.audio_config:
            args += ['--config', str(self.audio_config)]
        self.capture_log = (self.workspace / (self.capture_folder.name + '.log')).open('w', encoding='utf-8')
        self.capture_process = subprocess.Popen(args, stdout=self.capture_log,
            stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.capture_owned = True
        self.finish_requested = False
        self.capture_tail = None
        self.capture_error = None
        self.playback_active = False
        self.playback_seen = False
        self.capture_ready = False

    def _marker(self, label):
        if self.demo:
            return None
        return mark(self.rig, label)

    def _start_segment(self):
        segment = dict(id=uuid.uuid4().hex[:8], start_unix=time.time(), start_lsl=None,
                       active_seconds=0., samples=0, status='scanning',
                       capture_folder=str(self.capture_folder) if self.capture_folder else None)
        if not self.demo:
            self._marker('sphere-start:' + segment['id'])
        self.segments.append(segment)
        self.trail_break = True
        self.phase = 'scanning'
        self.message = '沿推荐短弧扫描；可以随时暂停休息。'
        self._event('segment_start', segment_id=segment['id'])

    def _pause(self, reason='已暂停扫描；原始录音与激励继续，休息轨迹不计入扫描。'):
        if self.phase not in ('scanning', 'preparing'):
            raise ValueError('当前没有正在采集的扫描段')
        if self.segments and self.segments[-1]['status'] == 'scanning':
            segment = self.segments[-1]
            segment.update(status='paused', end_unix=time.time())
            try:
                self._marker('sphere-pause:' + segment['id'])
            except (OSError, ValueError) as exc:
                segment['marker_error'] = str(exc)
        self.phase = 'paused'
        self.trail_break = True
        self._refresh_navigation()
        self.message = reason
        self._event('pause', reason=reason)
        self._save()

    def _trail(self, segment_id, point):
        if (self.trail_break or not self.trails or self.trails[-1]['segment_id'] != segment_id or
                self.trails[-1]['points'] and math.dist(self.trails[-1]['points'][-1], point) > .25):
            self.trails.append(dict(segment_id=segment_id, points=[]))
            self.trail_break = False
        points = self.trails[-1]['points']
        if not points or math.dist(points[-1], point) >= .015:
            points.append(point)
            if len(points) > 1800:
                self.trails[-1]['points'] = points[::2]

    def _record_point(self, point, *, stamp=None, audio_event=None):
        if self.phase != 'scanning' or not self.navigator or not self.segments:
            return
        if math.sqrt(sum(v*v for v in point)) <= self.model['enclosing_radius_m'] + .03:
            self.trail_break = True
            return
        self._trail(self.segments[-1]['id'], point)
        if self.navigator.add_sample(point):
            segment = self.segments[-1]
            segment['samples'] += 1
            event = dict(position_local_m=point, segment_id=segment['id'],
                         recording_id=self.recording_id, arrival_lsl=stamp,
                         basis='geometry_only', synthetic=self.demo)
            if audio_event:
                event.update(audio_sample_start_i64=audio_event.get('sample_start_i64'),
                             capture_folder=str(self.capture_folder))
            with (self.workspace / 'scan-points.jsonl').open('a', encoding='utf-8') as target:
                target.write(json.dumps(event, allow_nan=False) + '\n')

    def _probe_position(self, pose):
        checked_pose(pose)
        offset = rotate(pose['quaternion_wxyz'], self.probe_offset_m)
        return [a + b for a, b in zip(pose['position_m'], offset)]

    def _refresh_navigation(self):
        if not self.navigator:
            return
        current = self.demo_position if self.demo else (
            world_to_speaker(self.model, self._probe_position(self.last_probe)) if self._fresh() else None)
        self.nav_cache = self.navigator.snapshot(current, stale=not self._fresh())
        self.nav_updated = time.monotonic()

    def _fresh(self):
        if self.demo:
            return True
        if self.error or not self.last_probe:
            return False
        now = time.time()
        p = self.last_probe
        return (self.rig_status.get('phase') != 'closed' and
                0 <= now - self.rig_status.get('updated_unix', 0) < 2 and
                0 <= now - self.probe_seen_unix < .6 and p.get('valid') is True and
                0 <= p.get('optical_age_seconds', math.inf) < .25 and
                -.05 <= self._clock_now() - p['arrival_lsl'] < .6)

    def _clock_now(self):
        """Arrival-clock preview only; never relabel this as audio/pose alignment."""
        return self.rig_status.get('updated_lsl', 0.) + max(
            0., time.time() - self.rig_status.get('updated_unix', time.time()))

    def _poll_rig(self):
        state = read_json(self.rig / 'status.json', {})
        if state.get('world_id') != self.world_id:
            raise ValueError('位姿世界已改变；当前标定不可继续使用')
        self.rig_status = state
        events = self.rig_tail.read()
        for event in events:
            if event.get('serial') == A_SERIAL and event.get('kind') in ('pose', 'tracker_disconnect', 'tracker_callback_error'):
                self.recent_a.append(event)
            if event.get('serial') == B_SERIAL and event.get('kind') == 'pose':
                self.last_probe = event
                self.probe_seen_unix = time.time()
        if not self.last_probe:
            self.last_probe = state.get('trackers', {}).get(B_SERIAL, {}).get('last_pose')
            if self.last_probe:
                self.probe_seen_unix = state.get('updated_unix', 0)

    def _poll_capture(self):
        if not self.capture_folder:
            return
        if self.finish_requested and self.capture_folder.exists() and not (self.capture_folder / 'result.json').exists():
            if not (self.capture_folder / 'FINISH.json').exists():
                atomic_json(self.capture_folder / 'FINISH.json', dict(kind='operator_finish', request_unix=time.time()))
        if self.capture_tail is None and (self.capture_folder / 'session.json').exists():
            self._attach_capture(self.capture_folder)
        live = read_json(self.capture_folder / 'live-status.json', {})
        result = read_json(self.capture_folder / 'result.json')
        self.capture_ready = live.get('phase') == 'ready' and not result
        if live.get('failures') or result and (result.get('status') == 'incomplete' or result.get('capture_failures')):
            self.capture_error = '采集报告数据错误；原始数据和错误日志已保留。'
        if self.capture_process and self.capture_process.poll() is not None and not result:
            self.capture_error = '采集进程已退出，请查看工作目录内的 capture 日志。'
        if self.capture_tail:
            self.capture_pending.extend(self.capture_tail.read())
            consumed = 0
            for event in self.capture_pending:
                kind = event.get('kind')
                if kind == 'playback_started':
                    self.playback_active = self.playback_seen = True
                elif kind == 'playback_finished':
                    self.playback_active = False
                elif kind == 'pose' and event.get('serial') == B_SERIAL:
                    self.capture_pose = event
                elif kind == 'operator_marker' and self.segments:
                    if event.get('label') == 'sphere-start:' + self.segments[-1]['id']:
                        self.segments[-1]['start_lsl'] = event.get('arrival_lsl')
                elif kind == 'audio_block':
                    self.capture_counts['audio_blocks'] += 1
                    logical = event.get('sample_start_i64')
                    frames = event.get('frames')
                    sequence_ok = (type(logical) is int and type(frames) is int and frames > 0 and
                                   (self.expected_audio_start is None or logical == self.expected_audio_start))
                    if self.phase == 'scanning' and self.playback_active and self._fresh():
                        pose = self.capture_pose
                        stamp = event.get('arrival_lsl', 0)
                        start = self.segments[-1].get('start_lsl')
                        valid = bool(sequence_ok and pose and pose.get('valid') is True and start is not None and
                            pose.get('source_world_id') == self.world_id and
                            stamp >= start and pose.get('arrival_lsl', 0) >= start and
                            0 <= stamp - pose.get('arrival_lsl', -1) <= .08 and
                            0 <= pose.get('optical_age_seconds', math.inf) < .25 and
                            not any(event.get(k) for k in ('flags', 'silent', 'discontinuity', 'timestamp_error', 'device_gap_frames')) and
                            event.get('rate') == 48000 and event.get('channels') == 4 and event.get('valid_bits') == 24 and
                            event.get('timestamp_basis') == 'wasapi_first_frame_qpc_mapped_to_lsl' and
                            -.05 <= self._clock_now() - stamp < 1.)
                        if valid:
                            bits = event.get('container_bits')
                            if bits not in (24, 32):
                                valid = False
                            else:
                                size = event['frames'] * 4 * (bits // 8)
                                with (self.capture_folder / 'audio.pcm').open('rb') as pcm:
                                    pcm.seek(event['pcm_byte_offset'])
                                    raw = pcm.read(size)
                                if len(raw) < size:
                                    self.capture_counts['audio_blocks'] -= 1
                                    break
                                import numpy as np
                                from .session import unpack24
                                samples = unpack24(raw, bits)
                                if np.any((samples <= -8388608) | (samples >= 8388607)):
                                    valid = False
                                    self.capture_counts['clipped_blocks'] += 1
                        if valid:
                            self._record_point(world_to_speaker(self.model, self._probe_position(pose)),
                                               stamp=stamp, audio_event=event)
                        else:
                            self.capture_counts['rejected_blocks'] += 1
                            self.trail_break = True
                    if type(logical) is int and type(frames) is int and frames > 0:
                        self.expected_audio_start = max(self.expected_audio_start or 0, logical + frames)
                    if -.05 <= self._clock_now() - event.get('arrival_lsl', 0) < 1.:
                        self.last_audio_unix = time.time()
                consumed += 1
            del self.capture_pending[:consumed]
        if self.phase == 'preparing' and self.capture_ready and not self.capture_error:
            if not self.playback_seen and not self.playback_start_requested:
                trigger = self.capture_config['playback'].get('trigger_operator_marker')
                if trigger:
                    self._marker(trigger)
                    self.playback_start_requested = True
            if self.playback_active and time.time() - self.last_audio_unix < 1.:
                self._start_segment()
        if self.phase == 'scanning' and (result or self.capture_error or
                self.playback_seen and not self.playback_active):
            self._pause(self.capture_error or '本次激励已结束；录音保留，可结束或启动下一轮采集。')
        if self.phase == 'scanning' and time.time() - self.last_audio_unix > 2.:
            self._pause('录音数据已过期，扫描已暂停；检查采集状态后继续。')
        if self.phase == 'preparing' and (result or self.capture_error):
            self._pause(self.capture_error or '本次采集已结束，请重新开始。')

    def poll(self):
        with self.lock:
            now = time.monotonic()
            dt = min(.5, max(0., now - self.last_poll))
            self.last_poll = now
            if self.demo:
                if self.phase == 'scanning' and self.demo_motion and self.navigator:
                    nav = self.nav_cache or self.navigator.snapshot(self.demo_position)
                    recommendation = nav.get('recommendation')
                    if recommendation:
                        target = recommendation['point_local_m']
                        # Interpolate on a spherical arc, not a chord through
                        # the speaker. Radial transitions stay outside the box.
                        import numpy as np
                        p, t = np.asarray(self.demo_position), np.asarray(target)
                        radius, target_radius = np.linalg.norm(p), np.linalg.norm(t)
                        a, b = p / radius, t / target_radius
                        angle = math.acos(float(np.clip(a @ b, -1., 1.)))
                        step = min(angle, .35 * dt / max(radius, .1))
                        tangent = b - float(a @ b) * a
                        if np.linalg.norm(tangent) < 1e-9:
                            tangent = np.cross(a, [0, 0, 1] if abs(a[2]) < .9 else [1, 0, 0])
                        tangent /= np.linalg.norm(tangent)
                        direction = math.cos(step) * a + math.sin(step) * tangent
                        radius += float(np.clip(target_radius - radius, -.2 * dt, .2 * dt))
                        self.demo_position = (radius * direction).tolist()
                        self._record_point(list(self.demo_position))
            else:
                try:
                    self._poll_rig()
                    self.error = None
                    self._poll_capture()
                except (OSError, ValueError, KeyError) as exc:
                    self.error = str(exc)
                if self.phase == 'scanning' and not self._fresh():
                    self._pause('位姿数据过期或中断，扫描已暂停；恢复跟踪后可继续。')
            if self.phase == 'scanning':
                self.segments[-1]['active_seconds'] += dt
                if self.segments[-1]['active_seconds'] >= self.plan['segment_seconds']:
                    self._pause('这一段已完成，休息后点继续。原始录音与激励可保持连续。')
            if self.navigator and now - self.nav_updated >= .7:
                self._refresh_navigation()
            if now - self.last_save >= 3:
                self._save()

    def action(self, data):
        with self.lock:
            action = data.get('action')
            changing_geometry = ('configure_contact', 'capture_face', 'undo_face',
                                 'reset_calibration', 'finish_calibration', 'demo_calibration')
            if action in changing_geometry and (self.phase in ('scanning', 'preparing') or
                                                any(s.get('samples', 0) for s in self.segments)):
                raise ValueError('已有扫描数据时不能改动几何；请先结束并新建一轮')
            if action == 'configure_contact':
                if data.get('confirmed') is not True:
                    raise ValueError('请确认输入的是实际接触面的偏移与外法线')
                offset = finite_vector(data.get('offset_mm'))
                if max(abs(v) for v in offset) > 1000:
                    raise ValueError('接触偏移超出 1 米，请检查单位')
                try:
                    q = normal_quaternion(data.get('normal_axis'))
                except KeyError:
                    raise ValueError('请选择接触面外法线轴') from None
                self.contact_transform = dict(position_m=[v / 1000 for v in offset], quaternion_wxyz=q)
                self.faces = {}
                self.model = self.navigator = self.nav_cache = None
                self.phase = 'calibration'
                self.message = '接触面定义已保存，请依次贴合六个面。'
            elif action == 'capture_face':
                face = data.get('face')
                if face not in FACES:
                    raise ValueError('未知箱体面')
                if self.demo:
                    result = self._demo_face(face)
                else:
                    live = read_json(self.rig / 'status.json', {})
                    if self.error or live.get('world_id') != self.world_id:
                        raise ValueError('位姿世界不一致或数据源异常，不能继续标定')
                    if self.contact_transform is None:
                        raise ValueError('请先设置 Tracker A 的真实接触面偏移和外法线')
                    if not self.rig_status or time.time() - self.rig_status.get('updated_unix', 0) > 2:
                        raise ValueError('位姿服务数据过期')
                    samples = [e for e in self.recent_a if e.get('kind') == 'pose']
                    if not samples:
                        raise ValueError('等待 Tracker A 位姿')
                    end = samples[-1]['arrival_lsl']
                    if not -.05 <= self._clock_now() - end < .25:
                        raise ValueError('Tracker A 位姿过期')
                    sampled = summarize_static_pose(list(self.recent_a), A_SERIAL, end - 1.5, end,
                                                     self.world_id)
                    result = surface_from_static(sampled, self.contact_transform)
                self.faces[face] = result
                self.model = self.navigator = self.nav_cache = None
                self.phase = 'calibration'
                self.message = '已记录此面；保持外法线朝向箱体外侧。'
            elif action == 'undo_face':
                self.faces.pop(data.get('face'), None)
                self.model = self.navigator = self.nav_cache = None
                self.phase = 'calibration'
            elif action == 'reset_calibration':
                self.faces = {}
                self.model = self.navigator = self.nav_cache = None
                self.phase = 'calibration'
            elif action == 'finish_calibration':
                self._finish_calibration()
            elif action == 'demo_calibration':
                if not self.demo:
                    raise ValueError('实测模式不允许演示标定')
                self.faces = {f: self._demo_face(f) for f in FACES}
                self._finish_calibration()
                self.demo_position = [0, self.plan['radii_m'][0], 0]
            elif action == 'configure_plan':
                if self.phase in ('scanning', 'preparing'):
                    raise ValueError('请先暂停扫描再调整计划')
                if not self.model:
                    raise ValueError('请先完成箱体标定')
                radii = finite_vector(data.get('radii_m'))
                if not self.model['enclosing_radius_m'] + .03 <= radii[0] < radii[1] < radii[2] <= 5:
                    raise ValueError('三层半径需递增，并在箱体包围球外留出至少 3 cm；上限 5 m')
                order = data.get('order', self.plan['order'])
                if type(order) is not int or not 1 <= order <= 4:
                    raise ValueError('在线几何球谐阶数范围为 1～4')
                limits = [data.get('min_z_m'), data.get('max_z_m')]
                for value in limits:
                    if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                        raise ValueError('可达高度必须是有限米数或留空')
                if all(v is not None for v in limits) and limits[0] >= limits[1]:
                    raise ValueError('最低高度必须小于最高高度')
                duration = data.get('segment_seconds', self.plan['segment_seconds'])
                if type(duration) not in (int, float) or not math.isfinite(duration) or not 15 <= duration <= 600:
                    raise ValueError('每段时长范围为 15～600 秒')
                has_data = any(s.get('samples', 0) for s in self.segments)
                if has_data and (radii != self.plan['radii_m'] or order != self.plan['order']):
                    raise ValueError('已有数据时可调整可达高度和每段时长；更改球层或阶数请新建一轮')
                self.plan.update(radii_m=radii, order=order, min_z_m=limits[0], max_z_m=limits[1],
                                 segment_seconds=duration)
                if has_data:
                    self.navigator.set_reach_limits(min_z_m=limits[0], max_z_m=limits[1])
                    self.nav_updated = 0.
                else:
                    self._make_navigator()
                self.message = '三层球面和可达范围已更新；不可达区域仍保留为缺口。'
            elif action == 'start':
                if not self.model or self.phase not in ('ready', 'paused'):
                    raise ValueError('请完成标定；结束后可新建一轮')
                if not self._fresh():
                    raise ValueError('请等待新鲜的探针位姿')
                if self.demo:
                    self._start_segment()
                else:
                    if self.capture_folder and not self.capture_owned and (self.capture_folder / 'result.json').exists():
                        raise ValueError('绑定的采集已经结束，请启动新采集或配置播放参考')
                    if not self.capture_folder or self.capture_owned and (
                            (self.capture_folder / 'result.json').exists() or
                            self.capture_process and self.capture_process.poll() is not None):
                        self._launch_capture()
                    if self.playback_active and self.capture_ready and not self.capture_error and time.time() - self.last_audio_unix < 1.:
                        self._start_segment()
                    else:
                        self.phase = 'preparing'
                        self.message = '正在准备四通道录音与已知激励，准备好后进入扫描。'
            elif action == 'pause':
                self._pause()
            elif action == 'finish':
                self.finish_requested = True
                if self.phase in ('scanning', 'preparing'):
                    self._pause()
                if self.capture_folder and self.capture_folder.exists() and not (self.capture_folder / 'result.json').exists():
                    atomic_json(self.capture_folder / 'FINISH.json', dict(kind='operator_finish', request_unix=time.time()))
                self.phase = 'finished'
                self.message = '本轮导航已保存；采集端将停止激励并保存录音尾段。'
            elif action == 'new_scan':
                if self.phase in ('scanning', 'preparing'):
                    raise ValueError('请先暂停或结束当前扫描')
                if self.capture_folder and self.capture_owned and self.capture_process and self.capture_process.poll() is None:
                    raise ValueError('请先结束本轮并等待录音保存完成')
                archive = dict(recording_id=self.recording_id, segments=self.segments,
                               model=self.model, plan=self.plan, contact_transform=self.contact_transform,
                               probe_offset_m=self.probe_offset_m, world_id=self.world_id,
                               capture_folder=str(self.capture_folder) if self.capture_folder else None,
                               mode=self.mode, acoustic_confidence_validated=False)
                atomic_json(self.workspace / ('round-' + self.recording_id + '.json'), archive)
                self._event('archive_round', recording_id=self.recording_id)
                self.recording_id = uuid.uuid4().hex[:8]
                self.segments, self.trails = [], []
                self.trail_break = True
                if self.model:
                    self._make_navigator()
                self.phase = 'ready' if self.model else 'calibration'
            elif action == 'skip':
                if self.navigator:
                    self.navigator.skip()
                    self.nav_updated = 0.
            elif action == 'demo_motion':
                if not self.demo:
                    raise ValueError('实测模式不能生成演示轨迹')
                self.demo_motion = data.get('enabled') is True
            else:
                raise ValueError('未知操作')
            self._refresh_navigation()
            self._event(action)
            self._save()
            return dict(ok=True, message=self.message)

    def snapshot(self):
        with self.lock:
            fresh = self._fresh()
            probe = None
            if self.demo and self.model:
                probe = dict(position_local_m=self.demo_position, position_world_m=self.demo_position, fresh=True)
            elif self.last_probe and self.model:
                try:
                    world = self._probe_position(self.last_probe)
                    probe = dict(position_local_m=world_to_speaker(self.model, world), position_world_m=world, fresh=fresh)
                except ValueError:
                    pass
            nav = copy.deepcopy(self.nav_cache)
            if nav and not fresh:
                nav.update(stale=True, recommendation=None)
            next_face = next((f for f in FACES if f not in self.faces), None)
            a_latest = next((e for e in reversed(self.recent_a) if e.get('kind') == 'pose'), None)
            can_calibrate = self.demo or bool(self.contact_transform and a_latest and a_latest.get('valid') and
                time.time() - self.rig_status.get('updated_unix', 0) < 2 and
                -.05 <= self._clock_now() - a_latest.get('arrival_lsl', -99) < .25)
            if self.capture_error:
                capture_status = 'error'
            elif self.demo:
                capture_status = 'demo'
            elif self.capture_folder and (self.capture_folder / 'result.json').exists():
                capture_status = 'saved'
            elif self.finish_requested and self.capture_folder:
                capture_status = 'finishing'
            elif self.playback_active:
                capture_status = 'recording'
            elif self.capture_ready:
                capture_status = 'ready'
            elif self.capture_folder:
                capture_status = 'preparing'
            else:
                capture_status = 'idle'
            return dict(schema=1, mode=self.mode, phase=self.phase, session_id=self.session_id,
                source=dict(connected=fresh, stale=not fresh, world_id=self.world_id,
                            pose_age_ms=(max(0., self._clock_now() - self.last_probe['arrival_lsl']) * 1000
                                         if self.last_probe and not self.demo else 0 if self.demo else None),
                            status='demo' if self.demo else self.rig_status.get('phase', 'waiting'),
                            error=self.error, rig_folder=str(self.rig) if self.rig else None,
                            capture_folder=str(self.capture_folder) if self.capture_folder else None),
                calibration=dict(faces=copy.deepcopy(self.faces), contact_configured=self.contact_transform is not None,
                                 contact_transform=self.contact_transform, can_capture=can_calibrate,
                                 model=copy.deepcopy(self.model), next_face=next_face, error=None),
                plan=dict(self.plan), probe=probe, navigation=nav,
                trails=copy.deepcopy(self.trails), segments=copy.deepcopy(self.segments),
                elapsed_active_seconds=sum(s['active_seconds'] for s in self.segments),
                capture=dict(status=capture_status, ready=self.capture_ready,
                             configured=self.demo or bool(self.capture_folder or self.playback_plan),
                             error=self.capture_error, **self.capture_counts),
                message=self.message, demo_motion=self.demo_motion,
                acoustic_confidence_validated=False, timing_basis='arrival_preview_not_aligned')

    def close(self):
        with self.lock:
            if self.phase in ('scanning', 'preparing'):
                self._pause('导航服务退出，扫描段已暂停并保存。')
            if self.capture_owned and self.capture_folder and self.capture_folder.exists() and not (self.capture_folder / 'result.json').exists():
                atomic_json(self.capture_folder / 'FINISH.json', dict(kind='operator_finish', request_unix=time.time()))
            self._save()
        if self.capture_process:
            deadline = time.monotonic() + 40
            while self.capture_process.poll() is None and time.monotonic() < deadline:
                if self.capture_folder.exists() and not (self.capture_folder / 'FINISH.json').exists():
                    atomic_json(self.capture_folder / 'FINISH.json', dict(kind='operator_finish', request_unix=time.time()))
                time.sleep(.2)
            if self.capture_process.poll() is None:
                self._event('capture_still_draining', pid=self.capture_process.pid)
        if self.capture_log:
            self.capture_log.close()


def make_server(controller, port=8769):
    assets = Path(__file__).parent
    class Handler(BaseHTTPRequestHandler):
        def response(self, status, body, content_type='application/json; charset=utf-8', download=False):
            if isinstance(body, (dict, list)):
                body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            if download:
                self.send_header('Content-Disposition', 'attachment; filename="sphere-scan.json"')
            self.end_headers()
            self.wfile.write(body)

        def local_host(self):
            return self.headers.get('Host') in ('127.0.0.1:' + str(self.server.server_port),
                                                'localhost:' + str(self.server.server_port))

        def do_GET(self):
            if not self.local_host():
                self.send_error(403)
                return
            if self.path in ('/api/state', '/api/export'):
                self.response(200, controller.snapshot(), download=self.path.endswith('export'))
            elif self.path in ('/', '/index.html', '/sphere_scan.js', '/sphere_scan.css'):
                name = 'sphere_scan.html' if self.path in ('/', '/index.html') else self.path[1:]
                content_type = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript; charset=utf-8',
                                'css': 'text/css; charset=utf-8'}[name.split('.')[-1]]
                self.response(200, (assets / name).read_bytes(), content_type)
            else:
                self.send_error(404)

        def do_POST(self):
            if not self.local_host() or self.headers.get('Origin') != 'http://' + self.headers.get('Host', ''):
                self.response(403, dict(error='仅接受本机同源操作'))
                return
            if self.path != '/api/action':
                self.send_error(404)
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16384:
                    raise ValueError('请求长度无效')
                data = json.loads(self.rfile.read(length))
                if not isinstance(data, dict):
                    raise ValueError('请求必须是对象')
                self.response(200, controller.action(data))
            except (ValueError, KeyError, OSError) as exc:
                self.response(409, dict(error=str(exc)))

        def log_message(self, *args):
            pass
    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help='New/persistent scan directory')
    parser.add_argument('--demo', action='store_true', help='Synthetic UI rehearsal; never opens hardware')
    parser.add_argument('--rig', help='Existing rig broker directory; never opens another HID context')
    parser.add_argument('--capture', help='Attach existing four-channel capture with known excitation')
    parser.add_argument('--playback-plan', help='Explicit existing output route JSON')
    parser.add_argument('--reference-folder', help='Exact known excitation reference directory')
    parser.add_argument('--audio-config', help='Optional existing capture config JSON')
    parser.add_argument('--contact-transform', help='Explicit tracker_from_surface pose JSON for tracker A')
    parser.add_argument('--probe-offset-m', nargs=3, type=float, help='Tracker B to H3 acoustic-centre offset')
    parser.add_argument('--port', type=int, default=8769)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error('port must be 0..65535')
    controller = SphereScan(args.workspace, demo=args.demo, rig=args.rig, capture=args.capture,
        playback_plan=args.playback_plan, reference_folder=args.reference_folder, audio_config=args.audio_config,
        contact_transform=read_json(args.contact_transform) if args.contact_transform else None,
        probe_offset_m=args.probe_offset_m)
    server = make_server(controller, args.port)
    stop = threading.Event()
    def follow():
        while not stop.is_set():
            try:
                controller.poll()
            except Exception as exc:
                with controller.lock:
                    controller.error = str(exc)
                    if controller.phase in ('scanning', 'preparing'):
                        controller._pause('数据处理异常，扫描已暂停；请查看状态。')
            stop.wait(.1)
    worker = threading.Thread(target=follow, daemon=True)
    worker.start()
    print(f'SPHERE_SCAN http://127.0.0.1:{server.server_port}/ mode={controller.mode}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        worker.join(timeout=3)
        server.server_close()
        controller.close()


if __name__ == '__main__':
    main()
