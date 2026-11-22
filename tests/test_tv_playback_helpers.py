#!/usr/bin/env python3
"""Regression tests for IPTV provisioning and live-TV autoplay helpers."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
LIBEXEC = ROOT / "build/config/includes.chroot/usr/local/libexec"


def load(name):
    loader = importlib.machinery.SourceFileLoader(name.replace("-", "_"), str(LIBEXEC / name))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


provision = load("dede-iptv-provision")
autoplay = load("dede-tv-autoplay")


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def config(self, text, mode=0o640):
        path = self.dir / "iptv.conf"
        path.write_text(text, encoding="utf-8")
        path.chmod(mode)
        return path

    def read(self, text, **kwargs):
        return provision.read_config(self.config(text, **kwargs), require_root_owner=False)

    def test_valid_config_renders_kodi21_instance_settings(self):
        values = self.read("# Anbieter\nM3U_URL=http://10.0.2.2:8089/dede-test.m3u\n"
                           "EPG_URL=https://epg.example/x.xml?a=1&b=2\n")
        root = ET.fromstring(provision.render(values))
        settings = {s.get("id"): s.text for s in root.iter("setting")}
        self.assertEqual(root.get("version"), "2")
        self.assertEqual(settings["m3uPathType"], "1")
        self.assertEqual(settings["m3uUrl"], "http://10.0.2.2:8089/dede-test.m3u")
        # XML-escaped on disk, exact after parsing.
        self.assertEqual(settings["epgUrl"], "https://epg.example/x.xml?a=1&b=2")
        self.assertEqual(settings["kodi_addon_instance_enabled"], "true")
        self.assertEqual(settings["numberByOrder"], "false")

    def test_missing_config_is_not_an_error(self):
        self.assertIsNone(provision.read_config(self.dir / "absent.conf", require_root_owner=False))

    def test_hostile_or_malformed_configs_rejected(self):
        cases = {
            "file scheme": "M3U_URL=file:///etc/shadow\n",
            "smb scheme": "M3U_URL=smb://nas/x.m3u\n",
            "no m3u": "EPG_URL=http://x/e.xml\n",
            "unknown key": "M3U_URL=http://x/a.m3u\nSHELL=/bin/sh\n",
            "duplicate": "M3U_URL=http://x/a.m3u\nM3U_URL=http://y/b.m3u\n",
            "space": "M3U_URL=http://x/a b.m3u\n",
            "newline injection": "M3U_URL=http://x/a.m3u\r<setting>\n",
            "too long": "M3U_URL=http://x/" + "a" * 3000 + "\n",
        }
        for name, text in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(provision.ConfigError):
                    self.read(text)

    def test_world_writable_and_non_root_configs_rejected(self):
        with self.assertRaises(provision.ConfigError):
            self.read("M3U_URL=http://x/a.m3u\n", mode=0o666)
        if os.geteuid() != 0:
            with self.assertRaisesRegex(provision.ConfigError, "owned by root"):
                provision.read_config(self.config("M3U_URL=http://x/a.m3u\n"))

    def test_symlinked_config_rejected(self):
        real = self.config("M3U_URL=http://x/a.m3u\n")
        link = self.dir / "link.conf"
        link.symlink_to(real)
        with self.assertRaises(provision.ConfigError):
            provision.read_config(link, require_root_owner=False)

    def test_write_is_atomic_private_and_idempotent(self):
        target = self.dir / "home/.kodi/userdata/addon_data/pvr.iptvsimple/instance-settings-1.xml"
        content = provision.render({"M3U_URL": "http://x/a.m3u"})
        self.assertTrue(provision.write_atomic(content, target))
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)
        self.assertFalse(provision.write_atomic(content, target))
        self.assertEqual(list(target.parent.glob(".dede-iptv-*")), [])

    def test_symlinked_target_refused(self):
        target_dir = self.dir / "home/.kodi/userdata/addon_data/pvr.iptvsimple"
        target_dir.mkdir(parents=True)
        (target_dir / "instance-settings-1.xml").symlink_to(self.dir / "elsewhere")
        with self.assertRaises(provision.ConfigError):
            provision.write_atomic("x", target_dir / "instance-settings-1.xml")


def channel(cid, number, lastplayed="", hidden=False, locked=False):
    return {"channelid": cid, "channelnumber": number, "lastplayed": lastplayed,
            "hidden": hidden, "locked": locked, "label": f"ch{cid}"}


class AutoplayChoiceTests(unittest.TestCase):
    def test_first_boot_starts_lowest_channel_number(self):
        chosen = autoplay.choose([channel(7, 6), channel(3, 2), channel(5, 1)])
        self.assertEqual(chosen["channelid"], 5)

    def test_restart_resumes_most_recent_in_kodi_sort_order(self):
        # Kodi returns lastplayed as a date only; two channels watched today
        # are told apart solely by Kodi's lastplayed-descending sort order.
        chosen = autoplay.choose([
            channel(2, 6, "2026-11-22"),
            channel(1, 1, "2026-11-22"),
            channel(3, 2, "1970-01-01"),
        ])
        self.assertEqual(chosen["channelid"], 2)

    def test_epoch_and_empty_lastplayed_mean_never_watched(self):
        chosen = autoplay.choose([channel(8, 3, "1970-01-01"),
                                  channel(9, 1, "garbage"), channel(7, 2, "")])
        self.assertEqual(chosen["channelid"], 9)

    def test_channel_request_sorts_by_lastplayed_descending(self):
        with patch.object(autoplay, "rpc", return_value={"channels": []}) as rpc:
            autoplay.tv_channels()
        params = rpc.call_args.args[1]
        self.assertEqual(params["sort"], {"method": "lastplayed", "order": "descending"})

    def test_unnumbered_channels_sort_last(self):
        chosen = autoplay.choose([channel(9, 0), channel(4, 4)])
        self.assertEqual(chosen["channelid"], 4)

    def test_hidden_locked_and_malformed_channels_skipped(self):
        reply = {"channels": [channel(1, 1, hidden=True), channel(2, 2, locked=True),
                              {"channelid": "3"}, "junk", channel(4, 4)]}
        with patch.object(autoplay, "rpc", return_value=reply):
            self.assertEqual([c["channelid"] for c in autoplay.tv_channels()], [4])

    def test_existing_playback_is_never_interrupted(self):
        calls = []

        def fake(method, params=None, timeout=5.0):
            calls.append(method)
            if method == "Player.GetActivePlayers":
                return [{"playerid": 1, "type": "video"}]
            raise AssertionError(f"unexpected {method}")
        with patch.object(autoplay, "rpc", side_effect=fake):
            self.assertEqual(autoplay.main(), 0)
        self.assertNotIn("Player.Open", calls)

    def test_starts_chosen_channel_and_requests_fullscreen(self):
        state = {"playing": False, "opened": None}

        def fake(method, params=None, timeout=5.0):
            if method == "Player.GetActivePlayers":
                return [{"playerid": 1, "type": "video"}] if state["playing"] else []
            if method == "PVR.GetChannels":
                return {"channels": [channel(11, 2), channel(10, 1)]}
            if method == "Player.Open":
                state["opened"] = params["item"]["channelid"]
                state["playing"] = True
                return "OK"
            if method == "GUI.SetFullscreen":
                return True
            raise AssertionError(method)
        with patch.object(autoplay, "rpc", side_effect=fake), \
             patch.object(autoplay.time, "sleep"):
            self.assertEqual(autoplay.main(), 0)
        self.assertEqual(state["opened"], 10)

    def test_no_channels_fails_without_opening(self):
        def fake(method, params=None, timeout=5.0):
            if method == "Player.GetActivePlayers":
                return []
            if method == "PVR.GetChannels":
                return {"channels": []}
            raise AssertionError(method)
        with patch.object(autoplay, "rpc", side_effect=fake), \
             patch.object(autoplay, "CHANNEL_WAIT_S", 0), \
             patch.object(autoplay.time, "sleep"):
            self.assertEqual(autoplay.main(), 1)


class KeymapHookTests(unittest.TestCase):
    HOOK = ROOT / "build/config/hooks/live/0520-dede-kodi-keymaps.hook.chroot"
    KEYMAP = ROOT / "build/config/includes.chroot/usr/share/kodi/system/keymaps/dede-tv.xml"

    def run_hook(self, keymaps, disabled):
        # Fake dpkg-divert: perform the rename it would do.
        fake = keymaps.parent / "fake-divert"
        fake.write_text('#!/bin/sh\n# args: --local --rename --divert DEST --add SRC\nmv "$6" "$4"\n')
        fake.chmod(0o755)
        env = dict(os.environ, DEDE_KODI_KEYMAPS=str(keymaps),
                   DEDE_KODI_KEYMAPS_DISABLED=str(disabled), DEDE_DPKG_DIVERT=str(fake))
        return subprocess.run(["sh", str(self.HOOK)], env=env, capture_output=True, text=True)

    def test_only_dede_keymap_remains_active(self):
        with tempfile.TemporaryDirectory() as tmp:
            keymaps, disabled = Path(tmp, "keymaps"), Path(tmp, "disabled")
            keymaps.mkdir()
            for name in ("keyboard.xml", "remote.xml", "mouse.xml", "appcommand.xml"):
                (keymaps / name).write_text("<keymap/>")
            (keymaps / "dede-tv.xml").write_text(self.KEYMAP.read_text())
            result = self.run_hook(keymaps, disabled)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(sorted(p.name for p in keymaps.glob("*.xml")), ["dede-tv.xml"])
            self.assertEqual(len(list(disabled.glob("*.xml"))), 4)

    def test_missing_dede_keymap_fails_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            keymaps = Path(tmp, "keymaps")
            keymaps.mkdir()
            (keymaps / "keyboard.xml").write_text("<keymap/>")
            self.assertNotEqual(self.run_hook(keymaps, Path(tmp, "d")).returncode, 0)

    def test_live_tv_state_machine_bindings(self):
        root = ET.parse(self.KEYMAP).getroot()

        def action(window, device, key):
            node = root.find(f"{window}/{device}/{key}")
            return None if node is None else node.text
        for device, ok, chup, back in (("keyboard", "return", "pageup", "escape"),
                                       ("remote", "select", "channelplus", "back")):
            with self.subTest(device=device):
                self.assertEqual(action("FullscreenLiveTV", device, ok),
                                 "ActivateWindow(PVROSDChannels)")
                self.assertEqual(action("FullscreenLiveTV", device, chup), "ChannelUp")
                self.assertEqual(action("FullscreenLiveTV", device, back), "noop")
        self.assertEqual(action("FullscreenLiveTV", "remote", "channelminus"), "ChannelDown")
        # Back must still close the channel list (global binding, not overridden).
        self.assertIsNone(action("PVROSDChannels", "remote", "back"))
        self.assertEqual(action("global", "remote", "back"), "Back")
        # No binding anywhere may open a Kodi window other than the channel list.
        for node in root.iter():
            text = (node.text or "").strip()
            if "ActivateWindow" in text:
                self.assertEqual(text, "ActivateWindow(PVROSDChannels)")
            for forbidden in ("Settings", "FileManager", "ShutDown", "Quit",
                              "ContextMenu", "Info", "OSD", "Home"):
                self.assertFalse(text.startswith(forbidden), f"{node.tag}: {text}")


if __name__ == "__main__":
    unittest.main()
