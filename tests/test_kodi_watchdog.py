#!/usr/bin/env python3
"""Regression tests for the hung-Kodi watchdog decision logic."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest

import json
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


class FakeKodi:
    """Socketpair-backed Kodi: answers pings, floods notifications first."""

    def __init__(self):
        self.kodi, client = socket.socketpair()
        import dede_kodi_rpc
        self.connection = dede_kodi_rpc.Connection(sock=client)
        self.connects = 0

    def connect(self):
        self.connects += 1
        return self.connection

    def answer(self, flood=0):
        request = json.loads(self.kodi.recv(4096))
        note = b'{"jsonrpc":"2.0","method":"Player.OnAVChange","params":{"data":{"x":"' + b"y" * 400 + b'"}}}'
        self.kodi.sendall(note * flood + ('{"jsonrpc":"2.0","id":%d,"result":"pong"}' % request["id"]).encode())


class PingTests(unittest.TestCase):
    def test_many_pings_over_one_connection_despite_notification_floods(self):
        kodi = FakeKodi()
        pinger = wd.Pinger(connect=kodi.connect)
        for flood in (0, 400, 50):
            responder = threading.Thread(target=kodi.answer, args=(flood,), daemon=True)
            responder.start()
            self.assertTrue(pinger(timeout=5))
            responder.join(5)
        self.assertEqual(kodi.connects, 1)

    def test_lost_connection_is_unhealthy_then_reconnects(self):
        kodi = FakeKodi()
        pinger = wd.Pinger(connect=kodi.connect)
        kodi.kodi.close()  # Kodi went away
        self.assertFalse(pinger(timeout=1))
        self.assertIsNone(pinger.connection)
        replacement = FakeKodi()
        pinger.connect = replacement.connect
        responder = threading.Thread(target=replacement.answer, daemon=True)
        responder.start()
        self.assertTrue(pinger(timeout=5))
        responder.join(5)

    def test_unreachable_kodi_is_unhealthy(self):
        def refuse():
            raise ConnectionRefusedError
        self.assertFalse(wd.Pinger(connect=refuse)(timeout=1))

    def test_watchdog_is_a_long_running_service(self):
        units = ROOT / "build/config/includes.chroot/etc/systemd/system"
        self.assertFalse((units / "dede-kodi-watchdog.timer").exists())
        service = (units / "dede-kodi-watchdog.service").read_text()
        self.assertIn("Type=simple", service)
        self.assertIn("Restart=always", service)


if __name__ == "__main__":
    unittest.main()
