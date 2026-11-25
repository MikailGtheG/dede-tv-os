#!/usr/bin/env python3
"""Render the local 24/7 test films ("Dede Film", channel 05) for CI images.

Short parts (40 s) make film changes happen inside the E2E run. Each part
shows the channel colour, number/name, its part number and its own running
time, so a capture also reveals which film and position plays.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def render(channel, output, font, size):
    output.mkdir(parents=True, exist_ok=True)
    seconds = channel["part_seconds"]
    for part in range(1, channel["parts"] + 1):
        with tempfile.TemporaryDirectory() as scratch:
            title = Path(scratch, "title.txt")
            title.write_text(f"{channel['number']:02d}  {channel['name']}", encoding="utf-8")
            sub = Path(scratch, "sub.txt")
            sub.write_text(f"FILM {part} VON {channel['parts']}", encoding="utf-8")
            video = (
                f"color=c={channel['color']}:s={size}:r=25:d={seconds},"
                f"drawtext=fontfile={font}:textfile={title}:fontsize=h/7.5:fontcolor=white:x=(w-tw)/2:y=h*0.36,"
                f"drawtext=fontfile={font}:textfile={sub}:fontsize=h/22:fontcolor=0xf0e0e8:x=(w-tw)/2:y=h*0.62,"
                f"drawtext=fontfile={font}:text='%{{pts\\:hms}}':fontsize=h/18:fontcolor=0xf0e0e8:x=(w-tw)/2:y=h*0.80"
            )
            target = output / f"film-{part:02d}.mkv"
            subprocess.run([
                "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                "-f", "lavfi", "-i", video,
                "-f", "lavfi", "-i", f"sine=frequency={channel['tone_hz']}:sample_rate=48000:duration={seconds}",
                "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-b:v", "500k",
                "-g", "50", "-keyint_min", "50", "-sc_threshold", "0",
                "-c:a", "aac", "-b:a", "64k", "-shortest", str(target)], check=True)
            print(f"rendered {target.name}")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", default=DEFAULT_FONT)
    parser.add_argument("--size", default="640x360")
    parser.add_argument("--set", default="virtual_channels", choices=("virtual_channels", "admin_channels"),
                        help="admin_channels: films the E2E uploads through the console")
    args = parser.parse_args(argv)
    config = json.loads((HERE / "channels.json").read_text(encoding="utf-8"))
    for channel in config.get(args.set, []):
        render(channel, args.output, args.font, args.size)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
