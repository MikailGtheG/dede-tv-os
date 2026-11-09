#!/usr/bin/env python3
"""Offline checks for the actual BIOS/UEFI screenshot rejection gate."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).with_name("check_qemu_tv_visual.py")
spec = importlib.util.spec_from_file_location("qemu_visual_gate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def write_frame(path, *, stock_dialog=False, stock_logo=False, cyan_accent=False):
    width, height = 320, 240
    pixels = bytearray(bytes((23, 25, 28)) * width * height)
    # Real UI has some detail; a perfectly flat frame is a stalled renderer.
    pixels[(80 * width + 80) * 3:(80 * width + 80) * 3 + 3] = bytes((35, 37, 40))
    cyan = bytes((21, 122, 150))
    if stock_dialog:
        # A stock modal has an opaque body below the colored title bar.
        for y in range(int(height * .39), int(height * .58)):
            for x in range(int(width * .27), int(width * .72)):
                offset = (y * width + x) * 3
                pixels[offset:offset + 3] = bytes((10, 12, 14))
    if stock_dialog or cyan_accent:
        for y in range(int(height * .34), int(height * .385)):
            for x in range(int(width * .27), int(width * .72)):
                offset = (y * width + x) * 3
                pixels[offset:offset + 3] = cyan
    if stock_logo:
        for y in range(int(height * .02), int(height * .075)):
            for x in range(int(width * .04), int(width * .085)):
                offset = (y * width + x) * 3
                pixels[offset:offset + 3] = cyan
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode() + pixels)


class TvVisualGateTests(unittest.TestCase):
    def test_expected_charcoal_dede_screen_is_not_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dede.ppm"
            write_frame(path)
            self.assertLess(module.classify(path)["popup_cyan"], .35)

    def test_stock_kodi_addon_enable_modal_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stock-dialog.ppm"
            write_frame(path, stock_dialog=True)
            with self.assertRaisesRegex(ValueError, "stock Kodi add-on enable"):
                module.classify(path)

    def test_cyan_accent_without_modal_body_is_not_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "accent.ppm"
            write_frame(path, cyan_accent=True)
            self.assertGreater(module.classify(path)["popup_cyan"], .35)

    def test_stock_kodi_logo_is_rejected_without_dialog(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "stock-logo.ppm"
            write_frame(path, stock_logo=True)
            with self.assertRaisesRegex(ValueError, "stock Kodi branding"):
                module.classify(path)

    def test_black_renderer_frame_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "black.ppm"
            path.write_bytes(b"P6\n320 240\n255\n" + bytes(320 * 240 * 3))
            with self.assertRaisesRegex(ValueError, "blank or black QEMU frame"):
                module.classify(path)

    def test_dark_but_visible_screen_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "dark.ppm"
            pixels = bytearray(bytes((12, 14, 16)) * (320 * 240))
            pixels[(80 * 320 + 80) * 3:(80 * 320 + 80) * 3 + 3] = bytes((20, 22, 24))
            path.write_bytes(b"P6\n320 240\n255\n" + pixels)
            module.classify(path)

    def test_bright_uniform_frame_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "uniform.ppm"
            path.write_bytes(b"P6\n320 240\n255\n" + bytes((90, 100, 110)) * (320 * 240))
            with self.assertRaisesRegex(ValueError, "uniform QEMU frame"):
                module.classify(path)

    def test_incomplete_ppm_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "truncated.ppm"
            path.write_bytes(b"P6\n320 240\n255\n\x00\x00\x00")
            with self.assertRaisesRegex(ValueError, "incomplete"):
                module.classify(path)


if __name__ == "__main__":
    unittest.main()
