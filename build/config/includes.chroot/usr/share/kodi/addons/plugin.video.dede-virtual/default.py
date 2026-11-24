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
import time
import urllib.parse

CORE = "/usr/local/bin/dede-tv-core"
MEDIA_ROOT = "/srv/dede-tv/media"
HOME = os.environ.get("HOME", "/home/dede")
DB = os.path.join(HOME, ".local", "share", "dede-tv", "channels.db")
DURATIONS = os.path.join(HOME, ".local", "share", "dede-tv", "virtual-durations.json")


NEAR_END_S = 3.0


def current_playback(name, run=subprocess.run, clock=time.monotonic):
    """Return (file, position_seconds) from dede-tv-core, or raise ValueError."""
    if not name or len(name.encode()) > 92 or any(ord(c) < 0x20 for c in name):
        raise ValueError("invalid channel name")
    started = clock()
    result = run([CORE, "--db", DB, "request-virtual", name, MEDIA_ROOT],
                 capture_output=True, text=True, timeout=8, check=False)
    # Process start-up dominates the call and the core reads the clock near
    # its end, so hand Kodi the position for now: add the whole call time.
    latency = max(0.0, clock() - started)
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
    return path, position + latency


def cached_duration(path):
    """Measured length in seconds from dede-virtual-sync's cache, or None."""
    try:
        with open(DURATIONS, encoding="utf-8") as source:
            cache = json.load(source)
        for key, value in cache.items():
            if key.split("|", 1)[0] == path and isinstance(value, int) and value > 0:
                return value / 1000.0
    except (OSError, ValueError, AttributeError):
        pass
    return None


def duration_seconds(path, position):
    """Kodi honours a resume point only with a total time; use the measured one."""
    length = cached_duration(path)
    return length if length is not None else position + 1.0


def resolve(name, current=current_playback, sleep=time.sleep):
    """(file, position); a film about to end is skipped to its successor.

    Opening a file within its last seconds fails in Kodi (error dialog), so
    wait for the transition and ask the core again.
    """
    path, position = current(name)
    length = cached_duration(path)
    if length is not None and length - position < NEAR_END_S:
        remaining = max(0.0, length - position)
        sleep(remaining + 0.3)
        path, position = current(name)
    return path, position


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
        path, position = resolve(name)
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
