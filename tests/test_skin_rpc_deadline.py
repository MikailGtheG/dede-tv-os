#!/usr/bin/env python3
"""Offline regression tests for the Kodi skin activation JSON-RPC client."""
import importlib.machinery
import importlib.util
from pathlib import Path
import socket
import unittest
from unittest.mock import patch

SCRIPT = (Path(__file__).resolve().parents[1] /
          "build/config/includes.chroot/usr/local/libexec/dede-kodi-activate-skin")
loader = importlib.machinery.SourceFileLoader("skin_activation", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class FakeConnection:
    def __init__(self, *, stall=False):
        self.stall = stall
        self.timeouts = []
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, data):
        if not self.timeouts:
            raise AssertionError("sendall called without deadline")
        if self.stall:
            raise socket.timeout("simulated blocked send")
        self.sent = data

    def recv(self, size):
        return b'{"jsonrpc":"2.0","id":24011,"result":{"value":"skin.dede-tv"}}\\n'


class RpcDeadlineTests(unittest.TestCase):
    def test_send_has_timeout_and_response_is_parsed(self):
        connection = FakeConnection()
        with patch.object(module.socket, "create_connection", return_value=connection):
            result = module.rpc("Settings.GetSettingValue", timeout=1)
        self.assertEqual(result["value"], "skin.dede-tv")
        self.assertTrue(connection.timeouts)
        self.assertGreater(connection.timeouts[0], 0)
        self.assertLessEqual(connection.timeouts[0], 1)
        self.assertIn(b"Settings.GetSettingValue", connection.sent)

    def test_blocked_send_is_bounded(self):
        connection = FakeConnection(stall=True)
        with patch.object(module.socket, "create_connection", return_value=connection):
            with self.assertRaises(socket.timeout):
                module.rpc("Settings.GetSettingValue", timeout=1)
        self.assertTrue(connection.timeouts)


if __name__ == "__main__":
    unittest.main()
