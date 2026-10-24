"""Preparation must remain runnable in an exported source/CI bundle."""
from pathlib import Path
import unittest


class RuntimeDependencies(unittest.TestCase):
    def test_default_endpoint_helper_is_shipped(self):
        root = Path(__file__).resolve().parents[2]
        self.assertTrue((root / 'tools/endpoint-probe/Get-DefaultRenderEndpoint.ps1').is_file(),
                        'Preparation needs the read-only default endpoint helper at runtime')


if __name__ == '__main__':
    unittest.main()
