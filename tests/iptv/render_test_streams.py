#!/usr/bin/env python3
"""Render a short, seamlessly looping MPEG-TS segment set per test channel.

Each channel shows a distinct background colour, its channel number and
name in large type, and a running timestamp, with a distinct sine tone. The
live server (serve_test_iptv.py) turns these loops into time-advancing live
HLS playlists, so CI needs no long-running encoder next to QEMU.
"""
import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def render(channel, segment_seconds, loop_segments, output, font):
    out_dir = output / channel["id"]
    out_dir.mkdir(parents=True, exist_ok=True)
    fps = 25
    seconds = segment_seconds * loop_segments
    with tempfile.TemporaryDirectory() as scratch:
        # drawtext reads label text from files: no escaping of user text.
        title = Path(scratch, "title.txt")
        title.write_text(f"{channel['number']:02d}  {channel['name']}", encoding="utf-8")
        hint = Path(scratch, "hint.txt")
        hint.write_text("DEDE TV TESTSIGNAL", encoding="utf-8")
        video = (
            f"color=c={channel['color']}:s=1280x720:r={fps}:d={seconds},"
            f"drawtext=fontfile={font}:textfile={title}:fontsize=96:"
            f"fontcolor=white:x=(w-tw)/2:y=(h-th)/2-40,"
            f"drawtext=fontfile={font}:textfile={hint}:fontsize=32:"
            f"fontcolor=0xb8b8b8:x=(w-tw)/2:y=(h/2)+70,"
            f"drawtext=fontfile={font}:text='%{{pts\\:hms}}':fontsize=40:"
            f"fontcolor=0xd0d0d0:x=(w-tw)/2:y=h-120"
        )
        command = [
            "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", video,
            "-f", "lavfi", "-i", f"sine=frequency={channel['tone_hz']}:sample_rate=48000:duration={seconds}",
            "-c:v", "libx264", "-preset", "veryfast", "-profile:v", "main",
            "-pix_fmt", "yuv420p", "-b:v", "800k", "-maxrate", "1000k", "-bufsize", "2000k",
            "-g", str(fps * segment_seconds), "-keyint_min", str(fps * segment_seconds),
            "-sc_threshold", "0",
            "-c:a", "aac", "-b:a", "96k", "-ac", "2",
            "-f", "segment", "-segment_time", str(segment_seconds),
            "-segment_format", "mpegts", "-reset_timestamps", "0",
            str(out_dir / "seg_%03d.ts"),
        ]
        subprocess.run(command, check=True)
    produced = sorted(out_dir.glob("seg_*.ts"))
    if len(produced) != loop_segments:
        raise RuntimeError(f"{channel['id']}: expected {loop_segments} segments, got {len(produced)}")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", default=DEFAULT_FONT)
    args = parser.parse_args(argv)
    if not Path(args.font).is_file():
        parser.error(f"font not found: {args.font}")
    config = json.loads((HERE / "channels.json").read_text(encoding="utf-8"))
    for channel in config["channels"]:
        render(channel, config["segment_seconds"], config["loop_segments"], args.output, args.font)
        print(f"rendered {channel['number']:02d} {channel['name']}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
