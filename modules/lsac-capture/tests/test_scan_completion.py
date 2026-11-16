import unittest
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from lsac_capture.rig_capture import playback_outcome


class ScanCompletionTests(unittest.TestCase):
    def test_operator_can_preserve_partial_scan_without_hiding_failures(self):
        partial=dict(status='cancelled',start_call_before_qpc_i64=123,queued_frames=1000)
        self.assertEqual(playback_outcome(partial,True),'operator_finished')
        self.assertEqual(playback_outcome(partial,False),'incomplete')
        self.assertEqual(playback_outcome(dict(status='failed'),True),'incomplete')

    def test_finishing_before_playback_is_explicitly_background_only(self):
        self.assertEqual(playback_outcome({},True),'operator_finished_before_playback')
        self.assertEqual(playback_outcome(dict(status='cancelled'),True),'operator_finished_before_playback')
        self.assertEqual(playback_outcome({},False),'incomplete')

    def test_bounded_playback_completion_remains_completed(self):
        self.assertEqual(playback_outcome(dict(status='completed_not_acoustically_verified'),False),'completed')

    def test_frontend_finish_requests_preservation_instead_of_immediate_abort(self):
        from lsac_capture.coverage import Observer
        from test_coverage import calibration
        root=Path(__file__).resolve().parents[3]/'build'/'lsac'/'test-tmp'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            p=Path(tmp)
            (p/'session.json').write_text(json.dumps(dict(config=dict(world_id='test-world'))),encoding='utf-8')
            (p/'live-status.json').write_text(json.dumps(dict(phase='ready',failures=[])),encoding='utf-8')
            observer=Observer(p,calibration())
            observer.request_stop()
            self.assertFalse((p/'STOP').exists())
            self.assertEqual(json.loads((p/'FINISH.json').read_text())['kind'],'operator_finish')
            self.assertFalse(observer.snapshot()['can_stop'])

    def test_reference_preparation_fails_before_any_capture_session_is_opened(self):
        from lsac_capture.rig_capture import run
        root=Path(__file__).resolve().parents[3]/'build'/'lsac'/'test-tmp';root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as tmp:
            p=Path(tmp);(p/'reference.json').write_text(json.dumps(dict(seconds=600)),encoding='utf-8')
            with patch('lsac_capture.playback.prepare_playback_reference',side_effect=ValueError('preparation failed')), \
                 patch('lsac_capture.session.Session') as session,patch('lsac_capture.rig_capture.live_state') as live:
                with self.assertRaisesRegex(ValueError,'preparation failed'):
                    run('unused-rig','unused-output',seconds=1200,purpose='scan-preview',playback_plan={},
                        reference_folder=p,confirm_playback=True)
                session.assert_not_called();live.assert_not_called()


if __name__=='__main__':unittest.main()
