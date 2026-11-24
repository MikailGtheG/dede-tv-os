#!/usr/bin/env python3
"""End-to-end TV test against a running QEMU guest (P0 acceptance).

Drives the guest only through its virtual keyboard (QEMU monitor
"sendkey") and judges every step from real screen captures:

  1. TV starts by itself and shows channel 01 (no key pressed)
  2. CH+ (Page Up) twice -> 02 -> 03, CH- (Page Down) -> 02
  3. Back on live TV keeps the picture (no Kodi navigation)
  4. OK opens the channel list over the still-visible picture
  5. Down + OK switches to the highlighted channel and closes the list
  6. OK then Back closes the list without switching
  7. Digit 6 switches directly to channel 06
  8. After a Kodi crash (killed by the CI guest) channel 06 returns alone
  9. Dead channel 90: Dede no-picture screen, guard notices, OK plays 01
 10. Source outage (HTTP 503): picture interrupted, returns alone
 11. Kodi hung (SIGSTOP by the CI guest): watchdog restart, 01 returns
 12. Local 24/7 channel 05: plays at the scheduled position, continues
     with the next film, and its time runs on while the viewer is away
 13. No capture shows a stock Kodi dialog or splash

Usage: qemu_tv_e2e.py MONITOR_SOCKET OUTPUT_DIR LABEL
Prints DEDE_E2E lines; exit status 0 only if every step passed.
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_qemu_channel as channel_check  # noqa: E402
from check_qemu_tv_visual import classify as stock_kodi_check, read_ppm  # noqa: E402

FIRST_PICTURE_TIMEOUT = int(os.environ.get("DEDE_E2E_FIRST_PICTURE_TIMEOUT", "240"))
TOTAL_STEPS = 22
HANG_TIMEOUT = int(os.environ.get("DEDE_E2E_HANG_TIMEOUT", "420"))
PAUSE_FLAG = os.environ.get("DEDE_E2E_PAUSE_FLAG", "/tmp/dede-iptv-outage")
SERIAL_LOG = os.environ.get("DEDE_E2E_SERIAL_LOG", "/dev/null")
RESTART_TIMEOUT = int(os.environ.get("DEDE_E2E_RESTART_TIMEOUT", "240"))
SWITCH_TIMEOUT = int(os.environ.get("DEDE_E2E_SWITCH_TIMEOUT", "60"))


class Guest:
    def __init__(self, monitor, out_dir, label):
        self.monitor = monitor
        self.out = Path(out_dir)
        self.label = label
        self.shots = 0

    def hmp(self, command):
        with socket.socket(socket.AF_UNIX) as sock:
            sock.settimeout(5)
            sock.connect(self.monitor)
            sock.sendall(command.encode() + b"\n")
            try:
                sock.recv(4096)
            except (TimeoutError, socket.timeout):
                pass

    def key(self, name, settle=1.5):
        print(f"DEDE_E2E: {self.label}: key {name}", flush=True)
        self.hmp(f"sendkey {name}")
        time.sleep(settle)

    def capture(self, name):
        self.shots += 1
        path = self.out / f"{self.label}-{self.shots:02d}-{name}.ppm"
        path.unlink(missing_ok=True)
        self.hmp(f"screendump {path}")
        deadline = time.monotonic() + 10
        while True:
            try:
                read_ppm(path)
                return path
            except (OSError, ValueError):
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"screendump {path} not completed") from None
                time.sleep(0.2)


def preview_png(ppm, width_out=480):
    """Downscaled PNG for the job log (artifacts may be unavailable)."""
    width, height, pixels = read_ppm(ppm)
    height_out = max(1, height * width_out // width)
    raw = bytearray()
    for y in range(height_out):
        raw.append(0)
        sy = y * height // height_out
        for x in range(width_out):
            offset = (sy * width + x * width // width_out) * 3
            raw += pixels[offset:offset + 3]

    def chunk(kind, data):
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width_out, height_out, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


def log_preview(guest, ppm, step):
    data = base64.b64encode(preview_png(ppm)).decode()
    print(f"DEDE_E2E_PNG: {guest.label}/{step} {data}", flush=True)


def no_stock_kodi(ppm):
    try:
        stock_kodi_check(ppm)
    except SystemExit as exc:  # classify() exits on stock Kodi UI
        return str(exc)
    except ValueError as exc:
        return str(exc)
    return None


def judge(ppm, expected, mode):
    result = channel_check.analyse(ppm)
    stock = no_stock_kodi(ppm)
    if mode == "home":
        return stock is None and result["home"], result, stock
    if mode == "not":
        # A stock Kodi dialog is not a valid Dede TV outage state.
        # An outage must remove the expected picture, not silently switch to
        # another valid TV channel (which would mask a playback regression).
        return stock is None and result["channel"] is None, result, stock
    ok = stock is None and result["channel"] == expected
    panel = result["panel_distance_to_channel"]
    if ok and mode == "overlay":
        ok = panel is not None and panel >= channel_check.MAX_DISTANCE
    if ok and mode == "fullscreen":
        ok = panel is not None and panel < channel_check.MAX_DISTANCE
    return ok, result, stock


def wait_for(guest, step, expected, mode="fullscreen", timeout=None, interval=4):
    timeout = SWITCH_TIMEOUT if timeout is None else timeout
    started = time.monotonic()
    while True:
        ppm = guest.capture(step)
        ok, result, stock = judge(ppm, expected, mode)
        elapsed = time.monotonic() - started
        # Never allow a transient stock Kodi screen to pass unnoticed while
        # waiting for a later valid TV frame. Blank restart frames are normal.
        if stock is not None and not stock.startswith((
                "blank or black QEMU frame", "uniform QEMU frame")):
            print(f"DEDE_E2E: {guest.label}: FAIL {step} exposed stock Kodi UI: {stock}",
                  flush=True)
            log_preview(guest, ppm, f"{step}-stock-kodi")
            return False
        # During a source outage, switching to another playable channel is
        # a failure even if the original source disappears on a later frame.
        if mode == "not" and result["channel"] is not None and result["channel"] != expected:
            print(f"DEDE_E2E: {guest.label}: FAIL {step} switched to "
                  f"{result['channel']} instead of interrupting {expected}", flush=True)
            log_preview(guest, ppm, f"{step}-wrong-channel")
            return False
        if ok or elapsed >= timeout:
            status = "PASS" if ok else "FAIL"
            print(f"DEDE_E2E: {guest.label}: {status} {step} expected={expected} mode={mode} "
                  f"after={elapsed:.1f}s result={json.dumps(result, sort_keys=True)}"
                  + (f" stock_kodi={stock}" if stock else ""), flush=True)
            log_preview(guest, ppm, step)
            return ok
        os.unlink(ppm)
        guest.shots -= 1
        time.sleep(interval)


def run(guest):
    steps = []

    def check(step, ok):
        steps.append((step, ok))
        return ok

    if not check("autostart-channel-01", wait_for(guest, "autostart", "trt1",
                                                  timeout=FIRST_PICTURE_TIMEOUT, interval=6)):
        return steps  # nothing further can be judged without a picture
    # Let the channel banner fade so it does not cover the sample regions.
    time.sleep(8)
    guest.key("pgup")
    check("ch-plus-to-02", wait_for(guest, "ch-plus-02", "atv"))
    guest.key("pgup")
    check("ch-plus-to-03", wait_for(guest, "ch-plus-03", "kanald"))
    guest.key("pgdn")
    check("ch-minus-to-02", wait_for(guest, "ch-minus-02", "atv"))
    time.sleep(6)
    guest.key("esc", settle=4)
    check("back-stays-on-tv", wait_for(guest, "back-on-tv", "atv", timeout=8))
    guest.key("ret", settle=2)
    check("ok-opens-list", wait_for(guest, "list-open", "atv", mode="overlay", timeout=20))
    guest.key("down")
    guest.key("ret")
    check("list-ok-switches-to-03", wait_for(guest, "list-select-03", "kanald"))
    time.sleep(6)
    guest.key("ret", settle=2)
    check("ok-reopens-list", wait_for(guest, "list-reopen", "kanald", mode="overlay", timeout=20))
    guest.key("esc", settle=2)
    check("back-closes-list", wait_for(guest, "list-closed", "kanald", timeout=15))
    time.sleep(6)
    # Direct channel number entry switches by itself after a short pause.
    guest.key("6", settle=0.5)
    if not check("number-6-switches", wait_for(guest, "number-06", "teskilat")):
        return steps
    # The CI guest hard-kills Kodi once channel 06 plays (crash/power-loss
    # simulation). Kodi must restart and return to the last channel alone.
    if not check("crash-restart-resumes-06", wait_restart(guest, "teskilat")):
        return steps
    # State C: a dead channel (90 answers 404) must end on the Dede
    # no-picture screen, never on a Kodi dialog, and the remote stays usable.
    time.sleep(6)
    guest.key("9", settle=0.3)
    guest.key("0", settle=1)
    check("dead-channel-shows-home", wait_for(guest, "dead-90", None, mode="home", timeout=90))
    check("guard-detects-unavailable", serial_contains(
        "DEDE_GUARD: channel id", "external stream unavailable", timeout=30))
    guest.key("ret", settle=1)
    check("home-list-plays-01", wait_for(guest, "home-ok-01", "trt1"))
    # Network outage: the source answers 503 until the flag is removed.
    time.sleep(6)
    flag = Path(PAUSE_FLAG)
    try:
        flag.touch()
        outage = wait_for(guest, "outage", "trt1", mode="not", timeout=120)
        time.sleep(10)
    finally:
        # Do not poison later tests when capture/monitor operations fail.
        flag.unlink(missing_ok=True)
    check("outage-detected", outage)
    if not check("outage-recovers-01", wait_for(guest, "outage-recovered", "trt1", timeout=240)):
        return steps
    # The CI guest now freezes Kodi (SIGSTOP): process alive, picture stuck.
    # The watchdog must restart it and channel 01 must return by itself.
    if not check("hang-watchdog-recovers-01", wait_restart(guest, "trt1", timeout=HANG_TIMEOUT)):
        return steps
    # P2: local 24/7 channel "Dede Film" on 05 (three 40 s films).
    time.sleep(6)
    guest.key("5", settle=0.5)
    if not check("virtual-05-plays", wait_for(guest, "virtual-05", "dedefilm", timeout=90)):
        return steps
    # Programme guide: Kodi's "now" for 05 is the film the core schedules.
    check("virtual-epg-now-next", serial_contains("DEDE_CI_EPG: ok", timeout=120))
    # 120 s: a hand-over just before a film ends is skipped by the checker;
    # the next film's hand-over then decides.
    check("virtual-position-matches-schedule", virtual_positions_ok(minimum=1, timeout=120))
    # The running film ends within 40 s; the next one must follow by itself.
    time.sleep(45)
    continued = serial_contains("DEDE_GUARD:", "programme item ended, continuing", timeout=60)
    check("virtual-continues-next-film",
          continued and wait_for(guest, "virtual-next-film", "dedefilm", timeout=60))
    # Leave the channel and come back: its time must have kept running.
    before = serial_count("DEDE_CI_VIRTUAL: ok")
    guest.key("pgup")
    away = wait_for(guest, "virtual-away-06", "teskilat")
    time.sleep(25)
    guest.key("pgdn")
    back = wait_for(guest, "virtual-back-05", "dedefilm")
    check("virtual-time-runs-while-away",
          away and back and virtual_positions_ok(minimum=before + 1, timeout=120))
    return steps


def serial_count(needle):
    try:
        text = Path(SERIAL_LOG).read_bytes().decode("utf-8", "replace")
    except OSError:
        return 0
    return sum(1 for line in text.splitlines() if needle in line)


def virtual_positions_ok(minimum, timeout=60):
    """The CI guest compares Kodi's position with the core schedule on
    every local-channel start; require enough "ok" lines and no mismatch."""
    deadline = time.monotonic() + timeout
    while True:
        if serial_count("DEDE_CI_VIRTUAL: mismatch"):
            print("DEDE_E2E: serial: local channel position does not match its schedule", flush=True)
            serial_contains("DEDE_CI_VIRTUAL: mismatch", timeout=0)
            return False
        if serial_count("DEDE_CI_VIRTUAL: ok") >= minimum:
            serial_contains("DEDE_CI_VIRTUAL: ok", timeout=0)
            return True
        if time.monotonic() >= deadline:
            print(f"DEDE_E2E: serial: fewer than {minimum} local channel position checks", flush=True)
            return False
        time.sleep(2)


def serial_contains(*needles, timeout=30):
    """The guest's serial console (QEMU -serial) shows the guard's events."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            text = Path(SERIAL_LOG).read_bytes().decode("utf-8", "replace")
            for line in text.splitlines():
                if all(needle in line for needle in needles):
                    print(f"DEDE_E2E: serial: {line.strip()[:200]}", flush=True)
                    return True
        except OSError:
            pass
        if time.monotonic() >= deadline:
            print(f"DEDE_E2E: serial: missing {needles}", flush=True)
            return False
        time.sleep(2)


