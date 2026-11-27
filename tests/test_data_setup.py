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
LIB = ROOT / "build/config/includes.chroot/usr/local/lib/dede-tv"
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
               "DEDE_LIB": str(LIB),
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
                {"node": f"/dev/sda{i}", "start": s, "size": n} for i, (s, n) in enumerate(partitions, 1)]}})},
            "blockdev": {"": str(size)},
        }
        (self.tmp / "answers.json").write_text(json.dumps(self.answers))

    def test_plans_boot_and_data_partitions_behind_the_image_on_the_boot_disk(self):
        self.disk(medium="/dev/sda1", kind="part", pkname="sda")
        disk = self.setup.boot_disk()
        self.assertEqual(disk, "/dev/sda")
        kind, ((boot_label, boot_start, boot_size), (label, start, size)) = self.setup.plan(disk)
        self.assertEqual((kind, boot_label, label), ("dos", "dede-boot", "dede-data"))
        self.assertEqual(boot_start % 2048, 0)
        self.assertGreaterEqual(boot_start, 64 + 2278016)
        self.assertEqual(boot_size, 4 * GIB)
        self.assertEqual(start, boot_start + boot_size)
        self.assertEqual(start + size, 8 * GIB)

    def test_gpt_keeps_room_for_the_backup_table(self):
        self.disk(label="gpt")
        kind, layout = self.setup.plan(self.setup.boot_disk())
        self.assertEqual((kind, [label for label, _, _ in layout]), ("gpt", ["dede-boot", "dede-data"]))
        _, start, size = layout[-1]
        self.assertLessEqual(start + size, 8 * GIB - 34)
        self.assertEqual((start + size) % 2048, 0)

    def test_without_room_for_updates_only_the_data_partition_is_planned(self):
        self.disk(size=2278080 + 3 * GIB)
        _, layout = self.setup.plan(self.setup.boot_disk())
        self.assertEqual([label for label, _, _ in layout], ["dede-data"])
        self.disk(partitions=((64, 100), (200, 100), (2048, 2278016)))
        _, layout = self.setup.plan(self.setup.boot_disk())
        self.assertEqual([label for label, _, _ in layout], ["dede-data"])

    def test_skips_cd_existing_partition_small_disk_and_other_tables(self):
        for kwargs, reason in (
            ({"medium": "/dev/sr0", "kind": "rom"}, "CD"),
            ({"medium": "/dev/loop0", "kind": "loop"}, "image file"),
            ({"labels": "dede-data\n"}, "data partition"),
            ({"size": 2278080 + GIB // 2}, "not enough free space"),
            ({"label": "mac"}, "not supported"),
            ({"partitions": ((64, 100), (200, 100), (400, 100), (600, 100))}, "slot"),
        ):
            with self.subTest(kwargs=kwargs):
                self.disk(**kwargs)
                with self.assertRaises(self.setup.Skip) as caught:
                    self.setup.existing()
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


class GrubenvTests(unittest.TestCase):
    def setUp(self):
        import sys
        sys.path.insert(0, str(LIB))
        import dede_grubenv
        self.env = dede_grubenv
        self.path = Path(tempfile.mkdtemp()) / "grubenv"

    def test_block_is_1024_bytes_and_round_trips(self):
        self.env.write(self.path, {})
        data = self.path.read_bytes()
        self.assertEqual(len(data), 1024)
        self.assertTrue(data.startswith(b"# GRUB Environment Block\n###"))
        self.env.update(self.path, dede_try="b", dede_good="a")
        self.assertEqual(len(self.path.read_bytes()), 1024)
        self.assertEqual(self.env.read(self.path), {"dede_good": "a", "dede_try": "b"})
        self.env.update(self.path, dede_try="")
        self.assertEqual(self.env.read(self.path), {"dede_good": "a"})

    def test_rejects_values_grub_would_misread(self):
        for name, value in (("dede try", "a"), ("dede_try", "a\nb"), ("dede_try", "x" * 65)):
            with self.subTest(name=name, value=value), self.assertRaises(ValueError):
                self.env.write(self.path, {name: value})


if __name__ == "__main__":
    unittest.main()
