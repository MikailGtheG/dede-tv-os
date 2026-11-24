#!/usr/bin/env python3
"""Regression tests for local 24/7 channels: sync helper and Kodi resolver.

Uses fake ffprobe and, unless DEDE_CORE_BIN points at a real dede-tv-core
binary, a fake core with the same CLI contract (add/list/now/remove/
request-virtual/schedule).
"""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
CHROOT = ROOT / "build/config/includes.chroot"
REAL_CORE = os.environ.get("DEDE_CORE_BIN")

FAKE_CORE = r'''#!/usr/bin/env python3
import json, sys, time
db, cmd, args = sys.argv[2], sys.argv[3], sys.argv[4:]
try:
    state = json.load(open(db))
except Exception:
    state = {}
def save():
    json.dump(state, open(db, "w"))
if cmd == "add":
    name, rest = args[0], args[1:]
    videos = [{"path": rest[i], "duration_ms": int(rest[i + 1])} for i in range(0, len(rest), 2)]
    state[name] = {"videos": videos, "start": int(time.time() * 1000)}
    state.setdefault("_adds", 0); state["_adds"] += 1
    save(); print("Added")
elif cmd == "list":
    print("\n".join(k for k in state if not k.startswith("_")))
elif cmd == "remove":
    if args[0] not in state: sys.exit(1)
    del state[args[0]]; save()
elif cmd in ("now", "request-virtual"):
    ch = state.get(args[0])
    if not ch: sys.exit(1)
    total = sum(v["duration_ms"] for v in ch["videos"])
    pos = (int(time.time() * 1000) - ch["start"]) % total
    for v in ch["videos"]:
        if pos < v["duration_ms"]: break
        pos -= v["duration_ms"]
    if cmd == "now":
        print(f"{v['path']} @ {pos} ms")
    else:
        r = {"hours": pos // 3600000, "minutes": pos // 60000 % 60,
             "seconds": pos // 1000 % 60, "milliseconds": pos % 1000}
        print(json.dumps({"jsonrpc": "2.0", "method": "Player.Open", "id": 1,
                          "params": {"item": {"file": v["path"]}, "options": {"resume": r}}}))
elif cmd == "schedule":
    ch = state.get(args[0])
    if not ch: sys.exit(1)
    now = int(time.time() * 1000)
    until, t, slots = now + int(args[1]) * 3600000, ch["start"], []
    while t < until:
        for v in ch["videos"]:
            if t + v["duration_ms"] > now and t < until:
                slots.append({"path": v["path"], "start_ms": t, "end_ms": t + v["duration_ms"]})
            t += v["duration_ms"]
    print(json.dumps(slots))
else:
    sys.exit(2)
'''

FAKE_FFPROBE = r'''#!/bin/sh
# Duration encoded in the file content; "broken" files fail like ffprobe.
content=$(cat "$7")
case "$content" in broken*) exit 1 ;; esac
echo "$content"
'''


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.media = base / "media"
        (self.media / "film").mkdir(parents=True)
        self.config = base / "virtual-channels.conf"
        bin_dir = base / "bin"
        bin_dir.mkdir()
        core = Path(REAL_CORE) if REAL_CORE else bin_dir / "core"
        if not REAL_CORE:
            core.write_text(FAKE_CORE)
            core.chmod(0o755)
        ffprobe = bin_dir / "ffprobe"
        ffprobe.write_text(FAKE_FFPROBE)
        ffprobe.chmod(0o755)
        os.environ.update(HOME=str(self.home), DEDE_VIRTUAL_CONFIG=str(self.config),
                          DEDE_MEDIA_ROOT=str(self.media), DEDE_CORE_BIN=str(core),
                          DEDE_FFPROBE=str(ffprobe))
        self.sync = load("dede_virtual_sync", CHROOT / "usr/local/libexec/dede-virtual-sync")

    def tearDown(self):
        self.tmp.cleanup()

    def film(self, name, seconds):
        (self.media / "film" / name).write_text(str(seconds))

    def configure(self, text="[Dede Film]\nnumber = 5\nfolder = film\n"):
        self.config.write_text(text)
        self.config.chmod(0o640)
        return self.sync.read_config(self.config, require_root_owner=False)

    def run_sync(self):
        return self.sync.sync(self.configure())


