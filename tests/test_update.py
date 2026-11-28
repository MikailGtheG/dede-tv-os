#!/usr/bin/env python3
"""dede-update: only signed, matching images; A/B slots; rollback bookkeeping."""
import functools
import hashlib
import http.server
import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "build/config/includes.chroot/usr/local/libexec/dede-update"
LIB = ROOT / "build/config/includes.chroot/usr/local/lib/dede-tv"
sys.path.insert(0, str(LIB))
import dede_grubenv  # noqa: E402

CMDLINE = "boot=live components persistence persistence-label=dede-data quiet"


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", str(self.tmp)]))
        self.www = self.tmp / "www"
        self.www.mkdir()
        self.boot = self.tmp / "boot"
        (self.boot / "images").mkdir(parents=True)
        dede_grubenv.write(self.boot / "grubenv", {})
        keys = self.tmp / "keys"
        keys.mkdir()
        self.key = self.tmp / "signing.key"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(self.key)],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "pkey", "-in", str(self.key), "-pubout", "-out", str(keys / "test.pem")],
                       check=True, capture_output=True)
        self.other = self.tmp / "other.key"
        subprocess.run(["openssl", "genpkey", "-algorithm", "ed25519", "-out", str(self.other)],
                       check=True, capture_output=True)
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(Quiet, directory=str(self.www)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        self.url = f"http://127.0.0.1:{server.server_address[1]}/"
        (self.tmp / "source.conf").write_text(f"UPDATE_URL={self.url}\n")
        (self.tmp / "version").write_text("0.1.0\n")
        self.cmdline = self.tmp / "cmdline"
        self.cmdline.write_text(CMDLINE + "\n")
        self.reboots = self.tmp / "reboots"
        fake = self.tmp / "reboot"
        fake.write_text(f"#!/bin/sh\necho reboot >> '{self.reboots}'\n")
        fake.chmod(0o755)
        self.kodi = self.tmp / "kodi-active"
        systemctl = self.tmp / "systemctl"
        systemctl.write_text(f"#!/bin/sh\ntest -e '{self.kodi}'\n")
        systemctl.chmod(0o755)
        env = {"DEDE_LIB": str(LIB), "DEDE_UPDATE_SOURCE": str(self.tmp / "source.conf"),
               "DEDE_UPDATE_STATE": str(self.tmp / "update.conf"), "DEDE_UPDATE_KEYS": str(keys),
               "DEDE_VERSION_FILE": str(self.tmp / "version"), "DEDE_CMDLINE": str(self.cmdline),
               "DEDE_BOOT_DIR": str(self.boot), "DEDE_UPDATE_REBOOT": str(fake),
               "DEDE_SYSTEMCTL": str(systemctl), "DEDE_HEALTH_WAIT": "0.2",
               "DEDE_HEALTH_DEADLINE": "1", "DEDE_EFI_DIR": str(self.tmp)}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in old.items()])
        loader = importlib.machinery.SourceFileLoader("dede_update", str(SCRIPT))
        self.update = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(self.update)

    def publish(self, release, image=b"ISO" * 1000, key=None, **override):
        (self.www / "dede.iso").write_bytes(image)
        manifest = {"version": release, "iso": "dede.iso", "sha256": hashlib.sha256(image).hexdigest(),
                    "size": len(image), **override}
        data = json.dumps(manifest).encode()
        (self.www / "release.json").write_bytes(data)
        subprocess.run(["openssl", "pkeyutl", "-sign", "-inkey", str(key or self.key), "-rawin",
                        "-in", str(self.www / "release.json"), "-out", str(self.www / "release.json.sig")],
                       check=True, capture_output=True)

    def state(self):
        return self.update.read_state()

    def boot_into(self, slot):
        self.cmdline.write_text(f"{CMDLINE} findiso=/images/{slot}.iso\n")

    def test_versions_sort_like_semver(self):
        key = self.update.version_key
        ordered = ["0.1.0-test.1", "0.1.0-test.2", "0.1.0-test.10", "0.1.0", "0.1.1", "v1.0.0"]
        self.assertEqual(sorted(ordered, key=key), ordered)
        self.assertIsNone(key("latest"))

    def test_check_finds_a_newer_signed_release(self):
        self.publish("0.1.0")
        self.assertEqual(self.update.check(), 0)
        self.assertEqual(self.state()["UPDATE_STATE"], "current")
        self.publish("0.2.0-test.1")
        self.assertEqual(self.update.check(), 0)
        self.assertEqual(self.state()["UPDATE_STATE"], "available")
        self.assertEqual(self.state()["UPDATE_AVAILABLE"], "0.2.0-test.1")
        self.assertEqual(self.state()["UPDATE_CURRENT"], "0.1.0")

    def test_foreign_signature_or_bad_manifest_is_ignored(self):
        self.publish("0.2.0", key=self.other)
        self.update.check()
        self.assertEqual(self.state()["UPDATE_STATE"], "current")
        for bad in ({"iso": "../etc/passwd"}, {"sha256": "x"}, {"size": 0}, {"version": "neu"}):
            with self.subTest(bad=bad):
                self.publish("0.2.0", **bad)
                self.update.check()
                self.assertEqual(self.state()["UPDATE_STATE"], "current")

    def test_install_writes_the_inactive_slot_and_tries_it_once(self):
        self.publish("0.2.0")
        self.assertEqual(self.update.install(), 0)
        self.assertEqual((self.boot / "images/a.iso").read_bytes(), b"ISO" * 1000)
        self.assertEqual(dede_grubenv.read(self.boot / "grubenv"), {"dede_try": "a"})
        self.assertEqual(json.loads((self.boot / "slots.json").read_text()), {"a": "0.2.0"})
        self.assertEqual(self.state()["UPDATE_PENDING"], "a:0.2.0")
        self.assertTrue(self.reboots.exists())

        # GRUB started slot a; the TV is healthy, so it is confirmed.
        self.boot_into("a")
        self.kodi.touch()
        self.assertEqual(self.update.confirm(), 0)
        self.assertEqual(dede_grubenv.read(self.boot / "grubenv")["dede_good"], "a")
        self.assertEqual(self.state()["UPDATE_STATE"], "current")
        self.assertEqual(self.state()["UPDATE_CURRENT"], "0.2.0")
        self.assertNotIn("UPDATE_PENDING", self.state())

        # The next update goes to b while a keeps running.
        self.publish("0.3.0", image=b"NEW" * 1000)
        self.update.install()
        self.assertEqual((self.boot / "images/b.iso").read_bytes(), b"NEW" * 1000)
        self.assertEqual((self.boot / "images/a.iso").read_bytes(), b"ISO" * 1000)
        self.assertEqual(dede_grubenv.read(self.boot / "grubenv"), {"dede_good": "a", "dede_try": "b"})

        # b did not start: GRUB fell back to a, the console learns about it.
        self.update.confirm()
        self.assertEqual(self.state()["UPDATE_STATE"], "failed")
        self.assertEqual(self.state()["UPDATE_MESSAGE"], "update-did-not-start")
        self.assertEqual(dede_grubenv.read(self.boot / "grubenv")["dede_good"], "a")

    def test_unhealthy_update_restarts_to_the_previous_image(self):
        self.publish("0.2.0")
        self.update.install()
        self.reboots.unlink()
        self.boot_into("a")
        self.assertEqual(self.update.confirm(), 1)
        self.assertTrue(self.reboots.exists())
        self.assertNotIn("dede_good", dede_grubenv.read(self.boot / "grubenv"))

    def test_corrupt_download_is_not_tried(self):
        self.publish("0.2.0")
        (self.www / "dede.iso").write_bytes(b"ISX" * 1000)
        self.assertEqual(self.update.install(), 1)
        self.assertEqual(self.state()["UPDATE_MESSAGE"], "bad-image")
        self.assertFalse((self.boot / "images/a.iso").exists())
        self.assertEqual(dede_grubenv.read(self.boot / "grubenv"), {})
        self.assertFalse(self.reboots.exists())

    def test_bios_machines_do_not_install(self):
        self.publish("0.2.0")
        os.environ["DEDE_EFI_DIR"] = str(self.tmp / "no-efi")
        loader = importlib.machinery.SourceFileLoader("dede_update_bios", str(SCRIPT))
        bios = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(bios)
        self.assertEqual(bios.install(), 1)
        self.assertEqual(self.state()["UPDATE_MESSAGE"], "needs-uefi")
        self.assertFalse((self.boot / "images/a.iso").exists())

    def test_factory_start_after_failed_update_is_reported(self):
        self.publish("0.2.0")
        self.update.install()
        self.assertEqual(self.update.confirm(), 0)
        self.assertEqual(self.state()["UPDATE_MESSAGE"], "update-did-not-start")


if __name__ == "__main__":
    unittest.main()
