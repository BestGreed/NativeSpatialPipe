import ctypes
from pathlib import Path
import unittest
from unittest.mock import patch

from lsac_capture.receiver import Caps,choose,packet,pair,PAIR_PREFIX


class ReceiverTests(unittest.TestCase):
    def test_caps_layout_matches_windows_sdk(self):
        self.assertEqual(ctypes.sizeof(Caps),64)

    def test_selects_only_exact_receiver_not_hmd(self):
        rows=[dict(vid="0x28de",pid="0x2300",serial="wanted",interface_path="hmd"),
              dict(vid="0x28de",pid="0x2102",serial="wanted",interface_path="receiver"),
              dict(vid="0x28de",pid="0x2102",serial="other",interface_path="other")]
        self.assertEqual(choose(rows,"wanted")["interface_path"],"receiver")
        with self.assertRaises(ValueError): choose(rows,"want")

    def test_ambiguous_serial_refused(self):
        row=dict(vid="0x28de",pid="0x2102",serial="same")
        with self.assertRaises(ValueError): choose([row,row],"same")

    def test_exact_feature_prefix_padded_not_global_iteration(self):
        report=packet(64)
        self.assertEqual(report[:6],bytes.fromhex("ff ad 03 01 10 27"))
        self.assertEqual(report[6:],bytes(58))
        self.assertEqual(len(report),64)
        for n in (0,5,4097):
            with self.assertRaises(ValueError): packet(n)

    def test_unconfirmed_pair_has_no_io(self):
        with patch("lsac_capture.receiver.api") as native, patch("lsac_capture.receiver.subprocess.Popen") as child:
            with self.assertRaises(ValueError): pair("missing-plan.json","must-not-create.json")
            native.assert_not_called(); child.assert_not_called()
        self.assertFalse(Path("must-not-create.json").exists())


if __name__=="__main__": unittest.main()
