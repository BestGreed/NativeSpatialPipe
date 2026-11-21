"""audiodg build-profile table contract: selection, fail-closed, config propagation."""
import importlib.util
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location('audiodg_builds', ROOT / 'tools/backend/audiodg_builds.py')
builds = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builds)

REFERENCE_9444 = '3dd24fe6faf0b56f99d5b00f24687b5f3b3e76e15b219e23f0963114a0dce9a5'
BUILD_9974 = '017628fc0ace806163950bb3019709ca3820531d7f19c1103429e09b4c54f6ca'
BUILD_10068 = 'e55b5365056273c133954ca7c44b8d4f1c3139a4b3fa766a94ffe58c2621a8b3'


class FakeConfig:
    """Stand-in for the ctypes HookConfig fields filled by apply_profile."""

    def __init__(self):
        self.target_rva = 0
        self.target_size = 0
        self.vt1_rva = 0
        self.vt2_rva = 0
        self.prologue = [0] * 16
        self.profile_sha256 = [0] * 32


class BuildTableTests(unittest.TestCase):
    def test_each_supported_build_is_selected_by_full_file_sha256(self):
        expected = {
            REFERENCE_9444: ('win-26100-9444', 0x31930, 0x9f4f8, 0xa2d58),
            BUILD_9974: ('win-26100-9974', 0x32c40, 0x9e4e8, 0xa1be0),
            BUILD_10068: ('win-26100-10068', 0x32c40, 0x9e4e8, 0xa1c20),
        }
        for digest, (ident, target_rva, vt1_rva, vt2_rva) in expected.items():
            profile = builds.match_build(digest)
            self.assertIsNotNone(profile, digest)
            self.assertEqual(profile['id'], ident)
            self.assertEqual(profile['targetRva'], target_rva)
            self.assertEqual(profile['targetSize'], 474)
            self.assertEqual(profile['vt1Rva'], vt1_rva)
            self.assertEqual(profile['vt2Rva'], vt2_rva)

    def test_unknown_build_is_rejected_with_actual_and_supported_list(self):
        actual = '0' * 64
        self.assertIsNone(builds.match_build(actual))
        message = builds.unsupported_build_message(actual)
        self.assertIn(actual, message)
        self.assertIn(REFERENCE_9444[:8], message)
        self.assertIn(BUILD_9974[:8], message)
        self.assertIn(BUILD_10068[:8], message)
        self.assertIn('Unsupported audiodg build', message)
        self.assertIn('contracts/audiodg-builds.json', message)
        with self.assertRaises(RuntimeError) as raised:
            builds.require_build(actual)
        self.assertIn(actual, str(raised.exception))

    def test_same_rva_builds_remain_distinct_entries(self):
        build_9974 = builds.match_build(BUILD_9974)
        build_10068 = builds.match_build(BUILD_10068)
        self.assertEqual(build_9974['targetRva'], build_10068['targetRva'])
        self.assertEqual(build_9974['vt1Rva'], build_10068['vt1Rva'])
        # The second capture vtable moved between the two servicing builds even
        # though the target RVA is unchanged.
        self.assertNotEqual(build_9974['vt2Rva'], build_10068['vt2Rva'])
        self.assertNotEqual(build_9974['functionSha256'], build_10068['functionSha256'])

    def test_apply_profile_fills_every_field_the_hook_reads(self):
        profile = builds.require_build(BUILD_9974)
        config = builds.apply_profile(FakeConfig(), profile)
        self.assertEqual(config.target_rva, profile['targetRva'])
        self.assertEqual(config.target_size, profile['targetSize'])
        self.assertEqual(config.vt1_rva, profile['vt1Rva'])
        self.assertEqual(config.vt2_rva, profile['vt2Rva'])
        self.assertEqual(bytes(config.prologue), profile['prologue'])
        self.assertEqual(bytes(config.profile_sha256), bytes.fromhex(profile['sha256']))

    def test_every_profile_declares_runtime_fields(self):
        profiles = builds.PROFILES
        self.assertGreaterEqual(len(profiles), 3)
        self.assertEqual(len({profile['id'] for profile in profiles}), len(profiles))
        self.assertEqual(len({profile['sha256'] for profile in profiles}), len(profiles))
        for profile in profiles:
            self.assertEqual(len(profile['sha256']), 64)
            self.assertTrue(all(ch in '0123456789abcdef' for ch in profile['sha256']))
            self.assertTrue(profile['label'])
            self.assertGreater(profile['targetRva'], 0)
            self.assertEqual(profile['targetSize'], 474)
            self.assertGreater(profile['vt1Rva'], 0)
            self.assertGreater(profile['vt2Rva'], 0)
            self.assertEqual(len(profile['prologue']), 16)
            self.assertEqual(len(profile['functionSha256']), 64)

    def test_generated_tables_match_contract(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/Generate-AudiodgBuilds.py'), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()