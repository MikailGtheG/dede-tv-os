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
               "DEDE_UI_CONFIG": str(self.tmp / "etc" / "ui.conf"),
               "DEDE_IPTV_CONFIG": str(self.tmp / "etc" / "iptv.conf"),
               "DEDE_IPTV_PROVISION": str(LIBEXEC / "dede-iptv-provision"),
               "DEDE_REMOTE_CONFIG": str(self.tmp / "etc" / "remote.conf"),
               "DEDE_WG_DIR": str(self.tmp / "wireguard"),
               "DEDE_WG": str(self.fake_wg()),
               "DEDE_SYSTEMCTL": str(self.fake_systemctl()),
               "DEDE_TAILSCALE": str(self.fake_tailscale()),
               "DEDE_TS_CONFIG": str(self.tmp / "etc" / "tailscale.conf"),
               "DEDE_TS_FLAG": str(self.tmp / "etc" / "tailscale.enabled"),
               "DEDE_TS_WAIT": "0",
               "DEDE_APPLY_REBOOT": f"touch {self.tmp / 'rebooted'}",
               "DEDE_PROC_UPTIME": str(self.tmp / "uptime"),
               "DEDE_OS_RELEASE": str(self.tmp / "os-release")}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in old.items()])
        self.apply = load("dede_admin_apply", LIBEXEC / "dede-admin-apply")
        self.sync = load("dede_virtual_sync_for_apply", LIBEXEC / "dede-virtual-sync")

    def fake_wg(self):
        """genkey: random key; pubkey: a key derived from stdin, like wg(8)."""
        path = self.tmp / "wg"
        path.write_text("#!/usr/bin/env python3\nimport base64, hashlib, os, sys\n"
                        "data = os.urandom(32) if sys.argv[1] == 'genkey' else "
                        "hashlib.sha256(sys.stdin.read().strip().encode()).digest()\n"
                        "print(base64.b64encode(data).decode())\n")
        path.chmod(0o755)
        return path

    def fake_tailscale(self):
        """status prints self.ts_status; up/logout are logged and may change it."""
        self.ts_status = self.tmp / "ts-status.json"
        self.ts_status.write_text(json.dumps({"BackendState": "NoState"}))
        self.ts_log = self.tmp / "ts.log"
        path = self.tmp / "tailscale"
        path.write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            f"status, log = {str(self.ts_status)!r}, {str(self.ts_log)!r}\n"
            "open(log, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
            "data = json.load(open(status))\n"
            "if sys.argv[1] == 'status':\n    print(json.dumps(data.get('_status', data)))\n"
            "elif sys.argv[1] == 'up' and '_after_up' in data:\n"
            "    json.dump(data['_after_up'], open(status, 'w'))\n")
        path.chmod(0o755)
        return path

    def fake_systemctl(self):
        self.units = self.tmp / "systemctl.log"
        path = self.tmp / "systemctl"
        path.write_text(f"#!/bin/sh\necho \"$*\" >> '{self.units}'\n"
                        "case \"$1\" in\n"
                        "  is-active) case \"$2\" in dede-kodi.service) echo active;;"
                        " wg-quick@dede0.service) echo '<b>';; *) echo inactive;; esac;;\n"
                        "  --failed) printf 'dede-tv-guard.service loaded failed failed Guard\\n"
                        "bad;unit loaded failed failed x\\n';;\n"
                        "esac\n")
        path.chmod(0o755)
        return path

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

    def iptv(self, m3u, epg="", **extra):
        rid = "e" * 32
        body = {"id": rid, "kind": "iptv", "m3u_url": m3u, "epg_url": epg, **extra}
        (self.state / "requests" / f"{rid}.json").write_text(json.dumps(body))
        self.apply.main()
        return json.loads((self.state / "results" / f"{rid}.json").read_text())

    def test_iptv_source_is_written_and_restarts_only_on_change(self):
        conf = self.tmp / "etc" / "iptv.conf"
        url = "https://tv.example/get.php?user=a&token=x%20y"
        self.assertTrue(self.iptv(url, "http://10.0.2.2:8089/epg.xml")["ok"])
        self.assertEqual(conf.read_text(), f"M3U_URL={url}\nEPG_URL=http://10.0.2.2:8089/epg.xml\n")
        self.assertEqual(conf.stat().st_mode & 0o777, 0o640)
        self.assertTrue(self.restarted.exists())
        self.restarted.unlink()
        self.assertTrue(self.iptv(url, "http://10.0.2.2:8089/epg.xml")["ok"])
        self.assertFalse(self.restarted.exists())
        self.assertTrue(self.iptv(url)["ok"])
        self.assertEqual(conf.read_text(), f"M3U_URL={url}\n")

    def test_invalid_iptv_sources_are_refused(self):
        for m3u, epg, code in (
            ("", "", "invalid-address"),
            ("ftp://tv.example/x.m3u", "", "invalid-address"),
            ("file:///etc/shadow", "", "invalid-address"),
            ("https://tv.example/a b.m3u", "", "invalid-address"),
            ("http://localhost:9090/", "", "local-address"),
            ("http://127.0.0.1/x.m3u", "", "local-address"),
            ("http://[::1]/x.m3u", "", "local-address"),
            ("http://169.254.169.254/latest", "", "local-address"),
            ("https://tv.example/x.m3u", "http://127.0.0.1/epg", "local-address"),
        ):
            with self.subTest(m3u=m3u, epg=epg):
                self.assertEqual(self.iptv(m3u, epg)["error"], code)
        self.assertEqual(self.iptv("https://tv.example/x.m3u", extra=1)["error"], "bad-request")
        self.assertFalse((self.tmp / "etc" / "iptv.conf").exists())
        self.assertFalse(self.restarted.exists())

    def remote(self, kind, request_id, **fields):
        body = {"id": request_id, "kind": kind, **fields}
        (self.state / "requests" / f"{request_id}.json").write_text(json.dumps(body))
        self.apply.main()
        return json.loads((self.state / "results" / f"{request_id}.json").read_text())

    def test_wireguard_devices_are_added_once_and_removed(self):
        tunnel = self.tmp / "wireguard" / "dede0.conf"
        remote = self.tmp / "etc" / "remote.conf"
        first = self.remote("wireguard-add", "1" * 32, name="Handy Mikail", endpoint="haus.example.org")
        self.assertTrue(first["ok"], first)
        peer = first["peer"]
        self.assertIn("Address = 10.77.0.2/32", peer)
        self.assertIn("Endpoint = haus.example.org:51820", peer)
        self.assertIn("AllowedIPs = 10.77.0.1/32", peer)
        private = peer.split("PrivateKey = ")[1].split("\n")[0]
        # Only the device's public key stays on the TV.
        for kept in (tunnel, remote, self.tmp / "wireguard" / "dede0.key"):
            self.assertNotIn(private, kept.read_text())
        self.assertEqual(tunnel.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.tmp / "wireguard" / "dede0.key").stat().st_mode & 0o777, 0o600)
        self.assertEqual(remote.stat().st_mode & 0o777, 0o640)
        self.assertIn("ListenPort = 51820", tunnel.read_text())
        self.assertIn("AllowedIPs = 10.77.0.2/32", tunnel.read_text())
        self.assertIn("WG_ENDPOINT=haus.example.org:51820\n", remote.read_text())
        server_key = (self.tmp / "wireguard" / "dede0.key").read_text()

        second = self.remote("wireguard-add", "2" * 32, name="Tablet Ayşe", endpoint="[2001:db8::1]:4500")
        self.assertIn("Address = 10.77.0.3/32", second["peer"])
        self.assertIn("Endpoint = [2001:db8::1]:4500", second["peer"])
        self.assertEqual((self.tmp / "wireguard" / "dede0.key").read_text(), server_key)
        server_public = first["peer"].split("[Peer]\nPublicKey = ")[1].split("\n")[0]
        self.assertIn(f"PublicKey = {server_public}", second["peer"])
        self.assertEqual(self.units.read_text(), f"restart {self.apply.WG_UNIT}\n" * 2)

        self.assertEqual(self.remote("wireguard-remove", "3" * 32, address="10.77.0.9")["error"],
                         "unknown-device")
        self.assertTrue(self.remote("wireguard-remove", "4" * 32, address="10.77.0.2")["ok"])
        self.assertNotIn("10.77.0.2", tunnel.read_text() + remote.read_text())
        self.assertIn("Tablet Ayşe", remote.read_text())
        self.assertTrue(self.remote("wireguard-remove", "5" * 32, address="10.77.0.3")["ok"])
        self.assertFalse(tunnel.exists())
        self.assertTrue(self.units.read_text().endswith(f"stop {self.apply.WG_UNIT}\n"))
        self.assertNotIn("WG_DEVICE", remote.read_text())
        # The next device gets the lowest free address again.
        again = self.remote("wireguard-add", "6" * 32, name="Laptop", endpoint="203.0.113.7")
        self.assertIn("Address = 10.77.0.2/32", again["peer"])

    def test_invalid_wireguard_requests_change_nothing(self):
        for fields in (
            {"name": "", "endpoint": "haus.example.org"},
            {"name": "a\nb", "endpoint": "haus.example.org"},
            {"name": "x" * 33, "endpoint": "haus.example.org"},
            {"name": "<b>", "endpoint": "haus.example.org"},
            {"name": "Handy", "endpoint": "haus.example.org:0"},
            {"name": "Handy", "endpoint": "haus.example.org:"},
            {"name": "Handy", "endpoint": "http://haus.example.org"},
            {"name": "Handy", "endpoint": "2001:db8::1"},
            {"name": "Handy", "endpoint": "1234"},
            {"name": "Handy", "endpoint": "haus.example.org", "extra": 1},
            {"name": 7, "endpoint": "haus.example.org"},
        ):
            with self.subTest(fields=fields):
                self.assertEqual(self.remote("wireguard-add", "7" * 32, **fields)["error"], "bad-request")
        self.assertEqual(self.remote("wireguard-remove", "8" * 32, address="10.77.0.2", x=1)["error"],
                         "bad-request")
        self.assertFalse((self.tmp / "wireguard").exists())
        self.assertFalse((self.tmp / "etc" / "remote.conf").exists())
        self.assertFalse(self.units.exists())

    def test_tailscale_sign_in_link_status_and_sign_out(self):
        conf = self.tmp / "etc" / "tailscale.conf"
        link = "https://login.tailscale.com/a/1a2b3c4d"
        self.ts_status.write_text(json.dumps({"BackendState": "NeedsLogin", "_after_up": {
            "BackendState": "NeedsLogin", "AuthURL": link}}))
        result = self.remote("tailscale-login", "a" * 32)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["login"], link)
        self.assertTrue((self.tmp / "etc" / "tailscale.enabled").exists())
        self.assertIn("start tailscaled.service", self.units.read_text())
        self.assertIn("up --hostname=dede-tv --accept-dns=false --timeout=5s", self.ts_log.read_text())
        self.assertEqual(conf.read_text().splitlines()[1:], ["TS_STATE=NeedsLogin"])
        # The owner signed in on the phone.
        self.ts_status.write_text(json.dumps({"BackendState": "Running", "Self": {
            "TailscaleIPs": ["100.101.102.103", "fd7a:115c:a1e0::1"]}}))
        self.assertTrue(self.remote("tailscale-status", "b" * 32)["ok"])
        self.assertEqual(conf.read_text().splitlines()[1:],
                         ["TS_STATE=Running", "TS_ADDRESS=100.101.102.103"])
        again = self.remote("tailscale-login", "c" * 32)
        self.assertTrue(again["ok"])
        self.assertNotIn("login", again)
        self.assertTrue(self.remote("tailscale-logout", "d" * 32)["ok"])
        self.assertFalse((self.tmp / "etc" / "tailscale.enabled").exists())
        self.assertTrue(self.units.read_text().endswith("stop tailscaled.service\n"))
        self.assertEqual(conf.read_text().splitlines()[1:], ["TS_STATE=Stopped"])
        self.assertTrue(self.remote("tailscale-status", "e" * 32)["ok"])
        self.assertEqual(conf.read_text().splitlines()[1:], ["TS_STATE=Stopped"])

    def test_tailscale_without_link_or_with_bad_data_is_refused(self):
        for status in ({"BackendState": "NeedsLogin"},
                       {"BackendState": "NeedsLogin", "AuthURL": "http://login.example/x"},
                       {"BackendState": "NeedsLogin", "AuthURL": "https://x.example/a b"},
                       {"BackendState": "<b>", "AuthURL": "javascript:alert(1)"}):
            with self.subTest(status=status):
                self.ts_status.write_text(json.dumps(status))
                self.assertEqual(self.remote("tailscale-login", "f" * 32)["error"], "tailscale-unreachable")
        self.ts_status.write_text(json.dumps({"BackendState": "Running", "Self": {
            "TailscaleIPs": ["8.8.8.8", "not an ip"]}}))
        self.assertTrue(self.remote("tailscale-status", "f" * 32)["ok"])
        self.assertEqual((self.tmp / "etc" / "tailscale.conf").read_text().splitlines()[1:],
                         ["TS_STATE=Running"])
        self.assertEqual(self.remote("tailscale-login", "f" * 32, x=1)["error"], "bad-request")

    def test_diagnostics_report(self):
        (self.tmp / "uptime").write_text("7384.21 1234.00\n")
        (self.tmp / "os-release").write_text('PRETTY_NAME="Debian GNU/Linux 13 (trixie)"\n')
        report = self.remote("diagnostics", "9" * 32)["report"]
        self.assertEqual(report["uptime_s"], 7384)
        self.assertEqual(report["system"], "Debian GNU/Linux 13 (trixie)")
        self.assertEqual(report["units"], {"tv": "active", "guard": "inactive", "console": "inactive",
                                           "tailscale": "inactive", "wireguard": "unknown"})
        self.assertEqual(report["failed"], ["dede-tv-guard.service"])
        self.assertGreater(report["media_total_mb"], 0)
        self.assertEqual(self.remote("diagnostics", "9" * 32, x=1)["error"], "bad-request")

    def test_restart_tv_and_device(self):
        self.assertTrue(self.remote("restart-tv", "a" * 32)["ok"])
        self.assertTrue(self.restarted.exists())
        self.assertFalse((self.tmp / "rebooted").exists())
        self.assertTrue(self.remote("reboot", "b" * 32)["ok"])
        self.assertTrue((self.tmp / "rebooted").exists())

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
