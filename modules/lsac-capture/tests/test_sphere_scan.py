"""File-backed integration checks; no recorder, tracker, or playback is opened."""
import copy
import io
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from lsac_capture.calibration import A_SERIAL, B_SERIAL
from lsac_capture.sphere_scan import FACES, FACE_NORMALS, SphereScan, make_server


WORLD = "sphere-test-world"


def write_json(path, value):
    Path(path).write_text(json.dumps(value, allow_nan=False), encoding="utf-8")


def append_events(folder, *events):
    with (folder / "events.jsonl").open("a", encoding="utf-8") as output:
        for event in events:
            output.write(json.dumps(event, allow_nan=False) + "\n")


class SphereScanTests(unittest.TestCase):
    def test_pause_publishes_final_navigation_count_without_late_increment(self):
        controller = SphereScan(self.root / 'pause-cache', demo=True)
        controller.action(dict(action='demo_calibration'))
        controller.action(dict(action='start'))
        before = controller.snapshot()['navigation']['recording_bin_count']
        controller._record_point([0., controller.plan['radii_m'][0], 0.])
        self.assertEqual(controller.snapshot()['navigation']['recording_bin_count'], before)
        controller.action(dict(action='pause'))
        stopped = controller.snapshot()['navigation']['recording_bin_count']
        self.assertGreater(stopped, before)
        controller.poll()
        self.assertEqual(controller.snapshot()['navigation']['recording_bin_count'], stopped)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="lsac-sphere-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.counter = 0
        self.mark = self.enterContext(patch("lsac_capture.sphere_scan.mark", return_value={"status": "queued"}))
        self.launch = self.enterContext(patch("lsac_capture.sphere_scan.subprocess.Popen",
            side_effect=AssertionError("Tests must never launch a hardware process")))

    def make_capture(self, folder):
        folder.mkdir(parents=True, exist_ok=True)
        config = dict(world_id=WORLD, synthetic=False,
            audio=dict(channels=4, rate=48000, container_bits=24, valid_bits=24, format="PCM"),
            playback=dict(reference=dict(kind="music_noise_bandlimited", seconds=600,
                rate=48000, channels=2, sha256="test-reference"),
                trigger_operator_marker="test-playback-trigger"))
        write_json(folder / "session.json", dict(schema=1, config=config))
        write_json(folder / "live-status.json", dict(phase="ready", failures=[]))
        (folder / "events.jsonl").write_text("", encoding="utf-8")
        (folder / "audio.pcm").write_bytes(b"")
        return folder

    def make_live(self, *, capture=True, calibrated=True):
        self.counter += 1
        location = self.root / str(self.counter)
        rig = location / "rig"
        rig.mkdir(parents=True)
        write_json(rig / "rig.json", dict(world_id=WORLD))
        (rig / "events.jsonl").write_text("", encoding="utf-8")
        capture_folder = self.make_capture(location / "capture") if capture else None
        controller = SphereScan(location / "work", rig=rig, capture=capture_folder,
            contact_transform=dict(position_m=[0., 0., .02], quaternion_wxyz=[1., 0., 0., 0.]))
        if calibrated:
            controller.faces = {name: dict(world_id=WORLD,
                point_m=[.15 * v for v in FACE_NORMALS[name]],
                normal_world=list(FACE_NORMALS[name])) for name in FACES}
            controller._finish_calibration()
        self.refresh_rig(controller)
        return controller

    def refresh_rig(self, controller, stamp=101.02):
        radius = controller.plan["radii_m"][0]
        pose = dict(kind="pose", serial=B_SERIAL, epoch=0, valid=True,
            source_world_id=WORLD, arrival_lsl=stamp - .005, optical_age_seconds=.005,
            position_m=[0., radius, -controller.probe_offset_m[2]],
            quaternion_wxyz=[1., 0., 0., 0.])
        state = dict(world_id=WORLD, phase="both_pose_streaming_unvalidated",
            updated_unix=time.time(), updated_lsl=stamp,
            trackers={B_SERIAL: dict(last_pose=pose)}, errors=[])
        write_json(controller.rig / "status.json", state)
        controller.rig_status = state
        controller.last_probe = pose
        controller.probe_seen_unix = time.time()
        return pose

    def begin(self, controller):
        controller.playback_active = controller.playback_seen = True
        controller.capture_ready = True
        controller.last_audio_unix = time.time()
        controller._start_segment()
        append_events(controller.capture_folder,
            dict(kind="playback_started", arrival_lsl=100.9),
            dict(kind="operator_marker", source_world_id=WORLD, world_id=WORLD,
                 label="sphere-start:" + controller.segments[-1]["id"], arrival_lsl=101.))

    def audio(self, controller, *, logical=0, stamp=101.02, point_x=0.,
              event_changes=None, pose_changes=None, clipped=False, truncate=False):
        pose = self.refresh_rig(controller, stamp)
        pose = copy.deepcopy(pose)
        pose["position_m"][0] = point_x
        if pose_changes:
            pose.update(pose_changes)
        offset = (controller.capture_folder / "audio.pcm").stat().st_size
        frames = 480
        raw = (b"\xff\xff\x7f" if clipped else b"\x64\x00\x00") * (frames * 4)
        with (controller.capture_folder / "audio.pcm").open("ab") as output:
            output.write(raw[:len(raw) // 2] if truncate else raw)
        event = dict(kind="audio_block", sample_start_i64=logical, frames=frames,
            pcm_byte_offset=offset, device_position_u64=logical, device_qpc_100ns_u64=1000000000,
            arrival_lsl=stamp, lsl_first_frame=stamp - frames / 48000,
            timestamp_basis="wasapi_first_frame_qpc_mapped_to_lsl",
            flags=0, discontinuity=False, silent=False, timestamp_error=False,
            device_gap_frames=0, epoch=0, rate=48000, channels=4, container_bits=24, valid_bits=24)
        if event_changes:
            event.update(event_changes)
        append_events(controller.capture_folder, pose, event)
        return event, raw

    def count(self, controller):
        return controller.navigator.snapshot()["recording_bin_count"]

    def test_real_capture_fields_are_accepted_and_sample_index_is_preserved(self):
        controller = self.make_live()
        self.begin(controller)
        event, _ = self.audio(controller, logical=960)
        controller._poll_capture()
        self.assertEqual(self.count(controller), 1)
        points = [json.loads(line) for line in (controller.workspace / "scan-points.jsonl").read_text().splitlines()]
        self.assertEqual(points[0]["audio_sample_start_i64"], event["sample_start_i64"])
        self.assertFalse(points[0]["synthetic"])
        self.assertEqual(points[0]["basis"], "geometry_only")
        self.launch.assert_not_called()

    def test_invalid_audio_flags_format_and_gap_never_count(self):
        variants = ({"silent": True}, {"discontinuity": True}, {"timestamp_error": True},
            {"device_gap_frames": 7}, {"flags": 4}, {"valid_bits": 16},
            {"rate": 44100}, {"channels": None}, {"container_bits": 16},
            {"sample_start_i64": None}, {"frames": 0})
        for changes in variants:
            with self.subTest(changes=changes):
                controller = self.make_live()
                self.begin(controller)
                self.audio(controller, event_changes=changes)
                controller._poll_capture()
                self.assertEqual(self.count(controller), 0)

    def test_clipped_audio_is_not_accepted(self):
        controller = self.make_live()
        self.begin(controller)
        self.audio(controller, clipped=True)
        controller._poll_capture()
        self.assertEqual(self.count(controller), 0)
        self.assertEqual(controller.capture_counts["clipped_blocks"], 1)

    def test_pcm_flush_lag_keeps_block_pending_and_counts_only_once(self):
        controller = self.make_live()
        self.begin(controller)
        _, raw = self.audio(controller, truncate=True)
        controller._poll_capture()
        self.assertEqual(self.count(controller), 0)
        self.assertTrue(controller.capture_pending)
        with (controller.capture_folder / "audio.pcm").open("ab") as output:
            output.write(raw[len(raw) // 2:])
        controller._poll_capture()
        self.assertEqual(self.count(controller), 1)
        self.assertEqual(controller.capture_counts["audio_blocks"], 1)
        controller._poll_capture()
        self.assertEqual(self.count(controller), 1)

    def test_duplicate_and_discontinuous_logical_samples_do_not_add_positions(self):
        controller = self.make_live()
        self.begin(controller)
        self.audio(controller)
        controller._poll_capture()
        self.audio(controller, logical=0, stamp=101.03, point_x=.04)
        controller._poll_capture()
        self.audio(controller, logical=960, stamp=101.04, point_x=.08)
        controller._poll_capture()
        self.assertEqual(self.count(controller), 1)
        self.assertGreaterEqual(controller.capture_counts["rejected_blocks"], 2)

    def test_paused_and_no_playback_audio_do_not_add_positions(self):
        for paused in (True, False):
            with self.subTest(paused=paused):
                controller = self.make_live()
                self.begin(controller)
                if paused:
                    controller.action({"action": "pause"})
                else:
                    append_events(controller.capture_folder, dict(kind="playback_finished", arrival_lsl=101.))
                self.audio(controller)
                controller._poll_capture()
                self.assertEqual(self.count(controller), 0)

    def test_preparing_without_audio_cannot_become_scanning(self):
        controller = self.make_live()
        controller.action({"action": "start"})
        append_events(controller.capture_folder, dict(kind="playback_started", arrival_lsl=101.))
        controller._poll_capture()
        self.assertEqual(controller.phase, "preparing")
        self.assertEqual(self.count(controller), 0)
        self.assertEqual(controller.segments, [])

    def test_audio_timeout_pauses_even_when_pose_is_fresh(self):
        controller = self.make_live()
        self.begin(controller)
        self.audio(controller)
        controller._poll_capture()
        controller.last_audio_unix = time.time() - 3.
        self.refresh_rig(controller)
        controller._poll_capture()
        self.assertEqual(controller.phase, "paused")
        self.assertEqual(self.count(controller), 1)

    def test_bad_capture_pose_world_and_stale_optical_evidence_reject_audio(self):
        variants = ({"source_world_id": "other-world"}, {"optical_age_seconds": .3},
                    {"arrival_lsl": 100.5}, {"valid": False})
        for changes in variants:
            with self.subTest(changes=changes):
                controller = self.make_live()
                self.begin(controller)
                self.audio(controller, pose_changes=changes)
                controller._poll_capture()
                self.assertEqual(self.count(controller), 0)

    def test_changed_rig_world_pauses_and_hides_recommendation(self):
        controller = self.make_live()
        self.begin(controller)
        state = copy.deepcopy(controller.rig_status)
        state["world_id"] = "other-world"
        write_json(controller.rig / "status.json", state)
        controller.poll()
        snapshot = controller.snapshot()
        self.assertEqual(controller.phase, "paused")
        self.assertFalse(snapshot["source"]["connected"])
        self.assertIsNone(snapshot["navigation"]["recommendation"])

    def test_stale_rig_cannot_start(self):
        controller = self.make_live()
        controller.rig_status["updated_unix"] = time.time() - 5.
        with self.assertRaises(ValueError):
            controller.action({"action": "start"})
        self.assertEqual(controller.phase, "ready")
        self.launch.assert_not_called()

    def static_contacts(self, controller, *, unstable=False):
        events = [dict(kind="pose", serial=A_SERIAL, arrival_lsl=100. + i * .01,
            epoch=0, valid=True, optical_age_seconds=.005, position_m=[1., 2., 3.],
            quaternion_wxyz=[1., 0., 0., 0.]) for i in range(151)]
        if unstable:
            events[75]["position_m"][0] += .03
        controller.recent_a.extend(events)
        controller.rig_status.update(updated_lsl=101.51, updated_unix=time.time())

    def test_face_capture_uses_strict_static_contact_and_rejects_movement(self):
        controller = self.make_live(capture=False, calibrated=False)
        self.static_contacts(controller)
        controller.action(dict(action="capture_face", face="top"))
        face = controller.faces["top"]
        self.assertEqual(face["static_sample"]["count"], 151)
        self.assertAlmostEqual(face["point_m"][2], 3.02)
        other = self.make_live(capture=False, calibrated=False)
        self.static_contacts(other, unstable=True)
        with self.assertRaises(ValueError):
            other.action(dict(action="capture_face", face="top"))
        self.assertEqual(other.faces, {})

    def test_face_capture_rejects_changed_world_despite_cached_static_samples(self):
        controller = self.make_live(capture=False, calibrated=False)
        self.static_contacts(controller)
        controller.rig_status["world_id"] = "other-world"
        write_json(controller.rig / "status.json", controller.rig_status)
        with self.assertRaises(ValueError):
            controller.action(dict(action="capture_face", face="top"))
        self.assertEqual(controller.faces, {})

    def test_restart_restores_active_capture_and_points_without_new_hardware_owner(self):
        controller = self.make_live()
        self.begin(controller)
        self.audio(controller)
        controller._poll_capture()
        controller._save()
        restored = SphereScan(controller.workspace, rig=controller.rig)
        self.assertEqual(restored.capture_folder, controller.capture_folder)
        self.assertEqual(restored.phase, "paused")
        self.assertEqual(restored.segments[-1]["status"], "interrupted")
        self.assertEqual(self.count(restored), 1)
        restored._poll_capture()
        self.assertEqual(self.count(restored), 1)
        self.launch.assert_not_called()

    def test_restart_rejects_world_mismatch(self):
        controller = self.make_live()
        controller._save()
        write_json(controller.rig / "rig.json", dict(world_id="another-rig-world"))
        with self.assertRaises(ValueError):
            SphereScan(controller.workspace, rig=controller.rig)

    def test_restart_repairs_only_partial_tail_and_preserves_next_new_point(self):
        controller = self.make_live()
        self.begin(controller)
        self.audio(controller)
        controller._poll_capture()
        controller._save()
        path = controller.workspace / "scan-points.jsonl"
        with path.open("ab") as output:
            output.write(b'{"position_local_m":[')
        restored = SphereScan(controller.workspace, rig=controller.rig)
        self.assertEqual(self.count(restored), 1)
        self.assertTrue(list(controller.workspace.glob("scan-points.partial-*.bin")))
        self.refresh_rig(restored)
        restored._start_segment()
        restored._record_point([.05, restored.plan["radii_m"][0], 0.], stamp=102.)
        restored._save()
        again = SphereScan(controller.workspace, rig=controller.rig)
        self.assertEqual(self.count(again), 2)
        self.assertEqual(len([json.loads(line) for line in path.read_text().splitlines()]), 2)

    def test_restart_rejects_corrupt_complete_journal_line(self):
        controller = self.make_live()
        controller._save()
        path = controller.workspace / "scan-points.jsonl"
        path.write_bytes(b'{"broken":}\n')
        with self.assertRaises(ValueError):
            SphereScan(controller.workspace, rig=controller.rig)
        self.assertEqual(path.read_bytes(), b'{"broken":}\n')

    def test_attach_capture_rejects_another_world(self):
        controller = self.make_live(capture=False)
        folder = self.make_capture(controller.workspace / "other-capture")
        session = json.loads((folder / "session.json").read_text())
        session["config"]["world_id"] = "other-world"
        write_json(folder / "session.json", session)
        with self.assertRaises(ValueError):
            controller._attach_capture(folder)

    def test_finish_requests_writer_drain_and_stops_navigation(self):
        controller = self.make_live()
        self.begin(controller)
        controller.action(dict(action="finish"))
        request = json.loads((controller.capture_folder / "FINISH.json").read_text())
        self.assertEqual(request["kind"], "operator_finish")
        self.assertEqual(controller.phase, "finished")
        self.audio(controller)
        controller._poll_capture()
        self.assertEqual(self.count(controller), 0)

    def test_finish_during_child_reference_preparation_eventually_requests_drain(self):
        controller = self.make_live(capture=False)
        controller.capture_folder = controller.workspace / "pending-capture"
        controller.capture_owned = True
        controller.capture_process = Mock()
        controller.capture_process.poll.return_value = None
        controller.phase = "preparing"
        controller.action(dict(action="finish"))
        self.assertEqual(controller.phase, "finished")
        self.make_capture(controller.capture_folder)
        controller._poll_capture()
        self.assertTrue((controller.capture_folder / "FINISH.json").exists())

    def test_http_origin_and_host_checks_protect_mutating_actions(self):
        controller = SphereScan(self.root / "http-demo", demo=True)
        # Exercise the actual HTTP handler/parser using an in-memory transport;
        # this also works where sandbox policy disallows loopback sockets.
        with patch("lsac_capture.sphere_scan.ThreadingHTTPServer",
                   side_effect=lambda address, handler: SimpleNamespace(
                       server_port=8769, RequestHandlerClass=handler)):
            server = make_server(controller, port=8769)

        class Transport:
            def __init__(self, request):
                self.input = io.BytesIO(request)
                self.output = bytearray()

            def makefile(self, *args):
                return self.input

            def sendall(self, data):
                self.output.extend(data)

        def request(method, path, host, origin=None, body=""):
            headers = [f"{method} {path} HTTP/1.0", f"Host: {host}"]
            if origin is not None:
                headers.append("Origin: " + origin)
            headers.extend(["Content-Type: application/json", f"Content-Length: {len(body)}"])
            transport = Transport(("\r\n".join(headers) + "\r\n\r\n" + body).encode())
            server.RequestHandlerClass(transport, ("127.0.0.1", 12345), server)
            return int(bytes(transport.output).split(b" ", 2)[1])

        host = "127.0.0.1:8769"
        body = json.dumps(dict(action="demo_calibration"))
        self.assertEqual(request("POST", "/api/action", host, "https://remote.invalid", body), 403)
        self.assertIsNone(controller.model)
        self.assertEqual(request("POST", "/api/action", host, "http://" + host, body), 200)
        self.assertEqual(request("GET", "/api/state", "remote.invalid"), 403)
        self.assertIsNotNone(controller.model)
        self.launch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
