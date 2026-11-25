#!/usr/bin/env python3
"""Kodi setup service: setup code and address for the TV's setup card."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest

ROOT = Path(__file__).resolve().parent.parent
SERVICE = ROOT / "build/config/includes.chroot/usr/share/kodi/addons/service.dede-setup/service.py"
sys.modules.setdefault("xbmc", types.ModuleType("xbmc"))
sys.modules.setdefault("xbmcgui", types.ModuleType("xbmcgui"))
loader = importlib.machinery.SourceFileLoader("dede_setup_service", str(SERVICE))
spec = importlib.util.spec_from_loader(loader.name, loader)
service = importlib.util.module_from_spec(spec)
loader.exec_module(service)


class SetupServiceTests(unittest.TestCase):
    def code_file(self, content):
        tmp = tempfile.NamedTemporaryFile("w", delete=False)
        tmp.write(content)
        tmp.close()
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_valid_code_is_grouped(self):
        self.assertEqual(service.read_code(self.code_file("28155006")), "2815 5006")
        self.assertEqual(service.read_code(self.code_file("28155006\n")), "2815 5006")

    def test_missing_or_malformed_code_hides_the_card(self):
        self.assertEqual(service.read_code("/nonexistent/setup-code"), "")
        for bad in ("", "1234567", "123456789", "abcdefgh", "1234 5678", "[B]x[/B]"):
            with self.subTest(bad=bad):
                self.assertEqual(service.read_code(self.code_file(bad)), "")

    def test_address(self):
        self.assertEqual(service.address("192.168.1.20"), "http://192.168.1.20:8080")
        self.assertEqual(service.address("fd00::5"), "http://[fd00::5]:8080")
        for none in ("", "127.0.0.1", "0.0.0.0", None):
            self.assertEqual(service.address(none), "")


if __name__ == "__main__":
    unittest.main()
