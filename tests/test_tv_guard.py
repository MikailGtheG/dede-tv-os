#!/usr/bin/env python3
"""Regression tests for dede-tv-guard (remote-control state C)."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import socket
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
LIB = ROOT / "build/config/includes.chroot/usr/local/lib/dede-tv"
os.environ["DEDE_TV_LIB"] = str(LIB)
sys.path.insert(0, str(LIB))
import dede_kodi_rpc  # noqa: E402

loader = importlib.machinery.SourceFileLoader(
    "dede_tv_guard", str(ROOT / "build/config/includes.chroot/usr/local/libexec/dede-tv-guard"))
spec = importlib.util.spec_from_loader(loader.name, loader)
guard_module = importlib.util.module_from_spec(spec)
loader.exec_module(guard_module)


class FakeKodi:
    def __init__(self):
        self.window = guard_module.WINDOW_FULLSCREEN
        self.playing = False
        self.position = 0
        self.caching = False
        self.calls = []

    def __call__(self, method, params=None, timeout=5.0):
        self.calls.append((method, params))
        if method == "GUI.GetProperties":
            return {"currentwindow": {"id": self.window}}
        if method == "Player.GetActivePlayers":
            return [{"playerid": 1, "type": "video"}] if self.playing else []
        if method == "Player.GetProperties":
            ms = self.position
            return {"time": {"hours": 0, "minutes": ms // 60000,
                             "seconds": ms // 1000 % 60, "milliseconds": ms % 1000}}
        if method == "XBMC.GetInfoBooleans":
            return {"Player.Caching": self.caching}
        if method in ("Player.Open", "Input.Back", "GUI.SetFullscreen"):
            return "OK"
        raise AssertionError(method)

    def opened(self):
        return [p["item"]["channelid"] for m, p in self.calls if m == "Player.Open"]

    def count(self, method):
        return sum(1 for m, _ in self.calls if m == method)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def on_play(item):
    return {"jsonrpc": "2.0", "method": "Player.OnPlay",
            "params": {"data": {"item": item, "player": {"playerid": 1}}, "sender": "xbmc"}}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.kodi = FakeKodi()
        self.clock = Clock()
        self.guard = guard_module.Guard(call=self.kodi, clock=self.clock)
        self.logs = []
        guard_module.log = self.logs.append

    def run_for(self, seconds, step=2):
        end = self.clock.now + seconds
        while self.clock.now < end:
            self.guard.tick()
            self.clock.now += step

    def test_dead_channel_retried_with_bounded_backoff(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 90}))
        self.kodi.window = guard_module.WINDOW_HOME
        self.run_for(400, step=1)
        opens = [t for t in self.kodi.opened()]
        self.assertTrue(opens and set(opens) == {90})
        # 5, 10, 20, 40, 60, 60, ... seconds apart => about 9 retries in 400 s.
        self.assertTrue(6 <= len(opens) <= 10, len(opens))
        self.assertTrue(any("external stream unavailable" in line for line in self.logs))

    def test_no_retry_before_first_backoff(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 3}))
        self.kodi.window = guard_module.WINDOW_HOME
        self.run_for(4, step=1)
        self.assertEqual(self.kodi.opened(), [])

    def test_kodi_error_dialog_closed_and_nothing_else_done(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 90}))
        self.kodi.window = guard_module.WINDOW_OK_DIALOG
        self.guard.tick()
        self.assertEqual([m for m, _ in self.kodi.calls], ["GUI.GetProperties", "Input.Back"])

    def test_back_is_never_sent_without_the_error_dialog(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 90}))
        for window in (guard_module.WINDOW_HOME, guard_module.WINDOW_FULLSCREEN,
                       guard_module.WINDOW_CHANNEL_LIST):
            self.kodi.window = window
            self.run_for(30, step=1)
        self.assertEqual(self.kodi.count("Input.Back"), 0)

    def test_open_channel_list_pauses_retries(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 90}))
        self.kodi.window = guard_module.WINDOW_CHANNEL_LIST
        self.run_for(120, step=1)
        self.assertEqual(self.kodi.opened(), [])

    def test_viewer_choice_replaces_target_and_resets_backoff(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 90}))
        self.kodi.window = guard_module.WINDOW_HOME
        self.run_for(80, step=1)
        self.guard.on_notification(on_play({"type": "channel", "id": 6}))
        before = len(self.kodi.opened())
        self.run_for(6, step=1)
        self.assertEqual(self.kodi.opened()[before:], [6])

    def test_own_retry_notification_does_not_reset_backoff(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 90}))
        self.kodi.window = guard_module.WINDOW_HOME
        self.run_for(6, step=1)
        self.guard.on_notification(on_play({"type": "channel", "id": 90}))
        self.assertEqual(self.guard.attempts, 1)

    def test_recovery_returns_to_fullscreen_and_resets(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 4}))
        self.kodi.window = guard_module.WINDOW_HOME
        self.run_for(30, step=1)
        self.kodi.playing = True
        for _ in range(3):
            self.kodi.position += 2000
            self.guard.tick()
            self.clock.now += 2
        self.assertGreaterEqual(self.kodi.count("GUI.SetFullscreen"), 1)
        self.assertFalse(self.guard.failing)
        self.assertEqual(self.guard.attempts, 0)

    def test_frozen_position_restarts_channel(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 2}))
        self.kodi.playing = True
        self.run_for(28, step=2)
        self.assertEqual(self.kodi.opened(), [])
        self.run_for(10, step=2)
        self.assertEqual(self.kodi.opened(), [2])

    def test_endless_buffering_restarts_even_if_programme_clock_advances(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 2}))
        self.kodi.playing = True
        self.kodi.caching = True
        for _ in range(20):
            self.kodi.position += 2000
            self.guard.tick()
            self.clock.now += 2
        self.assertEqual(self.kodi.opened(), [2])

    def test_smooth_playback_never_retried(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 2}))
        self.kodi.playing = True
        for _ in range(200):
            self.kodi.position += 2000
            self.guard.tick()
            self.clock.now += 2
        self.assertEqual(self.kodi.opened(), [])

    def test_non_channel_playback_is_not_retried(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 2}))
        self.guard.on_notification(on_play({"type": "movie", "id": 9}))
        self.kodi.window = guard_module.WINDOW_HOME
        self.run_for(120, step=1)
        self.assertEqual(self.kodi.opened(), [])

    def test_natural_end_continues_shortly_without_failure(self):
        # A local 24/7 channel's film ended: open the channel again after a
        # short pause (Kodi refuses an immediate re-open), no backoff, no
        # "unavailable" message.
        self.guard.on_notification(on_play({"type": "channel", "id": 5}))
        self.guard.on_notification({"jsonrpc": "2.0", "method": "Player.OnStop",
                                    "params": {"data": {"end": True, "item": {"type": "channel", "id": 5}}}})
        self.kodi.window = guard_module.WINDOW_HOME
        self.guard.tick()
        self.assertEqual(self.kodi.opened(), [])
        self.run_for(guard_module.CONTINUE_DELAY_S + 1, step=1)
        self.assertEqual(self.kodi.opened(), [5])
        self.assertFalse(any("unavailable" in line for line in self.logs))
        self.assertTrue(any("programme item ended, continuing" in line for line in self.logs))

    def test_user_stop_or_failure_is_not_treated_as_programme_end(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 5}))
        self.guard.on_notification({"method": "Player.OnStop", "params": {"data": {"end": False}}})
        self.kodi.window = guard_module.WINDOW_HOME
        self.guard.tick()
        self.assertEqual(self.kodi.opened(), [])

    def test_new_choice_cancels_pending_continuation(self):
        self.guard.on_notification(on_play({"type": "channel", "id": 5}))
        self.guard.on_notification({"method": "Player.OnStop", "params": {"data": {"end": True}}})
        self.guard.on_notification(on_play({"type": "channel", "id": 2}))
        self.kodi.playing = True
        self.guard.tick()
        self.assertEqual(self.kodi.opened(), [])

    def test_malformed_notifications_ignored(self):
        for document in ({"method": "Player.OnPlay"}, {"method": "Player.OnPlay", "params": None},
                         on_play({"type": "channel", "id": "90"}), {"method": "GUI.OnScreensaverActivated"}):
            self.guard.on_notification(document)
        self.assertIsNone(self.guard.target)


class PersistentConnectionTests(unittest.TestCase):
    def pair(self):
        kodi, client = socket.socketpair()
        return kodi, dede_kodi_rpc.Connection(sock=client)

    def test_replies_matched_and_notifications_queued_on_one_socket(self):
        kodi, connection = self.pair()
        # Kodi sends a notification, a stale reply and a fragment first.
        kodi.sendall(b'{"jsonrpc":"2.0","method":"Player.OnPlay","params":{"data":{"item":{"type":"channel","id":5}}}}'
                     b'{"jsonrpc":"2.0","id":1,"result":"stale"}'
                     b'{"jsonrpc":"2.0","id":50001,"res')
        kodi.sendall(b'ult":{"currentwindow":{"id":12005}}}')
        result = connection.call("GUI.GetProperties", {"properties": ["currentwindow"]})
        self.assertEqual(result, {"currentwindow": {"id": 12005}})
        request = kodi.recv(4096)
        self.assertIn(b'"id":50001', request)
        self.assertEqual([d["method"] for d in connection.notifications()], ["Player.OnPlay"])
        kodi.sendall(b'{"jsonrpc":"2.0","method":"Player.OnStop","params":{}}')
        self.assertEqual([d["method"] for d in connection.notifications(wait=1)], ["Player.OnStop"])
        kodi.close()
        connection.close()

    def test_error_reply_and_timeout(self):
        kodi, connection = self.pair()
        kodi.sendall(b'{"jsonrpc":"2.0","id":50001,"error":{"code":-32601}}')
        with self.assertRaisesRegex(dede_kodi_rpc.RpcError, "-32601"):
            connection.call("Nope")
        with self.assertRaisesRegex(dede_kodi_rpc.RpcError, "no bounded reply"):
            connection.call("Slow", timeout=0.2)
        kodi.close()
        with self.assertRaises(ConnectionError):
            connection.notifications(wait=1)
        connection.close()

    def test_guard_uses_a_single_connection(self):
        source = (ROOT / "build/config/includes.chroot/usr/local/libexec/dede-tv-guard").read_text()
        run = source[source.index("def run():"):]
        self.assertIn("guard.call = connection.call", run)
        self.assertNotIn("rpc(", run)


if __name__ == "__main__":
    unittest.main()
