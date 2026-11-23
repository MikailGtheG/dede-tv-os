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

    def __init__(self, broken=None, serial=None, pause_flag=None):
        self.colours = e2e.channel_check.channel_colours()
        self.index = 0
        self.list_open = False
        self.highlight = 0
        self.home = False
        self.broken = broken
        self.frames = 0
        self.crash_at = None
        self.down = False
        self.entry = ""
        self.serial = serial
        self.pause_flag = pause_flag
        self.outage = False
        self.recover_at = None
        self.hang_at = None

    def tick(self):
        """Called per screendump: CI guest crash trigger and source outage."""
        if self.crash_at is None and ORDER[self.index] == "teskilat" and not self.list_open \
                and not self.home:
            self.crash_at = self.frames + 2
        if self.crash_at is not None and self.frames == self.crash_at:
            self.down = True
        if self.down and self.frames == self.crash_at + 3:
            self.down = False
            if self.broken == "restart-loses-channel":
                self.index = 0
        if self.pause_flag and os.path.exists(self.pause_flag):
            self.outage, self.recover_at = True, None
        elif self.outage and self.broken != "no-recovery":
            if self.recover_at is None:
                self.recover_at = self.frames + 2
            if self.frames >= self.recover_at:
                self.outage = False
                if self.broken != "no-watchdog":
                    self.hang_at = self.frames + 4
        if self.hang_at is not None and self.frames == self.hang_at:
            self.down = True
        if self.hang_at is not None and self.frames == self.hang_at + 2:
            self.down, self.hang_at = False, None
        self.frames += 1

    def key(self, name):
        if self.list_open:
            if name == "down":
                self.highlight = min(self.highlight + 1, len(ORDER) - 1)
            elif name == "ret":
                self.index, self.list_open = self.highlight, False
            elif name == "esc":
                self.list_open = False
            return
        if self.home:
            if name == "ret" and self.broken != "home-list-empty":
                self.home, self.index = False, 0
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
            self.entry += name
            numbers = {"1": 0, "2": 1, "3": 2, "4": 3, "6": 4}
            if self.entry in numbers:
                self.index, self.entry = numbers[self.entry], ""
            elif self.entry == "90":
                self.entry, self.home = "", True
                if self.serial:
                    with open(self.serial, "a") as log:
                        log.write("DEDE_GUARD: channel id 7 not playing (external stream unavailable)\n")

    def frame(self):
        self.tick()
        bg = bytes((16, 17, 20))
        panel = bytes((30, 31, 34))
        white = bytes((240, 240, 240))
        on_home = self.home or self.down or self.outage
        video = bg if on_home else bytes(self.colours[ORDER[self.index]])
        panel_w = int(W * 0.43)
        cap_x0, cap_x1 = int(W * 0.55), int(W * 0.75)
        rows = []
        for y in range(H):
            in_band = H * 0.03 < y < H * 0.97
            if self.list_open and self.broken == "list-fullscreen":
                rows.append(panel * W)
                continue
            show_panel = (self.list_open or (self.home or self.outage)) and in_band
            left = panel_w if show_panel else 0
            row = panel * left + video * (W - left)
            if not self.down and H * 0.45 < y < H * 0.55:
                # Centred caption (channel name) or the home screen message.
                row = row[:cap_x0 * 3] + white * (cap_x1 - cap_x0) + row[cap_x1 * 3:]
            if self.home and self.broken == "dialog-on-dead" and H * 0.31 < y < H * 0.69:
                x0, x1 = int(W * 0.26), int(W * 0.74)
                row = row[:x0 * 3] + bytes((32, 35, 40)) * (x1 - x0) + row[x1 * 3:]
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
            tv.serial = os.path.join(tmp, "serial.log")
            tv.pause_flag = os.path.join(tmp, "outage")
            Path(tv.serial).write_text("")
            stop = threading.Event()
            ready = threading.Event()
            thread = threading.Thread(target=serve, args=(sock, tv, stop, ready), daemon=True)
            thread.start()
            self.assertTrue(ready.wait(5))
            try:
                with patch.object(e2e.time, "sleep"), \
                     patch.object(e2e, "FIRST_PICTURE_TIMEOUT", 0), \
                     patch.object(e2e, "SWITCH_TIMEOUT", 0), \
                     patch.object(e2e, "RESTART_TIMEOUT", 3000), \
                     patch.object(e2e, "HANG_TIMEOUT", 3000), \
                     patch.object(e2e, "SERIAL_LOG", tv.serial), \
                     patch.object(e2e, "PAUSE_FLAG", tv.pause_flag), \
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

    def test_restart_to_wrong_channel_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="restart-loses-channel")))
        self.assertTrue(steps["number-6-switches"])
        self.assertFalse(steps["crash-restart-resumes-06"])

    def test_kodi_dialog_on_dead_channel_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="dialog-on-dead")))
        self.assertTrue(steps["crash-restart-resumes-06"])
        self.assertFalse(steps["dead-channel-shows-home"])

    def test_empty_home_list_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="home-list-empty")))
        self.assertFalse(steps["home-list-plays-01"])

    def test_missing_outage_recovery_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="no-recovery")))
        self.assertTrue(steps["outage-detected"])
        self.assertFalse(steps["outage-recovers-01"])

    def test_missing_hang_recovery_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="no-watchdog")))
        self.assertTrue(steps["outage-recovers-01"])
        self.assertFalse(steps["hang-watchdog-recovers-01"])

    def test_wait_for_rejects_transient_stock_kodi_before_valid_tv(self):
        # A later valid frame must not hide a momentary kiosk escape.
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp, "frame.ppm")
            ppm.write_bytes(FakeTv().frame())
            guest = type("StubGuest", (), {
                "label": "sim", "shots": 0,
                "capture": lambda self, step: ppm,
            })()
            stock = "stock Kodi home screen"
            with patch.object(e2e, "judge", side_effect=[
                    (False, {"channel": None}, stock),
                    (True, {"channel": "trt1"}, None)] ) as judge, \
                 patch.object(e2e, "log_preview"), \
                 patch("builtins.print"):
                self.assertFalse(e2e.wait_for(guest, "autostart", "trt1"))
                self.assertEqual(judge.call_count, 1)

    def test_wait_for_allows_transient_blank_then_tv(self):
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp, "frame.ppm")
            ppm.write_bytes(FakeTv().frame())
            guest = type("StubGuest", (), {
                "label": "sim", "shots": 1,
                "capture": lambda self, step: ppm,
            })()
            with patch.object(e2e, "judge", side_effect=[
                    (False, {"channel": None}, "blank or black QEMU frame"),
                    (True, {"channel": "trt1"}, None)]), \
                 patch.object(e2e, "log_preview"), \
                 patch.object(e2e.time, "sleep"), \
                 patch("builtins.print"):
                self.assertTrue(e2e.wait_for(guest, "autostart", "trt1"))

    def test_restart_rejects_transient_stock_kodi_before_recovery(self):
        # A subsequent valid channel must not hide a brief Kodi menu.
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp, "frame.ppm")
            ppm.write_bytes(FakeTv().frame())
            guest = type("StubGuest", (), {
                "label": "sim", "shots": 1,
                "capture": lambda self, step: ppm,
            })()
            with patch.object(e2e, "judge", side_effect=[
                    (False, {"channel": None}, "stock Kodi home screen"),
                    (True, {"channel": "trt1"}, None)]) as judge, \
                 patch.object(e2e, "log_preview"), \
                 patch("builtins.print"):
                self.assertFalse(e2e.wait_restart(guest, "trt1", timeout=10))
                self.assertEqual(judge.call_count, 1)

    def test_restart_rejects_wrong_live_channel_immediately(self):
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp, "frame.ppm")
            ppm.write_bytes(FakeTv().frame())
            guest = type("StubGuest", (), {
                "label": "sim", "shots": 1,
                "capture": lambda self, step: ppm,
            })()
            with patch.object(e2e, "judge", side_effect=[
                    (False, {"channel": "atv"}, None),
                    (True, {"channel": "trt1"}, None)]) as judge, \
                 patch.object(e2e, "log_preview"), \
                 patch("builtins.print"):
                self.assertFalse(e2e.wait_restart(guest, "trt1", timeout=10))
                self.assertEqual(judge.call_count, 1)

    def test_preview_png_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp, "f.ppm")
            ppm.write_bytes(FakeTv().frame())
            png = e2e.preview_png(ppm, width_out=160)
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertIn(b"IEND", png[-12:])


if __name__ == "__main__":
    unittest.main()
