#!/usr/bin/env python3
"""dede-data-setup: only the boot disk, only with free space, never twice."""
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "build/config/includes.chroot/usr/local/libexec/dede-data-setup"
CMDLINE = "boot=live components persistence persistence-label=dede-data quiet\n"
GIB = (1 << 30) // 512


class DataSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: os.system(f"rm -rf '{self.tmp}'"))
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.calls = self.tmp / "calls"
        (self.tmp / "cmdline").write_text(CMDLINE)
        self.answers = {}
        for name in ("findmnt", "lsblk", "sfdisk", "blockdev", "partx", "udevadm", "mkfs.ext4",
                     "mount", "umount", "cp", "reboot"):
            tool = self.bin / name
            tool.write_text(
                "#!/usr/bin/env python3\nimport json, sys\n"
                f"open({str(self.calls)!r}, 'a').write(' '.join(sys.argv) + '\\n')\n"
                f"answers = json.load(open({str(self.tmp / 'answers.json')!r}))\n"
                "key = ' '.join(sys.argv[1:])\n"
                "for prefix, reply in answers.get(sys.argv[0].rsplit('/', 1)[-1], {}).items():\n"
                "    if key.startswith(prefix):\n        print(reply)\n        break\n")
            tool.chmod(0o755)
        env = {"PATH": f"{self.bin}:{os.environ['PATH']}", "DEDE_CMDLINE": str(self.tmp / "cmdline"),
               "DEDE_DATA_REBOOT": str(self.bin / "reboot")}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        self.addCleanup(lambda: [os.environ.pop(k) if v is None else os.environ.__setitem__(k, v)
                                 for k, v in old.items()])
        loader = importlib.machinery.SourceFileLoader("dede_data_setup", str(SCRIPT))
        spec = importlib.util.spec_from_loader(loader.name, loader)
        self.setup = importlib.util.module_from_spec(spec)
        loader.exec_module(self.setup)

    def disk(self, medium="/dev/sda", kind="disk", pkname=None, labels="", size=8 * GIB,
             partitions=((64, 2278016), (540, 6656)), label="dos"):
        self.answers = {
            "findmnt": {"": medium},
            "lsblk": {"-J": json.dumps({"blockdevices": [{"name": medium[5:], "type": kind,
                                                          "pkname": pkname}]}),
                      "-n -o LABEL": labels},
            "sfdisk": {"-J": json.dumps({"partitiontable": {"label": label, "partitions": [
                {"start": s, "size": n} for s, n in partitions]}})},
            "blockdev": {"": str(size)},
        }
        (self.tmp / "answers.json").write_text(json.dumps(self.answers))

    def test_plans_a_partition_behind_the_image_on_the_boot_disk(self):
        self.disk(medium="/dev/sda1", kind="part", pkname="sda")
        disk = self.setup.boot_disk()
        self.assertEqual(disk, "/dev/sda")
        start, size, number = self.setup.plan(disk)
        self.assertEqual((start % 2048, number), (0, 3))
        self.assertGreaterEqual(start, 64 + 2278016)
        self.assertEqual(start + size, 8 * GIB)

    def test_skips_cd_existing_partition_small_disk_and_other_tables(self):
        for kwargs, reason in (
            ({"medium": "/dev/sr0", "kind": "rom"}, "CD"),
            ({"labels": "dede-data\n"}, "data partition"),
            ({"size": 2278080 + GIB // 2}, "not enough free space"),
            ({"label": "gpt"}, "not supported"),
            ({"partitions": ((64, 100), (200, 100), (400, 100), (600, 100))}, "slot"),
        ):
            with self.subTest(kwargs=kwargs):
                self.disk(**kwargs)
                with self.assertRaises(self.setup.Skip) as caught:
                    self.setup.plan(self.setup.boot_disk())
                self.assertIn(reason, str(caught.exception))

    def test_image_without_persistence_option_is_left_alone(self):
        (self.tmp / "cmdline").write_text("boot=live components quiet\n")
        self.disk()
        with self.assertRaises(self.setup.Skip):
            self.setup.boot_disk()
        self.assertEqual(self.setup.main(), 0)
        self.assertFalse(self.calls.exists())

    def test_nothing_is_written_when_skipping(self):
        self.disk(labels="dede-data\n")
        self.assertEqual(self.setup.main(), 0)
        written = [c for c in self.calls.read_text().splitlines()
                   if c.split()[0].rsplit("/", 1)[-1] in ("partx", "mkfs.ext4", "mount", "reboot")
                   or "--append" in c]
        self.assertEqual(written, [])

    def test_persistence_conf_keeps_settings_films_and_sign_ins(self):
        conf = self.setup.persistence_conf()
        for line in ("/etc/dede-tv source=etc-dede-tv", "/var/lib/dede-tv-admin source=var-lib-dede-tv-admin",
                     "/srv/dede-tv source=srv-dede-tv", "/var/lib/tailscale source=var-lib-tailscale",
                     "/etc/wireguard source=etc-wireguard", "/home/dede source=home-dede"):
            self.assertIn(line + "\n", conf)
        self.assertNotIn("union", conf)


if __name__ == "__main__":
    unittest.main()
