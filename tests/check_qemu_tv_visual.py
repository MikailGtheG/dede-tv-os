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
        expected_bytes = width * height * 3
        # Reject oversized or truncated captures before allocating their payload.
        # A malformed CI artifact must not exhaust the runner's memory.
        if Path(path).stat().st_size - stream.tell() != expected_bytes:
            raise ValueError("incomplete or trailing frame data")
        pixels = stream.read(expected_bytes)
    if len(pixels) != expected_bytes:
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


def modal_panel_fraction(width, height, pixels, step=4):
    """Estimate the dark, opaque body beneath Kodi's cyan modal header.

    A cyan stripe alone is also valid custom-skin artwork. Require a
    contrasting rectangular body before treating it as an onboarding modal.
    """
    x0, x1 = int(width * .27), int(width * .72)
    y0, y1 = int(height * .39), int(height * .58)
    contrasting = total = 0
    for y in range(y0, y1, step):
        for x in range(x0, x1, step):
            offset = (y * width + x) * 3
            red, green, blue = pixels[offset:offset + 3]
            total += 1
            # Compare against the adjacent, same-row backdrop: the modal
            # body must be a distinct panel, not merely a dark TV skin.
            outside = (y * width + int(width * .20)) * 3
            backdrop = pixels[outside:outside + 3]
            if max(red, green, blue) < 55 and sum(abs(a - b) for a, b in zip((red, green, blue), backdrop)) >= 18:
                contrasting += 1
    return contrasting / max(1, total)


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
        # The 8-pixel sampling grid can miss small overlays or a thin
        # picture. Recheck all pixels before declaring the VGA output blank.
        full_mean = sum(pixels) / len(pixels)
        if full_mean < 8:
            raise ValueError(f"blank or black QEMU frame detected (mean {full_mean:.1f})")
    # A uniformly colored frame can be a stuck splash/renderer, even when
    # it is bright enough to pass the black-screen check. Sample widely
    # spaced pixels and reject an essentially featureless display.
    sampled_colors = set()
    for y in range(0, height, 8):
        for x in range(0, width, 8):
            offset = (y * width + x) * 3
            sampled_colors.add(bytes(pixels[offset:offset + 3]))
            if len(sampled_colors) > 1:
                break
        if len(sampled_colors) > 1:
            break
    # Sparse samples can miss small but real UI details. Only reject a
    # uniform frame after checking every pixel, avoiding false positives.
    if len(sampled_colors) == 1:
        # Avoid allocating a second full-frame buffer (up to 192 MiB at
        # 8192x8192) just to compare the frame against a repeated RGB triplet.
        # A bounded, pixel-aligned chunk also handles off-grid UI details.
        color = pixels[:3]
        chunk_pixels = 4096
        pattern = color * chunk_pixels
        if all(pixels[offset:offset + len(pattern)] == pattern[:min(len(pattern), len(pixels) - offset)]
               for offset in range(0, len(pixels), len(pattern))):
            raise ValueError("uniform QEMU frame detected (no visible UI or video detail)")
    # The characteristic first-run "Disabled add-ons" popup has an opaque
    # cyan horizontal header across much of the screen's central region.
    popup = cyan_fraction(width, height, pixels, (.27, .34, .72, .385))
    # Estuary logo appears near the upper-left corner independently of
    # popup layout. Do not reject a scene based on one blue pixel.
    logo = cyan_fraction(width, height, pixels, (.04, .02, .085, .075), step=1)
    panel = modal_panel_fraction(width, height, pixels)
    if popup > .35 and panel > .80:
        raise ValueError(
            f"stock Kodi add-on enable dialog detected (cyan header {popup:.1%})"
        )
    if logo > .05:
        raise ValueError(
            f"stock Kodi branding detected (cyan logo {logo:.1%})"
        )
    return {"popup_cyan": popup, "panel_dark": panel, "logo_cyan": logo}


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