class ConfigTests(Fixture):
    def test_valid_config(self):
        self.assertEqual(self.configure(), [{"name": "Dede Film", "number": 5, "folder": "film"}])

    def test_rejected_configs(self):
        cases = {
            "escape": "[A]\nnumber = 5\nfolder = ../etc\n",
            "absolute": "[A]\nnumber = 5\nfolder = /etc\n",
            "duplicate number": "[A]\nnumber = 5\nfolder = a\n[B]\nnumber = 5\nfolder = b\n",
            "bad number": "[A]\nnumber = fünf\nfolder = a\n",
            "zero": "[A]\nnumber = 0\nfolder = a\n",
            "unknown key": "[A]\nnumber = 5\nfolder = a\ncommand = rm\n",
            "comma in name": "[Film, Serien]\nnumber = 5\nfolder = a\n",
            "reserved prefix": "[pvr:1]\nnumber = 5\nfolder = a\n",
            "bidi": "[Film‮]\nnumber = 5\nfolder = a\n",
        }
        for case, text in cases.items():
            with self.subTest(case=case):
                with self.assertRaises(self.sync.ConfigError):
                    self.configure(text)

    def test_world_writable_config_rejected(self):
        self.config.write_text("[A]\nnumber = 5\nfolder = film\n")
        self.config.chmod(0o666)
        with self.assertRaises(self.sync.ConfigError):
            self.sync.read_config(self.config, require_root_owner=False)

    def test_symlinked_folder_escape_rejected(self):
        outside = Path(self.tmp.name) / "outside"
        outside.mkdir()
        (self.media / "evil").symlink_to(outside)
        with self.assertRaises(self.sync.ConfigError):
            self.sync.media_folder("evil", self.media)


class SyncTests(Fixture):
    def test_channel_created_playlist_and_kodi_instance_written(self):
        self.film("a.mkv", 40)
        self.film("b.mp4", 50)
        self.film("notes.txt", 1)
        applied = self.run_sync()
        self.assertEqual([c["name"] for c in applied], ["Dede Film"])
        m3u = self.sync.PLAYLIST.read_text()
        self.assertIn('tvg-chno="5"', m3u)
        self.assertIn(",Dede Film\nplugin://plugin.video.dede-virtual/?channel=Dede%20Film\n", m3u)
        settings = {s.get("id"): s.text for s in ET.parse(self.sync.INSTANCE).getroot()}
        self.assertEqual(settings["m3uPathType"], "0")
        self.assertEqual(settings["m3uPath"], str(self.sync.PLAYLIST))
        self.assertEqual(settings["numberByOrder"], "false")
        now = subprocess.run([os.environ["DEDE_CORE_BIN"], "--db", str(self.sync.DB), "now", "Dede Film"],
                             capture_output=True, text=True)
        self.assertEqual(now.returncode, 0)
        self.assertIn(str(self.media / "film"), now.stdout)

    def test_restart_keeps_the_running_schedule(self):
        # Redefining a channel restarts its schedule: never on a plain reboot.
        self.film("a.mkv", 40)
        self.run_sync()
        before = self.sync.DB.read_bytes()
        calls = []
        original = self.sync.core
        self.sync.core = lambda *a, **k: (calls.append(a[0]), original(*a, **k))[1]
        self.run_sync()
        self.assertNotIn("add", calls)
        self.assertEqual(self.sync.DB.read_bytes(), before)

    def test_changed_library_redefines_channel(self):
        self.film("a.mkv", 40)
        self.run_sync()
        self.film("b.mkv", 30)
        calls = []
        original = self.sync.core
        self.sync.core = lambda *a, **k: (calls.append(a[0]), original(*a, **k))[1]
        self.run_sync()
        self.assertIn("add", calls)

    def test_broken_media_skipped_and_durations_cached(self):
        self.film("a.mkv", 40)
        (self.media / "film" / "bad.mkv").write_text("broken")
        self.run_sync()
        cache = json.loads(self.sync.DURATIONS.read_text())
        self.assertEqual(sorted(k.split("|")[0].rsplit("/", 1)[1] for k in cache), ["a.mkv"])
        self.assertEqual(list(cache.values()), [40000])

    def test_probe_budget_keeps_schedule_until_measured(self):
        self.film("a.mkv", 40)
        self.run_sync()
        self.film("b.mkv", 30)
        self.sync.PROBE_BUDGET_S = 0
        before = self.sync.DB.read_bytes()
        self.run_sync()
        self.assertEqual(self.sync.DB.read_bytes(), before)

    def test_removed_channel_leaves_core_and_kodi(self):
        self.film("a.mkv", 40)
        self.run_sync()
        self.config.write_text("")
        # Test files are not root-owned; the ownership rule has its own test.
        original = self.sync.read_config
        self.sync.read_config = lambda: original(self.config, require_root_owner=False)
        self.assertEqual(self.sync.main(), 0)
        self.assertFalse(self.sync.INSTANCE.exists())
        self.sync.sync([])
        listed = subprocess.run([os.environ["DEDE_CORE_BIN"], "--db", str(self.sync.DB), "list"],
                                capture_output=True, text=True)
        self.assertNotIn("Dede Film", listed.stdout)

    def test_empty_folder_publishes_no_channel(self):
        self.assertEqual(self.run_sync(), [])
        self.assertFalse(self.sync.INSTANCE.exists())


