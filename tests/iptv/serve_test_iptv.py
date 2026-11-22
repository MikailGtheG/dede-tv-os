#!/usr/bin/env python3
"""Local live-IPTV test source for Dede TV integration tests.

Serves, from pre-rendered loops (render_test_streams.py):
  /dede-test.m3u            M3U playlist (5 IPTV channels of the vision line-up)
  /dede-test-faults.m3u     same plus fault channels for error-state tests
  /epg.xml                  XMLTV guide with hourly test programmes
  /live/<id>/index.m3u8     live HLS media playlist, wall-clock driven
  /live/<id>/<seq>.ts       segment for media sequence <seq>

The media sequence is derived from wall-clock time, so a channel keeps
"broadcasting" while nobody watches it, exactly like real live TV. Loop
wrap-arounds are marked with EXT-X-DISCONTINUITY.

Fault channels: "http404" answers every request with 404; "stalled" serves
a media playlist that never advances (frozen source). While the file given
with --pause-flag exists, every request fails with 503 (network outage).

Test fixture only. Never installed into the appliance image.
"""
import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

HERE = Path(__file__).resolve().parent
WINDOW = 5
ROUTE_PLAYLIST = re.compile(r"^/live/([a-z0-9]{1,32})/index\.m3u8$")
ROUTE_SEGMENT = re.compile(r"^/live/([a-z0-9]{1,32})/([0-9]{1,12})\.ts$")


class Lineup:
    def __init__(self, media_root: Path, base_url: str, clock=time.time):
        config = json.loads((HERE / "channels.json").read_text(encoding="utf-8"))
        self.segment = int(config["segment_seconds"])
        self.loop = int(config["loop_segments"])
        self.channels = {c["id"]: c for c in config["channels"]}
        self.faults = {c["id"]: c for c in config["fault_channels"]}
        self.media_root = media_root
        self.base_url = base_url.rstrip("/")
        self.clock = clock
        self.frozen_sequence = {}

    def m3u(self, with_faults=False):
        lines = [f'#EXTM3U x-tvg-url="{self.base_url}/epg.xml"']
        entries = list(self.channels.values())
        if with_faults:
            entries += list(self.faults.values())
        for channel in entries:
            group = "Störungstest" if channel["id"] in self.faults else "Dede TV Test"
            lines.append(
                f'#EXTINF:-1 tvg-id="{channel["id"]}.dede.test" tvg-chno="{channel["number"]}" '
                f'tvg-name="{channel["name"]}" group-title="{group}",{channel["name"]}'
            )
            lines.append(f'{self.base_url}/live/{channel["id"]}/index.m3u8')
        return "\n".join(lines) + "\n"

    def current_sequence(self):
        return int(self.clock() // self.segment)

    def media_playlist(self, channel_id):
        if channel_id in self.faults:
            if self.faults[channel_id]["fault"] != "stalled":
                return None
            last = self.frozen_sequence.setdefault(channel_id, self.current_sequence())
        elif channel_id in self.channels:
            last = self.current_sequence()
        else:
            return None
        first = max(0, last - WINDOW + 1)
        lines = [
            "#EXTM3U",
            "#EXT-X-VERSION:3",
            f"#EXT-X-TARGETDURATION:{self.segment}",
            f"#EXT-X-MEDIA-SEQUENCE:{first}",
            f"#EXT-X-DISCONTINUITY-SEQUENCE:{first // self.loop}",
        ]
        for sequence in range(first, last + 1):
            if sequence != first and sequence % self.loop == 0:
                lines.append("#EXT-X-DISCONTINUITY")
            lines.append(f"#EXTINF:{self.segment:.3f},")
            lines.append(f"{sequence}.ts")
        return "\n".join(lines) + "\n"

    def segment_path(self, channel_id, sequence):
        source = self.channels.get(channel_id)
        if source is None:
            stalled = self.faults.get(channel_id)
            if not stalled or stalled["fault"] != "stalled":
                return None
            # A frozen source still serves the segments it already listed.
            source = next(iter(self.channels.values()))
        if sequence > self.current_sequence():
            return None
        return self.media_root / source["id"] / f"seg_{sequence % self.loop:03d}.ts"

    def epg(self):
        start = datetime.fromtimestamp(self.clock(), timezone.utc).replace(minute=0, second=0, microsecond=0)
        start -= timedelta(hours=2)
        out = ['<?xml version="1.0" encoding="UTF-8"?>', '<tv generator-info-name="dede-test">']
        for channel in self.channels.values():
            out.append(f'  <channel id={quoteattr(channel["id"] + ".dede.test")}>'
                       f'<display-name>{escape(channel["name"])}</display-name></channel>')
        for channel in self.channels.values():
            for hour in range(26):
                begin = start + timedelta(hours=hour)
                end = begin + timedelta(hours=1)
                out.append(
                    f'  <programme start="{begin:%Y%m%d%H%M%S} +0000" stop="{end:%Y%m%d%H%M%S} +0000" '
                    f'channel={quoteattr(channel["id"] + ".dede.test")}>'
                    f'<title lang="de">Testsendung {begin:%H}:00</title></programme>'
                )
        out.append("</tv>")
        return "\n".join(out) + "\n"


def make_handler(lineup: Lineup, pause_flag=None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DedeTestIPTV/1"

        def log_message(self, fmt, *args):
            sys.stderr.write("IPTV_TEST: " + (fmt % args) + "\n")

        def send_body(self, body: bytes, content_type: str):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            if not getattr(self, "head_only", False):
                self.wfile.write(body)

        def do_HEAD(self):
            # IPTV Simple probes cached EPG/playlist files with HEAD.
            self.head_only = True
            try:
                self.do_GET()
            finally:
                self.head_only = False

        def do_GET(self):
            if pause_flag is not None and pause_flag.exists():
                return self.send_error(503)
            path = self.path.split("?", 1)[0]
            if path == "/dede-test.m3u":
                return self.send_body(lineup.m3u().encode(), "audio/x-mpegurl")
            if path == "/dede-test-faults.m3u":
                return self.send_body(lineup.m3u(with_faults=True).encode(), "audio/x-mpegurl")
            if path == "/epg.xml":
                return self.send_body(lineup.epg().encode(), "application/xml")
            match = ROUTE_PLAYLIST.match(path)
            if match:
                playlist = lineup.media_playlist(match.group(1))
                if playlist is not None:
                    return self.send_body(playlist.encode(), "application/vnd.apple.mpegurl")
            match = ROUTE_SEGMENT.match(path)
            if match:
                segment = lineup.segment_path(match.group(1), int(match.group(2)))
                if segment is not None and segment.is_file():
                    return self.send_body(segment.read_bytes(), "video/mp2t")
            self.send_error(404)

    return Handler


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("media_root", type=Path)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8089)
    parser.add_argument("--base-url", help="URL clients use, e.g. http://10.0.2.2:8089 for a QEMU guest")
    parser.add_argument("--pause-flag", type=Path, help="simulate an outage while this file exists")
    args = parser.parse_args(argv)
    base_url = args.base_url or f"http://{args.bind}:{args.port}"
    lineup = Lineup(args.media_root.resolve(), base_url)
    server = ThreadingHTTPServer((args.bind, args.port), make_handler(lineup, args.pause_flag))
    print(f"IPTV_TEST: serving {base_url}/dede-test.m3u", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
