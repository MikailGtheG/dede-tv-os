#!/usr/bin/env python3
"""Self-test for qemu_tv_e2e.py against a simulated QEMU monitor + TV."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.machinery
import importlib.util
import os
import secrets
import urllib.parse
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

ORDER = ["trt1", "atv", "kanald", "showtv", "dedefilm", "teskilat"]
W, H = 640, 400


class FakeAdmin(BaseHTTPRequestHandler):
    """The administration console's HTTP contract (dede-tv-web/admin)."""
    code = "24681357"

    def log_message(self, *args):
        pass

    def reply(self, status, text="", location=None, cookies=()):
        self.send_response(status)
        if location:
            self.send_header("Location", location)
        for name, value in cookies:
            self.send_header("Set-Cookie", f"{name}={value}; Path=/; HttpOnly; SameSite=Strict")
        self.end_headers()
        self.wfile.write(text.encode())

    def cookies(self):
        pairs = (p.strip().split("=", 1) for p in (self.headers.get("Cookie") or "").split(";") if "=" in p)
        return dict(pairs)

    def do_GET(self):
        state = self.server.state
        session = self.cookies().get("dede_session")
        if self.path == "/setup" and state["password"] is None:
            return self.reply(200, "Einrichtungscode vom Fernseher", cookies=[("dede_form", "f" * 64)])
        if self.path == "/login":
            return self.reply(200, "Anmelden", cookies=[("dede_form", "f" * 64)])
        if session in state["sessions"]:
            if self.path == "/":
                title = "Yönetim" if state.get("language") == "tr" else "Verwaltung"
                return self.reply(200, f'{title} <input type="hidden" name="csrf" value="{"c" * 64}">')
            if self.path == "/channels":
                return self.reply(200, " ".join(state["channels"]))
            if self.path == "/channels/7":
                return self.reply(200, " ".join(state["films"]))
            if self.path.startswith("/apply/"):
                return self.reply(200, '<p class="msg ok" role="status">Fertig.</p>')
        return self.reply(303, location="/login" if state["password"] else "/setup")

    def do_POST(self):
        state = self.server.state
        if self.headers.get("Origin") != f"http://127.0.0.1:{self.server.server_port}":
            return self.reply(403)
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        form = dict(urllib.parse.parse_qsl(raw.decode("utf-8", "replace")))
        session = self.cookies().get("dede_session")
        if session in state["sessions"] and self.path not in ("/setup", "/login"):
            return self.signed_in_post(state, raw)
        if form.get("form") != self.cookies().get("dede_form"):
            return self.reply(403)
        if self.path == "/setup":
            if form.get("code") != self.code or state["broken"] == "admin-setup-fails":
                return self.reply(200, "Der Code stimmt nicht.")
            state["password"] = form["password"]
            state["on_password"]()
            session = secrets.token_hex(32)
            state["sessions"].add(session)
            return self.reply(200, "Das Passwort ist gespeichert.", cookies=[("dede_session", session)])
        if self.path == "/login":
            if form.get("password") != state["password"]:
                return self.reply(200, "Das Passwort stimmt nicht.")
            session = secrets.token_hex(32)
            state["sessions"].add(session)
            return self.reply(303, location="/", cookies=[("dede_session", session)])
        return self.reply(404)


def _signed_in_post(self, state, body):
    if self.path == "/channels/7/upload":
        ok = f'name="csrf"\r\n\r\n{"c" * 64}'.encode() in body and b'filename="Neuer Film.mkv"' in body
        if not ok or state["broken"] == "admin-upload-fails":
            return self.reply(303, location="/channels/7?n=failed")
        state["films"].append("Neuer Film.mkv")
        return self.reply(303, location="/channels/7?n=uploaded")
    form = dict(urllib.parse.parse_qsl(body.decode()))
    if form.get("csrf") != "c" * 64:
        return self.reply(403)
    if self.path == "/channels/new" and form.get("number") == "7":
        state["channels"].append(form["name"])
        return self.reply(303, location="/apply/" + "1" * 32 + "?back=/channels/7")
    if self.path == "/language" and form.get("language") == "tr":
        if state["broken"] != "language-ignored":
            state["language"] = "tr"
            state["on_apply"]("Turkish")
        return self.reply(303, location="/apply/" + "3" * 32 + "?back=/language")
    if self.path == "/apply":
        state["on_apply"]()
        return self.reply(303, location="/apply/" + "2" * 32 + "?back=/channels/7")
    return self.reply(404)


