"""Publish the administration's setup code and address to the skin."""
import re

import xbmc
import xbmcgui

CODE_FILE = "/run/dede-tv-admin/setup-code"
PORT = 8080
HOME_WINDOW = 10000


def read_code(path=CODE_FILE):
    """'1234 5678' while a valid code is published, else ''."""
    try:
        with open(path, encoding="ascii") as handle:
            code = handle.read(16).strip()
    except (OSError, UnicodeDecodeError):
        return ""
    if not re.fullmatch(r"[0-9]{8}", code):
        return ""
    return code[:4] + " " + code[4:]


def address(ip):
    if not ip or ip.startswith("127.") or ip == "0.0.0.0":
        return ""
    host = f"[{ip}]" if ":" in ip else ip
    return f"http://{host}:{PORT}"


def run():
    monitor = xbmc.Monitor()
    home = xbmcgui.Window(HOME_WINDOW)
    shown = None
    while not monitor.abortRequested():
        code = read_code()
        url = address(xbmc.getIPAddress()) if code else ""
        if (code, url) != shown:
            home.setProperty("dede.setup.url", url)
            home.setProperty("dede.setup.code", code)
            shown = (code, url)
        if monitor.waitForAbort(3):
            break
    home.clearProperty("dede.setup.code")
    home.clearProperty("dede.setup.url")


if __name__ == "__main__":
    run()
