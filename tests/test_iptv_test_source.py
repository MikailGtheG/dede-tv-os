#!/usr/bin/env python3
"""Regression tests for the local live-IPTV test source (no ffmpeg needed)."""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parent / "iptv" / "serve_test_iptv.py"
loader = importlib.machinery.SourceFileLoader("serve_test_iptv", str(SCRIPT))
spec = importlib.util.spec_from_loader(loader.name, loader)
module = importlib.util.module_from_spec(spec)
loader.exec_module(module)


class Clock:
    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


def media_lines(playlist):
    return [line for line in playlist.splitlines() if line.endswith(".ts")]


class LiveSourceTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock(1000.0)
        self.lineup = module.Lineup(Path("/media"), "http://10.0.2.2:8089/", clock=self.clock)

    def test_playlist_matches_vision_lineup(self):
        m3u = self.lineup.m3u()
        self.assertTrue(m3u.startswith('#EXTM3U x-tvg-url="http://10.0.2.2:8089/epg.xml"'))
        numbers = [int(part.split('"')[1]) for part in m3u.split("tvg-chno=")[1:]]
        # Number 5 is reserved for the virtual channel "Dede Film".
        self.assertEqual(numbers, [1, 2, 3, 4, 6])
        self.assertIn(",Teşkilat TV\n", m3u)
        self.assertNotIn("offline", m3u)
        self.assertIn("/live/offline/index.m3u8", self.lineup.m3u(with_faults=True))

    def test_live_window_advances_with_wall_clock(self):
        first = media_lines(self.lineup.media_playlist("kanald"))
        self.assertEqual(first, ["496.ts", "497.ts", "498.ts", "499.ts", "500.ts"])
        self.clock.now += 6
        self.assertEqual(media_lines(self.lineup.media_playlist("kanald"))[-1], "503.ts")
        self.assertIn("#EXT-X-MEDIA-SEQUENCE:499", self.lineup.media_playlist("kanald"))

    def test_loop_wrap_is_marked_discontinuous(self):
        playlist = self.lineup.media_playlist("kanald").splitlines()
        # Loop length 10: sequence 500 wraps to seg_000 and must be preceded by
        # exactly one discontinuity tag; 496..499 continue the previous loop.
        self.assertEqual(playlist.count("#EXT-X-DISCONTINUITY"), 1)
        self.assertEqual(playlist[playlist.index("500.ts") - 2], "#EXT-X-DISCONTINUITY")
        self.assertIn("#EXT-X-DISCONTINUITY-SEQUENCE:49", playlist)
        self.assertEqual(self.lineup.segment_path("kanald", 500), Path("/media/kanald/seg_000.ts"))
        self.assertEqual(self.lineup.segment_path("kanald", 497), Path("/media/kanald/seg_007.ts"))

    def test_future_and_unknown_segments_are_refused(self):
        self.assertIsNone(self.lineup.segment_path("kanald", 501))
        self.assertIsNone(self.lineup.segment_path("nope", 500))
        self.assertIsNone(self.lineup.media_playlist("nope"))

    def test_fault_channels(self):
        self.assertIsNone(self.lineup.media_playlist("offline"))
        stalled = self.lineup.media_playlist("stalled")
        self.clock.now += 60
        self.assertEqual(self.lineup.media_playlist("stalled"), stalled)

    def test_routes_reject_traversal(self):
        for path in ("/live/../etc/passwd", "/live/kanald/../../x.ts",
                     "/live/kanald/-1.ts", "/live/KANALD/index.m3u8"):
            with self.subTest(path=path):
                self.assertIsNone(module.ROUTE_PLAYLIST.match(path))
                self.assertIsNone(module.ROUTE_SEGMENT.match(path))

    def test_epg_is_valid_xmltv(self):
        import xml.etree.ElementTree as ET
        root = ET.fromstring(self.lineup.epg())
        self.assertEqual(len(root.findall("channel")), 5)
        self.assertEqual(len(root.findall("programme")), 5 * 26)


if __name__ == "__main__":
    unittest.main()
