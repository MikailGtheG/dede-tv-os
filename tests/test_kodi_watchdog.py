#!/usr/bin/env python3
"""Regression tests for the hung-Kodi watchdog decision logic."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest

import os
import socket
import threading

ROOT = Path(__file__).resolve().parent.parent
os.environ["DEDE_TV_LIB"] = str(ROOT / "build/config/includes.chroot/usr/local/lib/dede-tv")
SCRIPT = (Path(__file__).resolve().parent.parent
          / "build/config/includes.chroot/usr/local/libexec/dede-kodi-watchdog")
loader = importlib.machinery.SourceFileLoader("dede_kodi_watchdog", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
wd = importlib.util.module_from_spec(spec)
loader.exec_module(wd)


def run(checks, start_state=None):
    """checks: list of (uptime, healthy, now). Returns restart decisions."""
    state = start_state or {"failures": 0, "last_restart": -1e12}
    decisions = []
    for uptime, healthy, now in checks:
        state, restart = wd.decide(state, uptime, healthy, now)
        decisions.append(restart)
    return decisions, state


class WatchdogTests(unittest.TestCase):
    def test_three_consecutive_failures_restart_once(self):
        decisions, _ = run([(300, False, 1000), (330, False, 1030), (360, False, 1060)])
        self.assertEqual(decisions, [False, False, True])

    def test_single_success_resets_failures(self):
        decisions, _ = run([(300, False, 1000), (330, False, 1030), (360, True, 1060),
                            (390, False, 1090), (420, False, 1120)])
        self.assertEqual(decisions, [False] * 5)

    def test_grace_period_after_kodi_start(self):
        decisions, _ = run([(30, False, 1000), (60, False, 1030), (90, False, 1060), (110, False, 1080)])
        self.assertEqual(decisions, [False] * 4)

    def test_inactive_service_is_left_to_systemd(self):
        decisions, _ = run([(None, False, t) for t in (1000, 1030, 1060, 1090)])
        self.assertEqual(decisions, [False] * 4)

    def test_no_restart_loop(self):
        checks = [(300 + 30 * i, False, 1000 + 30 * i) for i in range(30)]  # 15 min hung
        decisions, _ = run(checks)
        # First restart after 3 failures, the next only after 10 minutes.
        self.assertEqual(decisions.count(True), 2)
        first, second = [i for i, d in enumerate(decisions) if d]
        self.assertGreaterEqual((second - first) * 30, wd.MIN_INTERVAL_S)

    def test_corrupt_state_file_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "state.json")
            for content in ("", "{", "[]", '{"failures":"x"}', "\x00" * 10):
                path.write_text(content)
                self.assertEqual(wd.load(str(path))["failures"], 0)
            wd.save({"failures": 2, "last_restart": 5.0}, str(path))
            self.assertEqual(wd.load(str(path)), {"failures": 2, "last_restart": 5.0})


class PingTests(unittest.TestCase):
    def test_pong_found_behind_a_flood_of_notifications(self):
        server = socket.socket()
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            server.bind(("127.0.0.1", 9090))
        except OSError:
            self.skipTest("port 9090 busy")
        server.listen(1)

        def kodi():
            conn, _ = server.accept()
            request = conn.recv(4096)
            request_id = __import__("json").loads(request)["id"]
            note = b'{"jsonrpc":"2.0","method":"Player.OnAVChange","params":{"data":{"x":"' + b"y" * 400 + b'"}}}'
            conn.sendall(note * 400)  # ~180 KiB before the reply
            conn.sendall(('{"jsonrpc":"2.0","id":%d,"result":"pong"}' % request_id).encode())
            conn.close()
        thread = threading.Thread(target=kodi, daemon=True)
        thread.start()
        try:
            self.assertTrue(wd.ping(timeout=5))
        finally:
            thread.join(5)
            server.close()

    def test_unreachable_kodi_is_unhealthy(self):
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        import dede_kodi_rpc
        original = dede_kodi_rpc.PORT
        dede_kodi_rpc.PORT = port
        try:
            self.assertFalse(wd.ping(timeout=1))
        finally:
            dede_kodi_rpc.PORT = original


if __name__ == "__main__":
    unittest.main()
