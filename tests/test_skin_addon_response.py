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


class JsonRpcResponseTests(unittest.TestCase):
    def test_kodi_write_methods_accept_scalar_ok_result(self):
        class FakeSocket:
            def __init__(self):
                self.sent = b""
                self.received = False

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def settimeout(self, timeout):
                pass

            def sendall(self, data):
                self.sent += data

            def recv(self, count):
                if self.received:
                    return b""
                self.received = True
                return b'{"jsonrpc":"2.0","id":24011,"result":"OK"}'

        connection = FakeSocket()
        with patch.object(module.socket, "create_connection", return_value=connection):
            self.assertEqual(
                module.rpc("Addons.SetAddonEnabled",
                           {"addonid": "skin.dede-tv", "enabled": True}),
                "OK",
            )
        self.assertIn(b"Addons.SetAddonEnabled", connection.sent)

    def test_malformed_missing_result_is_rejected(self):
        class FakeSocket:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def settimeout(self, timeout):
                pass
            def sendall(self, data):
                pass
            def recv(self, count):
                return b'{"jsonrpc":"2.0","id":24011}'

        with patch.object(module.socket, "create_connection", return_value=FakeSocket()):
            with self.assertRaisesRegex(RuntimeError, "missing result"):
                module.rpc("Settings.SetSettingValue")


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