class EpgTests(Fixture):
    def guide(self):
        root = ET.parse(self.sync.EPG).getroot()
        return root.findall("channel"), root.findall("programme")

    def test_guide_written_and_linked_to_the_channel(self):
        self.film("Erster_Film.mkv", 40)
        self.film("b.mp4", 50)
        self.run_sync()
        settings = {s.get("id"): s.text for s in ET.parse(self.sync.INSTANCE).getroot()}
        self.assertEqual(settings["epgPathType"], "0")
        self.assertEqual(settings["epgPath"], str(self.sync.EPG))
        channels, programmes = self.guide()
        tvg = self.sync.tvg_id("Dede Film")
        self.assertIn(f'tvg-id="{tvg}"', self.sync.PLAYLIST.read_text())
        self.assertEqual([(c.get("id"), c.findtext("display-name")) for c in channels],
                         [(tvg, "Dede Film")])
        self.assertGreater(len(programmes), 100)
        self.assertEqual({p.get("channel") for p in programmes}, {tvg})
        for first, second in zip(programmes, programmes[1:]):
            self.assertEqual(first.get("stop"), second.get("start"))
        self.assertEqual({p.findtext("title") for p in programmes}, {"Erster Film", "b"})
        now = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        self.assertLessEqual(programmes[0].get("start")[:14], now)
        self.assertGreater(programmes[0].get("stop")[:14], now)
        self.assertTrue(programmes[0].get("start").endswith(" +0000"))

    def test_titles_are_escaped(self):
        self.film("<b>Tom & Jerry<i>.mp4", 40)
        self.run_sync()
        _, programmes = self.guide()
        self.assertEqual(programmes[0].findtext("title"), "<b>Tom & Jerry<i>")

    def test_hourly_refresh_rewrites_guide_without_touching_schedule(self):
        self.film("a.mkv", 40)
        self.run_sync()
        db = json.loads(self.sync.DB.read_text()) if not REAL_CORE else None
        self.sync.EPG.unlink()
        self.configure("[Dede Film]\nnumber = 5\nfolder = film\n[Neu]\nnumber = 6\nfolder = film\n")
        ready = self.sync.refresh_epg(self.sync.read_config(self.config, require_root_owner=False))
        self.assertEqual([c["name"] for c in ready], ["Dede Film"])
        self.assertTrue(self.sync.EPG.is_file())
        if db is not None:
            self.assertEqual(json.loads(self.sync.DB.read_text()), db)

    def test_refresh_before_first_sync_is_a_no_op(self):
        self.assertEqual(self.sync.refresh_epg(self.configure()), [])
        self.assertFalse(self.sync.EPG.exists())

    def test_programme_titles(self):
        title = self.sync.programme_title
        self.assertEqual(title("/m/film/Der.grosse_Film.2020.mkv"), "Der grosse Film 2020")
        self.assertEqual(title("/m/film/___.mp4"), "___.mp4")


class ResolverTests(Fixture):
    def setUp(self):
        super().setUp()
        self.resolver = load("dede_virtual_resolver",
                             CHROOT / "usr/share/kodi/addons/plugin.video.dede-virtual/default.py")
        self.resolver.CORE = os.environ["DEDE_CORE_BIN"]
        self.resolver.MEDIA_ROOT = str(self.media.resolve())
        self.resolver.DB = str(self.sync.DB)
        self.resolver.DURATIONS = str(self.sync.DURATIONS)

    def test_resolves_current_film_and_position_with_duration(self):
        self.film("a.mkv", 40)
        self.run_sync()
        time.sleep(1.2)
        path, position = self.resolver.current_playback("Dede Film")
        self.assertEqual(Path(path).name, "a.mkv")
        self.assertGreaterEqual(position, 1.0)
        self.assertLess(position, 40)
        self.assertEqual(self.resolver.duration_seconds(path, position), 40.0)

    def test_position_follows_wall_clock_while_nobody_watches(self):
        self.film("a.mkv", 600)
        self.run_sync()
        _, first = self.resolver.current_playback("Dede Film")
        time.sleep(2.1)
        _, second = self.resolver.current_playback("Dede Film")
        self.assertAlmostEqual(second - first, 2.1, delta=0.6)

    def test_core_call_latency_is_compensated(self):
        # A slow core start must not make the film start behind schedule.
        reply = {"params": {"item": {"file": str(self.media.resolve()) + "/film/a.mkv"},
                            "options": {"resume": {"hours": 0, "minutes": 0, "seconds": 10,
                                                   "milliseconds": 0}}}}

        class Result:
            returncode, stdout = 0, json.dumps(reply)
        ticks = iter([100.0, 104.0])
        _, position = self.resolver.current_playback(
            "Dede Film", run=lambda *a, **k: Result(), clock=lambda: next(ticks))
        self.assertAlmostEqual(position, 14.0)

    def test_unknown_or_invalid_channel_fails_cleanly(self):
        for name in ("Unbekannt", "", "x\nPlayer.Quit", "a" * 200):
            with self.subTest(name=name[:20]):
                with self.assertRaises(ValueError):
                    self.resolver.current_playback(name)

    def test_channel_parameter_parsing(self):
        self.assertEqual(self.resolver.channel_from_argv(
            ["plugin://x/", "1", "?channel=Dede%20Film"]), "Dede Film")
        self.assertEqual(self.resolver.channel_from_argv(
            ["plugin://x/", "1", "?channel=a&channel=b"]), "")


if __name__ == "__main__":
    unittest.main()
