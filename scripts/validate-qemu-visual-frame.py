#!/usr/bin/env python3
"""Fail a QEMU graphical smoke check when the captured PPM is not the
DclawOS Phase 1 desktop.

Phase 1 desktop appearance (what the gate is matched to):
- Openbox root window with NO wallpaper: black everywhere except the
  taskbar. The taskbar is a full-width 48px strip pinned to the bottom
  (dclawos-shell: move(0, height-48)), rendered in the default GTK theme,
  which is essentially grayscale.
- That is why the old DclawOS gates do NOT apply here: the old desktop
  painted a colorful aurora wallpaper over nearly the whole frame
  (non-black ~100%, mean saturation ~0.95) and the old saturation gate
  existed to exclude the GDM greeter. The new desktop has no wallpaper
  (non-black ~= 48/height ~= 6% at 800px tall) and a grayscale theme
  (saturation ~= 0), and the greeter is excluded by the session-marker
  gate in qemu-boot-smoke.sh, not by saturation.

Gate (all three must hold):
1. Overall non-black ratio >= 4%: excludes all-black / dead frames.
   A taskbar-only desktop is ~6%; a text console is ~9% — the ratio
   alone does not discriminate those two, it only excludes blank.
2. Sampled color variation >= 32: excludes a uniform X root window.
3. Bottom-band density >= 30%: sampled pixels in the last 10% of rows
   must be at least 30% non-black. This is the taskbar discriminator:
   the taskbar strip fills ~60% of the bottom band's samples, while a
   text console's bottom band holds at most a login-prompt line or two
   (well under 30%). Run 6 evidence: its frame had exactly 7680
   non-black samples at 1280x800 — precisely the taskbar strip's sampled
   size (1280*48/8) — yet the old 12% / saturation gates failed it.
"""
from __future__ import annotations

import sys
from pathlib import Path


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    if not data.startswith(b"P6"):
        raise ValueError("expected binary P6 PPM")

    cursor = 2
    tokens: list[bytes] = []
    while len(tokens) < 3:
        while cursor < len(data) and data[cursor] in b" \t\r\n":
            cursor += 1
        if cursor < len(data) and data[cursor] == ord("#"):
            while cursor < len(data) and data[cursor] not in b"\r\n":
                cursor += 1
            continue
        start = cursor
        while cursor < len(data) and data[cursor] not in b" \t\r\n":
            cursor += 1
        if start == cursor:
            raise ValueError("truncated PPM header")
        tokens.append(data[start:cursor])

    while cursor < len(data) and data[cursor] in b" \t\r\n":
        cursor += 1
    width, height, max_value = map(int, tokens)
    if max_value != 255:
        raise ValueError(f"unsupported PPM max value {max_value}")
    pixels = data[cursor:]
    expected = width * height * 3
    if len(pixels) != expected:
        raise ValueError(f"PPM payload has {len(pixels)} bytes; expected {expected}")
    return width, height, pixels


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} FRAME.ppm", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    try:
        width, height, pixels = read_ppm(path)
    except (OSError, ValueError) as exc:
        print(f"invalid QEMU graphical frame: {exc}", file=sys.stderr)
        return 1

    sample_step = 8
    bottom_start_row = height - max(1, height // 10)
    sampled = 0
    nonblack = 0
    bottom_sampled = 0
    bottom_nonblack = 0
    sampled_colors: set[tuple[int, int, int]] = set()
    pixel_count = width * height
    for index in range(0, pixel_count, sample_step):
        offset = 3 * index
        red, green, blue = pixels[offset : offset + 3]
        sampled += 1
        sampled_colors.add((red, green, blue))
        is_nonblack = red > 8 or green > 8 or blue > 8
        if is_nonblack:
            nonblack += 1
        if index // width >= bottom_start_row:
            bottom_sampled += 1
            if is_nonblack:
                bottom_nonblack += 1

    ratio = nonblack / sampled if sampled else 0.0
    color_count = len(sampled_colors)
    bottom_density = bottom_nonblack / bottom_sampled if bottom_sampled else 0.0
    print(
        f"QEMU visual frame {width}x{height}: "
        f"non-black samples {nonblack}/{sampled} ({ratio:.2%}); "
        f"sampled colors {color_count}; "
        f"bottom-band non-black {bottom_nonblack}/{bottom_sampled} ({bottom_density:.2%})"
    )
    if ratio < 0.04:
        print(
            "QEMU frame is effectively blank; "
            "the Live graphical desktop was not visibly rendered",
            file=sys.stderr,
        )
        return 1
    if color_count < 32:
        print(
            "QEMU frame has too little color variation; it is a uniform X root "
            "window rather than a rendered Live desktop",
            file=sys.stderr,
        )
        return 1
    if bottom_density < 0.30:
        print(
            "QEMU frame has no rendered bottom taskbar band; "
            "it is a text console, not the DclawOS Phase 1 desktop",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
