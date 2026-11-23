#!/usr/bin/env python3
"""Identify the playing test channel on a QEMU screendump (PPM).

Each IPTV test channel (tests/iptv/channels.json) has a distinct background
hue. The text is centred, so the outer screen regions show the pure colour.

  check_qemu_channel.py classify SHOT.ppm
  check_qemu_channel.py expect   SHOT.ppm CHANNEL_ID
  check_qemu_channel.py overlay  SHOT.ppm CHANNEL_ID
  check_qemu_channel.py home     SHOT.ppm

"expect" requires the right-hand video area to show CHANNEL_ID. "overlay"
additionally requires the left-hand channel-list panel to cover the video
there, i.e. the list is open while the picture stays visible on the right.
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from check_qemu_tv_visual import read_ppm  # noqa: E402

# Video area that the vision's left-side channel list never covers.
VIDEO_RECT = (0.80, 0.08, 0.95, 0.22)
# Channel list panel area (left third, vertically central).
PANEL_RECT = (0.06, 0.35, 0.30, 0.65)
# Home ("no picture") screen: skin background colour, list panel on the left.
HOME_BACKGROUND = (16, 17, 20)
HOME_CORNER_RECT = (0.60, 0.82, 0.95, 0.95)
# Empty strip between list panel and message text; a centred Kodi dialog
# would always cover it.
HOME_GAP_RECT = (0.455, 0.32, 0.515, 0.68)
MAX_DISTANCE = 45.0
MIN_MARGIN = 1.6


def channel_colours():
    config = json.loads((HERE / "iptv" / "channels.json").read_text(encoding="utf-8"))
    return {c["id"]: tuple(int(c["color"][i:i + 2], 16) for i in (2, 4, 6))
            for c in config["channels"] + config.get("virtual_channels", [])}


def mean_colour(width, height, pixels, rect, step=3):
    x0, y0, x1, y1 = rect
    total = [0, 0, 0]
    count = 0
    for y in range(int(height * y0), int(height * y1), step):
        for x in range(int(width * x0), int(width * x1), step):
            offset = (y * width + x) * 3
            for i in range(3):
                total[i] += pixels[offset + i]
            count += 1
    if not count:
        raise ValueError("empty sample region")
    return tuple(value / count for value in total)


def distance(sample, reference):
    # Players may output studio range (16-235) on a full-range display, so
    # accept the closer of the full-range and limited-range rendition.
    limited = tuple(16 + value * 219 / 255 for value in reference)
    return min(
        sum((a - b) ** 2 for a, b in zip(sample, candidate)) ** 0.5
        for candidate in (reference, limited)
    )


def rgb_distance(a, b):
    """Plain distance for skin-drawn (not video) pixels."""
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def classify_colour(sample):
    ranked = sorted((distance(sample, colour), channel)
                    for channel, colour in channel_colours().items())
    (best, channel), (second, _) = ranked[0], ranked[1]
    if best > MAX_DISTANCE or second < best * MIN_MARGIN:
        return None, best
    return channel, best


def analyse(path):
    width, height, pixels = read_ppm(path)
    video = mean_colour(width, height, pixels, VIDEO_RECT)
    panel = mean_colour(width, height, pixels, PANEL_RECT)
    channel, video_distance = classify_colour(video)
    panel_distance = (distance(panel, channel_colours()[channel])
                      if channel else None)
    corner = mean_colour(width, height, pixels, HOME_CORNER_RECT)
    gap = mean_colour(width, height, pixels, HOME_GAP_RECT)
    home = (channel is None
            and rgb_distance(corner, HOME_BACKGROUND) < 12
            and rgb_distance(gap, HOME_BACKGROUND) < 12
            and rgb_distance(panel, HOME_BACKGROUND) > 8)
    return {
        "home": home,
        "channel": channel,
        "video_rgb": [round(v) for v in video],
        "video_distance": round(video_distance, 1),
        "panel_rgb": [round(v) for v in panel],
        "panel_distance_to_channel": None if panel_distance is None else round(panel_distance, 1),
    }


def main(argv):
    if len(argv) < 2 or argv[0] not in {"classify", "expect", "overlay", "home"}:
        print(__doc__, file=sys.stderr)
        return 2
    result = analyse(Path(argv[1]))
    print("DEDE_E2E:", json.dumps(result, sort_keys=True))
    if argv[0] == "classify":
        return 0
    if argv[0] == "home":
        return 0 if result["home"] else 1
    expected = argv[2]
    if result["channel"] != expected:
        print(f"DEDE_E2E: expected channel {expected}, screen shows {result['channel']}", file=sys.stderr)
        return 1
    if argv[0] == "overlay" and result["panel_distance_to_channel"] < MAX_DISTANCE:
        print("DEDE_E2E: channel list overlay not visible on the left", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
