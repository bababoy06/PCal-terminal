# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Bitmap fonts for the graphical PCal screen.

Reads Linux console fonts (PSF1 and PSF2, optionally gzipped), the same files the
console itself uses, e.g. Terminus in /usr/share/consolefonts/ter-v16n.psf.gz.
Box-drawing lines, block elements and the menu's arrows are drawn by code instead,
so they join up perfectly at every size, even when a font lacks them.

Glyphs are numpy float arrays (height x width) with values 0..1 (0 = off, 1 = lit).
"""
import gzip
import os
import struct

import numpy as np

FONT_DIRS = ("/usr/share/consolefonts", "/usr/share/kbd/consolefonts")
# Terminus comes in these heights; "v" = the variant with the most characters.
TERMINUS_SIZES = (12, 14, 16, 18, 20, 22, 24, 28, 32)


def find_font(size=16, bold=False):
    """Path of the Terminus console font of this height (or the nearest one), or None."""
    want = sorted(TERMINUS_SIZES, key=lambda s: (abs(s - size), s))
    for s in want:
        for d in FONT_DIRS:
            for ext in (".psf.gz", ".psf", ".psfu.gz", ".psfu"):
                p = os.path.join(d, f"ter-v{s}{'b' if bold else 'n'}{ext}")
                if os.path.exists(p):
                    return p
    for d in FONT_DIRS:  # anything usable at all
        for name in ("default8x16.psf.gz", "default8x16.psfu.gz", "lat1-16.psfu.gz"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                return p
    return None


class PSFFont:
    """A console font: glyph(ch) gives the bitmap of one character."""

    def __init__(self, path):
        with open(path, "rb") as f:
            data = f.read()
        if data[:2] == b"\x1f\x8b":
            data = gzip.decompress(data)
        self.path = path
        self.map = {}
        if data[:2] == b"\x36\x04":
            self._psf1(data)
        elif data[:4] == b"\x72\xb5\x4a\x86":
            self._psf2(data)
        else:
            raise ValueError(f"{path}: not a PSF console font")
        self.cache = {}

    def _bitmaps(self, raw, count, height, width):
        rowbytes = (width + 7) // 8
        a = np.frombuffer(raw, dtype=np.uint8, count=count * height * rowbytes)
        bits = np.unpackbits(a.reshape(count, height, rowbytes), axis=2)[:, :, :width]
        self.glyphs = bits.astype(np.float32)
        self.height, self.width = height, width

    def _psf1(self, data):
        mode, size = data[2], data[3]
        count = 512 if mode & 1 else 256
        self._bitmaps(data[4:], count, size, 8)
        if mode & 6:  # unicode table: little-endian u16, 0xFFFE starts a sequence, 0xFFFF ends a glyph
            pos, g, seq = 4 + count * size, 0, False
            while g < count and pos + 1 < len(data):
                (u,) = struct.unpack_from("<H", data, pos)
                pos += 2
                if u == 0xFFFF:
                    g, seq = g + 1, False
                elif u == 0xFFFE:
                    seq = True
                elif not seq:
                    self.map.setdefault(chr(u), g)
        else:
            self.map = {chr(i): i for i in range(count)}

    def _psf2(self, data):
        _, _, hsize, flags, count, charsize, height, width = struct.unpack_from("<4sIIIIIII", data)
        self._bitmaps(data[hsize:], count, height, width)
        if flags & 1:  # unicode table: UTF-8, 0xFE starts a sequence, 0xFF ends a glyph
            pos = hsize + count * charsize
            for g in range(count):
                end = data.index(b"\xff", pos)
                entry = data[pos:end].split(b"\xfe")[0]
                for ch in entry.decode("utf-8", "replace"):
                    self.map.setdefault(ch, g)
                pos = end + 1
        else:
            self.map = {chr(i): i for i in range(count)}

    def glyph(self, ch):
        g = self.cache.get(ch)
        if g is None:
            g = drawn_glyph(ch, self.height, self.width)
            if g is None:
                i = self.map.get(ch)
                if i is None:
                    i = self.map.get(FALLBACK.get(ch, "?"), self.map.get("?", 0))
                g = self.glyphs[i]
            self.cache[ch] = g
        return g


# Characters a font may lack, and what to show instead.
FALLBACK = {"·": ".", "»": ">", "«": "<", "■": "#", "►": ">", "▲": "^", "▼": "v", "…": "."}

# ------------------------------------------------------------------ drawn glyphs
# Box drawing: per character the weight of the arm going (up, down, left, right):
# 0 none, 1 light, 2 heavy, 3 double. Mixed light/double corners use the font.
_L, _H, _D = 1, 2, 3
BOX = {
    "─": (0, 0, _L, _L), "│": (_L, _L, 0, 0), "┌": (0, _L, 0, _L), "┐": (0, _L, _L, 0),
    "└": (_L, 0, 0, _L), "┘": (_L, 0, _L, 0), "├": (_L, _L, 0, _L), "┤": (_L, _L, _L, 0),
    "┬": (0, _L, _L, _L), "┴": (_L, 0, _L, _L), "┼": (_L, _L, _L, _L),
    "╴": (0, 0, _L, 0), "╵": (_L, 0, 0, 0), "╶": (0, 0, 0, _L), "╷": (0, _L, 0, 0),
    "━": (0, 0, _H, _H), "┃": (_H, _H, 0, 0), "┏": (0, _H, 0, _H), "┓": (0, _H, _H, 0),
    "┗": (_H, 0, 0, _H), "┛": (_H, 0, _H, 0), "┣": (_H, _H, 0, _H), "┫": (_H, _H, _H, 0),
    "┳": (0, _H, _H, _H), "┻": (_H, 0, _H, _H), "╋": (_H, _H, _H, _H),
    "═": (0, 0, _D, _D), "║": (_D, _D, 0, 0), "╔": (0, _D, 0, _D), "╗": (0, _D, _D, 0),
    "╚": (_D, 0, 0, _D), "╝": (_D, 0, _D, 0), "╠": (_D, _D, 0, _D), "╣": (_D, _D, _D, 0),
    "╦": (0, _D, _D, _D), "╩": (_D, 0, _D, _D), "╬": (_D, _D, _D, _D),
}
SHADES = {"░": 0.25, "▒": 0.5, "▓": 0.75}


def _box(arms, h, w):
    up, down, left, right = arms
    g = np.zeros((h, w), np.float32)
    t = max(1, round(w / 8))           # light line thickness
    T = max(2, round(w / 4))           # heavy line thickness
    cx, cy = (w - t) // 2, (h - t) // 2
    if 3 in arms:
        d = max(1, round(w / 5))        # half the gap between double lines
        vert = up or down
        horz = left or right
        for s in (-1, 1):               # the two horizontal strokes: above (-1) and below (+1)
            y = cy + s * d
            near = up if s < 0 else down    # the vertical arm on this stroke's side
            if right:
                x0 = cx + d if near else (cx - d if vert else (0 if left else cx))
                g[y:y + t, x0:] = 1
            if left:
                x1 = cx - d if near else (cx + d if vert else (w if right else cx))
                g[y:y + t, :x1 + t] = 1
        for s in (-1, 1):               # the two vertical strokes: left (-1) and right (+1)
            x = cx + s * d
            near = left if s < 0 else right
            if down:
                y0 = cy + d if near else (cy - d if horz else (0 if up else cy))
                g[y0:, x:x + t] = 1
            if up:
                y1 = cy - d if near else (cy + d if horz else (h if down else cy))
                g[:y1 + t, x:x + t] = 1
        return g
    for arm, on in (("u", up), ("d", down), ("l", left), ("r", right)):
        if not on:
            continue
        th = T if on == _H else t
        x, y = (w - th) // 2, (h - th) // 2
        if arm == "u":
            g[:y + th, x:x + th] = 1
        elif arm == "d":
            g[y:, x:x + th] = 1
        elif arm == "l":
            g[y:y + th, :x + th] = 1
        else:
            g[y:y + th, x:] = 1
    return g


def _triangle(direction, h, w):
    g = np.zeros((h, w), np.float32)
    if direction == "right":
        size = min(w - 1, h // 2)
        top = (h - 2 * size) // 2 + 1
        for i in range(2 * size - 1):
            reach = size - abs(i - (size - 1))
            g[top + i, 1:1 + reach] = 1
    else:
        half = max(2, (w - 1) // 2)
        rows = half + 1
        top = (h - rows) // 2
        for i in range(rows):
            span = i if direction == "up" else rows - 1 - i
            g[top + i, w // 2 - span:w // 2 + span + 1] = 1
    return g


def drawn_glyph(ch, h, w):
    """Glyphs drawn by code (lines, blocks, arrows), or None to use the font."""
    if ch in BOX:
        return _box(BOX[ch], h, w)
    if ch == "█":
        return np.ones((h, w), np.float32)
    if ch == "▀":
        g = np.zeros((h, w), np.float32)
        g[: h // 2] = 1
        return g
    if ch == "▄":
        g = np.zeros((h, w), np.float32)
        g[h // 2:] = 1
        return g
    if ch == "▌":
        g = np.zeros((h, w), np.float32)
        g[:, : w // 2] = 1
        return g
    if ch == "▐":
        g = np.zeros((h, w), np.float32)
        g[:, w // 2:] = 1
        return g
    if ch in SHADES:  # a dither pattern, like the old character sets
        yy, xx = np.mgrid[0:h, 0:w]
        level = SHADES[ch]
        if level == 0.5:
            return ((yy + xx) % 2 == 0).astype(np.float32)
        if level == 0.25:
            return ((yy % 2 == 0) & ((xx + yy // 2) % 2 == 0)).astype(np.float32)
        return 1 - ((yy % 2 == 0) & ((xx + yy // 2) % 2 == 0)).astype(np.float32)
    if ch == "■":
        g = np.zeros((h, w), np.float32)
        s = max(2, w - 2)
        y0, x0 = (h - s) // 2, (w - s) // 2
        g[y0:y0 + s, x0:x0 + s] = 1
        return g
    if ch == "►":
        return _triangle("right", h, w)
    if ch == "▲":
        return _triangle("up", h, w)
    if ch == "▼":
        return _triangle("down", h, w)
    return None