def wait_restart(guest, expected, timeout=None):
    timeout = RESTART_TIMEOUT if timeout is None else timeout
    started = time.monotonic()
    interrupted = False
    while time.monotonic() - started < timeout:
        ppm = guest.capture("restart")
        ok, result, stock = judge(ppm, expected, "fullscreen")
        # Blank/uniform captures are expected while Kodi is restarting.
        # They still count as interrupted playback and must recover before
        # the deadline; only an actual stock Kodi UI is an immediate failure.
        if stock is not None and not stock.startswith((
                "blank or black QEMU frame", "uniform QEMU frame")):
            print(f"DEDE_E2E: {guest.label}: FAIL restart exposed stock Kodi UI: {stock}",
                  flush=True)
            log_preview(guest, ppm, "restart-stock-kodi")
            return False
        # A different live channel is not a valid restart interruption:
        # accepting it would hide an unintended channel switch on recovery.
        if result["channel"] is not None and result["channel"] != expected:
            print(f"DEDE_E2E: {guest.label}: FAIL restart switched to "
                  f"{result['channel']} instead of {expected}", flush=True)
            log_preview(guest, ppm, "restart-wrong-channel")
            return False
        if not ok and not interrupted:
            interrupted = True
            print(f"DEDE_E2E: {guest.label}: picture interrupted (Kodi restarting) "
                  f"after={time.monotonic() - started:.1f}s", flush=True)
            log_preview(guest, ppm, "restart-interrupted")
        elif ok and interrupted:
            print(f"DEDE_E2E: {guest.label}: PASS restart expected={expected} "
                  f"after={time.monotonic() - started:.1f}s result={json.dumps(result, sort_keys=True)}",
                  flush=True)
            log_preview(guest, ppm, "restart-resumed")
            return True
        os.unlink(ppm)
        guest.shots -= 1
        time.sleep(3)
    ppm = guest.capture("restart-final")
    print(f"DEDE_E2E: {guest.label}: FAIL restart expected={expected} "
          f"interrupted={interrupted} after={timeout}s "
          f"result={json.dumps(channel_check.analyse(ppm), sort_keys=True)}", flush=True)
    log_preview(guest, ppm, "restart-final")
    return False


def main(argv):
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    guest = Guest(*argv)
    guest.out.mkdir(parents=True, exist_ok=True)
    try:
        steps = run(guest)
    except (OSError, RuntimeError) as exc:
        print(f"DEDE_E2E: {guest.label}: ERROR {type(exc).__name__}: {exc}", flush=True)
        return 1
    passed = sum(ok for _, ok in steps)
    print(f"DEDE_E2E_SUMMARY: {guest.label}: {passed}/{len(steps)} passed: "
          + ", ".join(f"{name}={'ok' if ok else 'FAIL'}" for name, ok in steps), flush=True)
    return 0 if len(steps) == TOTAL_STEPS and all(ok for _, ok in steps) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