FakeAdmin.signed_in_post = _signed_in_post


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
        self.last_playing = None
        self.film_frames = 0
        self.admin = None
        self.admin_announced = False
        self.custom_ready = False
        self.custom = False
        self.apply_restart_at = None
        self.pending_language = None

    def serial_line(self, line):
        if self.serial:
            with open(self.serial, "a") as log:
                log.write(line + "\n")

    def tick(self):
        """Called per screendump: CI guest crash trigger and source outage."""
        if self.admin is not None:
            if not self.admin_announced:
                self.admin_announced = True
                self.serial_line("DEDE_CI_LANGUAGE: German")
                self.serial_line(f"DEDE_CI_ADMIN_CODE: {FakeAdmin.code}")
                if self.broken != "admin-code-not-shown":
                    self.serial_line("DEDE_CI_ADMIN: tv shows code url=http://10.0.2.15:8080")
        if self.apply_restart_at is not None and self.frames == self.apply_restart_at:
            self.down = True
        if self.apply_restart_at is not None and self.frames == self.apply_restart_at + 2:
            self.down, self.apply_restart_at = False, None
            self.custom_ready = self.broken != "admin-channel-missing"
            if self.pending_language:
                self.serial_line(f"DEDE_CI_LANGUAGE: {self.pending_language}")
                self.pending_language = None
        playing = ORDER[self.index] if not (self.home or self.down or self.outage) else None
        if playing == "dedefilm" and self.last_playing != "dedefilm":
            verdict = "mismatch" if self.broken == "virtual-wrong-position" else "ok"
            self.serial_line(f"DEDE_CI_VIRTUAL: {verdict} file=film-01.mkv drift=+0.4s")
            if self.broken != "no-epg":
                self.serial_line("DEDE_CI_EPG: ok now=film-01 next=film-02")
            self.film_frames = 0
        if playing == "dedefilm":
            self.film_frames += 1
            # The E2E's waits are patched out, so the guard's "next film"
            # event is emitted at once; the broken variant drops to Home.
            if self.film_frames == 1 and self.broken != "virtual-stops-after-film":
                self.serial_line("DEDE_GUARD: channel id 9: programme item ended, continuing")
            if self.film_frames == 2 and self.broken == "virtual-stops-after-film":
                self.home = True
        self.last_playing = playing
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
        if not name.isdigit():
            self.custom = False
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
            if self.entry == "07":
                self.entry, self.custom = "", self.custom_ready
                return
            numbers = {"1": 0, "2": 1, "3": 2, "4": 3, "5": 4, "6": 5}
            if self.entry in numbers:
                self.index, self.entry = numbers[self.entry], ""
            elif self.entry == "90":
                self.entry, self.home = "", True
                if self.serial:
                    with open(self.serial, "a") as log:
                        log.write("DEDE_GUARD: channel id 7 not playing (external stream unavailable)\n")

    def restart_after_apply(self, language=None):
        self.apply_restart_at = self.frames + 1
        self.pending_language = language

    def admin_card(self):
        """The setup card shows until the password is set."""
        return (self.admin is not None and self.admin_announced and self.admin["password"] is None
                and self.broken not in ("admin-code-not-shown", "admin-card-hidden"))

    def frame(self):
        self.tick()
        bg = bytes((16, 17, 20))
        panel = bytes((30, 31, 34))
        white = bytes((240, 240, 240))
        on_home = self.home or self.down or self.outage
        shown = "dedeneu" if self.custom else ORDER[self.index]
        video = bg if on_home else bytes(self.colours[shown])
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
            if self.admin_card() and H * 0.037 < y < H * 0.296:
                x0, x1 = int(W * 0.479), int(W * 0.771)
                row = row[:x0 * 3] + bytes((38, 30, 32)) * (x1 - x0) + row[x1 * 3:]
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
            admin = ThreadingHTTPServer(("127.0.0.1", 0), FakeAdmin)
            admin.state = tv.admin = {"password": None, "sessions": set(), "broken": tv.broken,
                                      "on_password": lambda: tv.serial_line("DEDE_CI_ADMIN: tv code cleared"),
                                      "channels": [], "films": [],
                                      "on_apply": tv.restart_after_apply}
            threading.Thread(target=admin.serve_forever, daemon=True).start()
            try:
                with patch.object(e2e.time, "sleep"), \
                     patch.object(e2e, "FIRST_PICTURE_TIMEOUT", 0), \
                     patch.object(e2e, "SWITCH_TIMEOUT", 0), \
                     patch.object(e2e, "RESTART_TIMEOUT", 3000), \
                     patch.object(e2e, "HANG_TIMEOUT", 3000), \
                     patch.object(e2e, "SERIAL_LOG", tv.serial), \
                     patch.object(e2e, "ADMIN_URL", f"http://127.0.0.1:{admin.server_port}"), \
                     patch.object(e2e, "UPLOAD_FILM", __file__), \
                     patch.object(e2e, "PAUSE_FLAG", tv.pause_flag), \
                     patch.object(e2e.time, "monotonic", side_effect=_fast_clock()), \
                     patch("builtins.print"):
                    guest = e2e.Guest(sock, os.path.join(tmp, "shots"), "sim")
                    guest.out.mkdir()
                    return e2e.run(guest)
            finally:
                admin.shutdown()
                admin.server_close()
                stop.set()
                thread.join()

    def test_correct_tv_passes_all_steps(self):
        steps = self.run_against(FakeTv())
        self.assertEqual(len(steps), e2e.TOTAL_STEPS)
        self.assertTrue(all(ok for _, ok in steps), steps)

    def test_failed_film_upload_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="admin-upload-fails")))
        self.assertTrue(steps["admin-create-channel-07"])
        self.assertFalse(steps["admin-upload-film"])

    def test_new_channel_missing_on_tv_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="admin-channel-missing")))
        self.assertTrue(steps["admin-send-to-tv"])
        self.assertTrue(steps["admin-tv-restarts-on-05"])
        self.assertFalse(steps["admin-channel-07-plays"])

    def test_language_not_reaching_the_tv_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="language-ignored")))
        self.assertTrue(steps["tv-language-german-by-default"])
        self.assertFalse(steps["admin-language-turkish"])
        self.assertFalse(steps["tv-language-turkish"])

    def test_setup_code_missing_on_tv_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="admin-code-not-shown")))
        self.assertTrue(steps["autostart-channel-01"])
        self.assertFalse(steps["admin-tv-shows-setup-code"])

    def test_setup_card_missing_on_screen_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="admin-card-hidden")))
        self.assertFalse(steps["admin-tv-shows-setup-code"])

    def test_failed_admin_setup_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="admin-setup-fails")))
        self.assertFalse(steps["admin-setup-via-web"])
        self.assertFalse(steps["admin-tv-hides-code"])
        self.assertFalse(steps["admin-sign-in"])

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

    def test_outage_rejects_transient_wrong_live_channel(self):
        # A later blank frame must not hide a brief switch to another station.
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp, "frame.ppm")
            ppm.write_bytes(FakeTv().frame())
            guest = type("StubGuest", (), {
                "label": "sim", "shots": 1,
                "capture": lambda self, step: ppm,
            })()
            with patch.object(e2e, "judge", side_effect=[
                    (False, {"channel": "atv"}, None),
                    (True, {"channel": None}, None)]) as judge, \
                 patch.object(e2e, "log_preview"), \
                 patch("builtins.print"):
                self.assertFalse(e2e.wait_for(
                    guest, "outage", "trt1", mode="not", timeout=10))
                self.assertEqual(judge.call_count, 1)

    def test_virtual_position_mismatch_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="virtual-wrong-position")))
        self.assertTrue(steps["virtual-05-plays"])
        self.assertFalse(steps["virtual-position-matches-schedule"])

    def test_missing_programme_guide_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="no-epg")))
        self.assertTrue(steps["virtual-05-plays"])
        self.assertFalse(steps["virtual-epg-now-next"])

    def test_virtual_channel_stopping_after_a_film_is_caught(self):
        steps = dict(self.run_against(FakeTv(broken="virtual-stops-after-film")))
        self.assertTrue(steps["virtual-05-plays"])
        self.assertFalse(steps["virtual-continues-next-film"])

    def test_preview_png_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            ppm = Path(tmp, "f.ppm")
            ppm.write_bytes(FakeTv().frame())
            png = e2e.preview_png(ppm, width_out=160)
            self.assertTrue(png.startswith(b"\x89PNG\r\n\x1a\n"))
            self.assertIn(b"IEND", png[-12:])


if __name__ == "__main__":
    unittest.main()
