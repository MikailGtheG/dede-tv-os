#!/usr/bin/env python3
"""Regression tests for the QEMU screenshot channel classifier."""
import importlib.machinery
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parent / "check_qemu_channel.py"
loader = importlib.machinery.SourceFileLoader("check_qemu_channel", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)

W, H = 320, 240


def write_ppm(path, background, panel=None):
    rows = []
    for y in range(H):
        row = bytearray()
        for x in range(W):
            colour = background
            if panel and x < W * 0.4 and 0.05 * H < y < 0.95 * H:
                colour = panel
            row += bytes(colour)
        rows.append(bytes(row))
    path.write_bytes(f"P6\n{W} {H}\n255\n".encode() + b"".join(rows))


def limited(colour):
    return tuple(round(16 + value * 219 / 255) for value in colour)


class ChannelClassifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.colours = module.channel_colours()

    def tearDown(self):
        self.tmp.cleanup()

    def shot(self, name, background, panel=None):
        path = self.dir / f"{name}.ppm"
        write_ppm(path, background, panel)
        return path

    def test_every_channel_identified_full_and_limited_range(self):
        for channel, colour in self.colours.items():
            for variant, rgb in (("full", colour), ("limited", limited(colour))):
                with self.subTest(channel=channel, range=variant):
                    path = self.shot(f"{channel}-{variant}", rgb)
                    self.assertEqual(module.main(["expect", str(path), channel]), 0)

    def test_channel_colours_are_mutually_distinct(self):
        items = list(self.colours.items())
        for i, (a, ca) in enumerate(items):
            for b, cb in items[i + 1:]:
                with self.subTest(a=a, b=b):
                    self.assertGreater(module.distance(ca, cb), 2 * module.MAX_DISTANCE)

    def test_wrong_channel_and_non_video_screens_fail(self):
        trt1 = self.shot("trt1", self.colours["trt1"])
        self.assertEqual(module.main(["expect", str(trt1), "atv"]), 1)
        for name, rgb in (("black", (0, 0, 0)), ("kodi-home", (20, 24, 28)),
                          ("estuary-cyan", (18, 160, 220))):
            with self.subTest(screen=name):
                self.assertIsNone(module.analyse(self.shot(name, rgb))["channel"])

    def test_overlay_requires_panel_over_left_video(self):
        atv = self.colours["atv"]
        open_list = self.shot("list", atv, panel=(38, 38, 40))
        self.assertEqual(module.main(["overlay", str(open_list), "atv"]), 0)
        closed = self.shot("closed", atv)
        self.assertEqual(module.main(["overlay", str(closed), "atv"]), 1)
        # A full-screen Kodi window is not "list over running TV picture".
        fullscreen_menu = self.shot("menu", (38, 38, 40))
        self.assertEqual(module.main(["overlay", str(fullscreen_menu), "atv"]), 1)


if __name__ == "__main__":
    unittest.main()
