"""Resolve a Dede TV local 24/7 channel to "the film running now, at the right spot".

Called by Kodi when the viewer switches to a local channel (IPTV Simple
stream URL plugin://plugin.video.dede-virtual/?channel=NAME). The schedule
lives in dede-tv-core; this resolver only asks it which file plays now and
where, and hands Kodi that file with a resume point. Nothing runs while
nobody watches, and nothing is transcoded.
"""
import json
import os
import subprocess
import sys
import urllib.parse

CORE = "/usr/local/bin/dede-tv-core"
MEDIA_ROOT = "/srv/dede-tv/media"
HOME = os.environ.get("HOME", "/home/dede")
DB = os.path.join(HOME, ".local", "share", "dede-tv", "channels.db")
DURATIONS = os.path.join(HOME, ".local", "share", "dede-tv", "virtual-durations.json")


def current_playback(name, run=subprocess.run):
    """Return (file, position_seconds) from dede-tv-core, or raise ValueError."""
    if not name or len(name.encode()) > 92 or any(ord(c) < 0x20 for c in name):
        raise ValueError("invalid channel name")
    result = run([CORE, "--db", DB, "request-virtual", name, MEDIA_ROOT],
                 capture_output=True, text=True, timeout=8, check=False)
    if result.returncode != 0:
        raise ValueError("channel not available")
    request = json.loads(result.stdout)
    params = request["params"]
    path = params["item"]["file"]
    # dede-tv-core already validated the file against the approved media root.
    if not isinstance(path, str) or not path.startswith(MEDIA_ROOT + "/"):
        raise ValueError("unexpected media path")
    resume = params["options"]["resume"]
    position = (int(resume["hours"]) * 3600 + int(resume["minutes"]) * 60
                + int(resume["seconds"]) + int(resume["milliseconds"]) / 1000.0)
    return path, position


def duration_seconds(path, position):
    """Kodi honours a resume point only with a total time; use the measured one."""
    try:
        with open(DURATIONS, encoding="utf-8") as source:
            cache = json.load(source)
        for key, value in cache.items():
            if key.split("|", 1)[0] == path and isinstance(value, int) and value > 0:
                return value / 1000.0
    except (OSError, ValueError, AttributeError):
        pass
    return position + 1.0


def channel_from_argv(argv):
    query = urllib.parse.parse_qs(argv[2].lstrip("?")) if len(argv) > 2 else {}
    values = query.get("channel", [])
    return values[0] if len(values) == 1 else ""


def main(argv):
    import xbmc
    import xbmcgui
    import xbmcplugin
    handle = int(argv[1])
    name = channel_from_argv(argv)
    try:
        path, position = current_playback(name)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        xbmc.log(f"DEDE_VIRTUAL: cannot resolve local channel ({type(error).__name__})", xbmc.LOGWARNING)
        xbmcplugin.setResolvedUrl(handle, False, xbmcgui.ListItem())
        return
    item = xbmcgui.ListItem(label=name, path=path)
    tag = item.getVideoInfoTag()
    tag.setTitle(name)
    if position >= 1.0:
        tag.setResumePoint(position, duration_seconds(path, position))
    xbmc.log(f"DEDE_VIRTUAL: {name} -> {os.path.basename(path)} @ {position:.1f}s", xbmc.LOGINFO)
    xbmcplugin.setResolvedUrl(handle, True, item)


if __name__ == "__main__":
    main(sys.argv)
