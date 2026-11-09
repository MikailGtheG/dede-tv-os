#!/usr/bin/env python3
"""Fail integration if a QEMU capture still displays stock Kodi onboarding.

Pure Python; no dependencies beyond the standard library. This gate
deliberately detects known failure signatures rather than attempting to
certify arbitrary videos or infer functional TV playback from colors.
"""
from pathlib import Path
import sys


def read_ppm(path):
    with Path(path).open("rb") as stream:
        if stream.readline() != b"P6\n":
            raise ValueError("expected binary PPM P6")
        dimensions = stream.readline().split()
        if len(dimensions) != 2:
            raise ValueError("invalid PPM dimensions")
        width, height = (int(part) for part in dimensions)
        if not (320 <= width <= 8192 and 240 <= height <= 8192):
            raise ValueError("invalid frame dimensions")
        if stream.readline() != b"255\n":
            raise ValueError("invalid PPM max value")
        pixels = stream.read()
    if len(pixels) != width * height * 3:
        raise ValueError("incomplete or trailing frame data")
    return width, height, pixels


def cyan_fraction(width, height, pixels, rect, step=4):
    x0, y0, x1, y1 = rect
    count = 0
    cyan = 0
    for y in range(int(height * y0), int(height * y1), step):
        for x in range(int(width * x0), int(width * x1), step):
            offset = (y * width + x) * 3
            red, green, blue = pixels[offset:offset + 3]
            count += 1
            # Estuary's cyan header / blue highlighted action button.
            if red < 80 and 80 < green < 210 and blue > green + 15:
                cyan += 1
    return cyan / max(1, count)


def classify(path):
    width, height, pixels = read_ppm(path)
    # A completely black capture is a failed renderer/boot, not a TV UI.
    # Sample the whole frame, rather than relying only on Estuary colors.
    samples = 0
    brightness = 0
    for y in range(0, height, 8):
        for x in range(0, width, 8):
            offset = (y * width + x) * 3
            brightness += sum(pixels[offset:offset + 3])
            samples += 3
    mean_brightness = brightness / max(1, samples)
    if mean_brightness < 8:
        raise ValueError(f"blank or black QEMU frame detected (mean {mean_brightness:.1f})")
    # The characteristic first-run "Disabled add-ons" popup has an opaque
    # cyan horizontal header across much of the screen's central region.
    popup = cyan_fraction(width, height, pixels, (.27, .34, .72, .385))
    # Estuary logo appears near the upper-left corner independently of
    # popup layout. Do not reject a scene based on one blue pixel.
    logo = cyan_fraction(width, height, pixels, (.04, .02, .085, .075), step=1)
    if popup > .35:
        raise ValueError(
            f"stock Kodi add-on enable dialog detected (cyan header {popup:.1%})"
        )
    if logo > .05:
        raise ValueError(
            f"stock Kodi branding detected (cyan logo {logo:.1%})"
        )
    return {"popup_cyan": popup, "logo_cyan": logo}


def main(argv):
    if len(argv) != 2:
        raise SystemExit("usage: check_qemu_tv_visual.py IMAGE.ppm")
    try:
        metrics = classify(argv[1])
    except (OSError, ValueError) as error:
        raise SystemExit(f"Dede TV visual regression: {error}")
    print(f"QEMU screen passed stock-Kodi rejection check: {metrics}")


if __name__ == "__main__":
    main(sys.argv)
