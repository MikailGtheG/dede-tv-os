#!/usr/bin/env python3
"""dede-admin-apply: requests from the console are re-validated as root."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
LIBEXEC = ROOT / "build/config/includes.chroot/usr/local/libexec"


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: os.system(f"rm -rf '{self.tmp}'"))
        self.media = self.tmp / "media"
        (self.media / "film").mkdir(parents=True)
        (self.media / "neu").mkdir()
        self.state = self.tmp / "state"
        (self.state / "requests").mkdir(parents=True)
        self.config = self.tmp / "etc" / "virtual-channels.conf"
        self.restarted = self.tmp / "restarted"
        env = {"DEDE_ADMIN_STATE": str(self.state), "DEDE_VIRTUAL_CONFIG": str(self.config),
               "DEDE_MEDIA_ROOT": str(self.media), "DEDE_VIRTUAL_SYNC": str(LIBEXEC / "dede-virtual-sync"),
               "DEDE_APPLY_RESTART": f"touch {self.restarted}",
               "DEDE_UI_CONFIG": str(self.tmp / "etc" / "ui.conf")}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in old.items()])
        self.apply = load("dede_admin_apply", LIBEXEC / "dede-admin-apply")
        self.sync = load("dede_virtual_sync_for_apply", LIBEXEC / "dede-virtual-sync")

    def request(self, channels, request_id="0" * 31 + "1", **extra):
        body = {"id": request_id, "kind": "virtual-channels", "channels": channels, **extra}
        (self.state / "requests" / f"{request_id}.json").write_text(json.dumps(body))
        self.apply.main()
        self.assertFalse((self.state / "requests" / f"{request_id}.json").exists())
        return json.loads((self.state / "results" / f"{request_id}.json").read_text())

    def test_restart_only_when_the_picture_changes(self):
        self.assertTrue(self.request([{"name": "Neu", "number": 7, "folder": "neu"}])["ok"])
        self.assertFalse(self.restarted.exists(), "empty channel must not restart the TV")
        (self.media / "neu" / "a.mp4").write_bytes(b"x")
        self.assertTrue(self.request([{"name": "Neu", "number": 7, "folder": "neu"}], refresh=True)["ok"])
        self.assertTrue(self.restarted.exists())
        self.restarted.unlink()
        self.assertTrue(self.request([])["ok"])
        self.assertTrue(self.restarted.exists(), "removing a channel on air must restart")
        self.assertEqual(self.request([], refresh="yes")["error"], "bad-request")

    def test_valid_list_is_written_and_tv_restarted(self):
        (self.media / "film" / "a.mkv").write_bytes(b"x")
        result = self.request([{"name": "Çizgi = Spaß", "number": 7, "folder": "neu"},
                               {"name": "Dede Film", "number": 5, "folder": "film"}])
        self.assertEqual(result, {"id": "0" * 31 + "1", "ok": True})
        self.assertTrue(self.restarted.exists())
        read = self.sync.read_config(self.config, require_root_owner=False)
        self.assertEqual(read, [{"name": "Dede Film", "number": 5, "folder": "film"},
                                {"name": "Çizgi = Spaß", "number": 7, "folder": "neu"}])
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o640)

    def test_empty_list_removes_all_channels(self):
        self.assertTrue(self.request([])["ok"])
        self.assertEqual(self.sync.read_config(self.config, require_root_owner=False), [])

    def test_invalid_requests_change_nothing(self):
        self.config.parent.mkdir()
        self.config.write_text("[Alt]\nnumber = 3\nfolder = film\n")
        (self.media / "link").symlink_to(self.tmp)
        cases = [
            ([{"name": "A[1]", "number": 7, "folder": "neu"}], "invalid-name"),
            ([{"name": "pvr:1", "number": 7, "folder": "neu"}], "invalid-name"),
            ([{"name": " A", "number": 7, "folder": "neu"}], "invalid-name"),
            ([{"name": "A", "number": 0, "folder": "neu"}], "invalid-number"),
            ([{"name": "A", "number": True, "folder": "neu"}], "invalid-number"),
            ([{"name": "A", "number": 7, "folder": "neu"}, {"name": "B", "number": 7, "folder": "film"}],
             "duplicate-number"),
            ([{"name": "A", "number": 7, "folder": "../etc"}], "invalid-folder"),
            ([{"name": "A", "number": 7, "folder": "fehlt"}], "invalid-folder"),
            ([{"name": "A", "number": 7, "folder": "link"}], "invalid-folder"),
            ([{"name": "A", "number": 7, "folder": "neu", "extra": 1}], "bad-request"),
            ("nope", "bad-request"),
        ]
        for channels, code in cases:
            with self.subTest(code=code, channels=channels):
                self.assertEqual(self.request(channels), {"id": "0" * 31 + "1", "ok": False, "error": code})
        self.assertEqual(self.config.read_text(), "[Alt]\nnumber = 3\nfolder = film\n")
        self.assertFalse(self.restarted.exists())

    def test_unknown_kind_and_mismatched_id(self):
        self.assertEqual(self.request([], kind="shell")["error"], "unknown-kind")
        rid = "a" * 32
        (self.state / "requests" / f"{rid}.json").write_text(json.dumps({"id": "b" * 32, "kind": "virtual-channels", "channels": []}))
        self.apply.main()
        self.assertEqual(json.loads((self.state / "results" / f"{rid}.json").read_text())["error"], "bad-request")

    def language(self, value, **extra):
        rid = "d" * 32
        body = {"id": rid, "kind": "language", "language": value, **extra}
        (self.state / "requests" / f"{rid}.json").write_text(json.dumps(body))
        self.apply.main()
        return json.loads((self.state / "results" / f"{rid}.json").read_text())

    def test_language_is_written_and_restarts_only_on_change(self):
        ui = self.tmp / "etc" / "ui.conf"
        self.assertTrue(self.language("tr")["ok"])
        self.assertIn("LANGUAGE=tr\n", ui.read_text())
        self.assertEqual(ui.stat().st_mode & 0o777, 0o640)
        self.assertTrue(self.restarted.exists())
        self.restarted.unlink()
        self.assertTrue(self.language("tr")["ok"])
        self.assertFalse(self.restarted.exists(), "same language must not restart")
        # German is the default: choosing it on a fresh device changes nothing.
        ui.unlink()
        self.assertTrue(self.language("de")["ok"])
        self.assertFalse(self.restarted.exists())

    def test_invalid_language_requests(self):
        for value, extra in (("fr", {}), ("../x", {}), (None, {}), ("tr", {"extra": 1})):
            self.assertEqual(self.language(value, **extra)["error"], "bad-request")
        self.assertFalse((self.tmp / "etc" / "ui.conf").exists())

    def test_symlinked_or_badly_named_requests_are_dropped(self):
        target = self.tmp / "elsewhere.json"
        target.write_text(json.dumps({"id": "c" * 32, "kind": "virtual-channels", "channels": []}))
        (self.state / "requests" / f"{'c' * 32}.json").symlink_to(target)
        (self.state / "requests" / "../escape.json".replace("../", "x")).write_text("{}")
        self.apply.main()
        self.assertEqual(json.loads((self.state / "results" / f"{'c' * 32}.json").read_text())["error"], "bad-request")
        self.assertEqual(list((self.state / "requests").iterdir()), [])
        self.assertTrue(target.exists())
        self.assertFalse(self.config.exists())


if __name__ == "__main__":
    unittest.main()
