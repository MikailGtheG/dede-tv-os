#!/usr/bin/env python3
"""Regression: malformed Kodi addon metadata must not crash skin activation."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

SCRIPT = (Path(__file__).resolve().parents[1] /
          "build/config/includes.chroot/usr/local/libexec/dede-kodi-activate-skin")
loader = importlib.machinery.SourceFileLoader("skin_addon_activation", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class AddonResponseTests(unittest.TestCase):
    def test_malformed_addon_metadata_is_retried_without_crash(self):
        for addon in (None, [], "invalid", {"enabled": "false"}, {"enabled": None}, {}):
            with self.subTest(addon=addon):
                with patch.object(module, "desired_skin", return_value="skin.dede-tv"), \
                     patch.object(module.Path, "is_file", return_value=True), \
                     patch.object(module, "rpc", return_value={"addon": addon}) as rpc, \
                     patch.object(module, "range", create=True, return_value=range(1)), \
                     patch.object(module.time, "sleep", return_value=None):
                    self.assertEqual(module.run(), 1)
                    rpc.assert_called_once()

    def test_valid_disabled_addon_attempts_enable(self):
        with patch.object(module, "desired_skin", return_value="skin.dede-tv"), \
             patch.object(module.Path, "is_file", return_value=True), \
             patch.object(module, "rpc", side_effect=[
                 {"addon": {"enabled": False}}, {}, {}, {"value": "skin.estuary"}
             ]) as rpc, \
             patch.object(module, "range", create=True, return_value=range(1)), \
             patch.object(module.time, "sleep", return_value=None):
            self.assertEqual(module.run(), 1)
            self.assertEqual(rpc.call_args_list[1].args[0], "Addons.SetAddonEnabled")


if __name__ == "__main__":
    unittest.main()
