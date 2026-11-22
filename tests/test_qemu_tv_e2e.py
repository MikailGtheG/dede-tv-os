#!/usr/bin/env python3
"""Self-test for qemu_tv_e2e.py against a simulated QEMU monitor + TV."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import socket
import tempfile
import threading
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
loader = importlib.machinery.SourceFileLoader("qemu_tv_e2e", str(HERE / "qemu_tv_e2e.py"))
spec = importlib.util.spec_from_loader(loader.name, loader)
e2e = importlib.util.module_from_spec(spec)
loader.exec_module(e2e)

ORDER = ["trt1", "atv", "kanald", "showtv", "teskilat"]
W, H = 640, 400


class FakeTv:
    """Minimal model of the intended TV behaviour, optionally broken."""

    def __init__(self, broken=None):
        self.colours = e2e.channel_check.channel_colours()
        self.index = 0
        self.list_open = False
        self.highlight = 0
        self.home = False
        self.broken = broken

    def key(self, name):
        if self.list_open:
            if name == "down":
                self.highlight = min(self.highlight + 1, len(ORDER) - 1)
            elif name == "ret":
                self.index, self.list_open = self.highlight, False
            elif name == "esc":
                self.list_open = False
            return
        if name == "pgup":
            self.index = min(self.index + 1, len(ORDER) - 1)
        elif name == "pgdn":
            self.index = max(self.index - 1, 0)
        elif name == "ret":
            self.list_open, self.highlight = True, self.index
        elif name == "esc" and self.broken == "back-leaves-tv":
            self.home = True
        elif name.isdigit() and self.broken != "no-number-entry":
            # Channel numbers: 1-4 then 6 (5 is the virtual channel).
            numbers = [1, 2, 3, 4, 6]
            if int(name) in numbers:
                self.index = numbers.index(int(name))

    def frame(self):
        video = bytes((20, 22, 26) if self.home else self.colours[ORDER[self.index]])
        panel = bytes((30, 31, 34))
        white = bytes((240, 240, 240))
        panel_w = int(W * 0.43)
        cap_x0, cap_x1 = int(W * 0.55), int(W * 0.75)
        rows = []
        for y in range(H):
            in_band = H * 0.03 < y < H * 0.97
            full_panel = self.list_open and self.broken == "list-fullscreen"
            if full_panel:
                rows.append(panel * W)
                continue
            left = panel_w if (self.list_open and in_band) else 0
            row = panel * left + video * (W - left)
            if not self.home and H * 0.45 < y < H * 0.55:
                # Centred caption, like the real test signal's channel name.
                row = row[:cap_x0 * 3] + white * (cap_x1 - cap_x0) + row[cap_x1 * 3:]
            rows.append(row)
        return f"P6\n{W} {H}\n255\n".encode() + b"".join(rows)


def serve(sock_path, tv, stop, ready):
    server = socket.socket(socket.AF_UNIX)
    server.bind(sock_path)
    server.listen(4)
    ready.set()
    server.settimeout(0.2)
    while not stop.is_set():
        try:
            conn, _ = server.accept()
        except (TimeoutError, socket.timeout):
            continue
        with conn:
            line = conn.recv(4096).decode().strip()
            command, _, arg = line.partition(" ")
            if command == "sendkey":
                tv.key(arg)
            elif command == "screendump":
                Path(arg).write_bytes(tv.frame())
            conn.sendall(b"(qemu) ")
    server.close()


def _fast_clock():
    """Each call advances 30 s, so any wait loop ends after one retry."""
    now = 0.0
    while True:
        now += 30.0
        yield now


class E2ESelfTest(unittest.TestCase):
    def run_against(self, tv):
        with tempfile.TemporaryDirectory() as tmp:
            sock = os.path.join(tmp, "mon.sock")
            stop = threading.Event()
            ready = threading.Event()
            thread = threading.Thread(target=serve, args=(sock, tv, stop, ready), daemon=True)
            thread.start()
            self.assertTrue(ready.wait(5))
            try:
                with patch.object(e2e.time, "sleep"), \
                     patch.object(e2e, "FIRST_PICTURE_TIMEOUT", 0), \
                     patch.object(e2e, "SWITCH_TIMEOUT", 0), \
                     patch.object(e2e.time, "monotonic", side_effect=_fast_clock()), \
                     patch("builtins.print"):
                    guest = e2e.Guest(sock, os.path.join(tmp, "shots"), "sim")
                    guest.out.mkdir()
                    return e2e.run(guest)
            finally:
                stop.set()
                thread.join()

    def test_correct_tv_passes_all_steps(self):
        steps = self.run_against(FakeTv())
        self.assertEqual(len(steps), e2e.TOTAL_STEPS)
        self.assertTrue(all(ok for _, ok in steps), steps)

    def test_back_leaving_tv_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="back-leaves-tv")))
        self.assertFalse(steps["back-stays-on-tv"])

    def test_list_hiding_picture_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="list-fullscreen")))
        self.assertFalse(steps["ok-opens-list"])

    def test_missing_number_entry_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="no-number-entry")))
        self.assertFalse(steps["number-6-switches"])

    def test_preview_png_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp, "f.ppm")
            ppm.write_bytes(FakeTv().frame())
            png = e2e.preview_png(ppm, width_out=160)
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertIn(b"IEND", png[-12:])


if __name__ == "__main__":
    unittest.main()
