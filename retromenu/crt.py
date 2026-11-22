# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The CRT renderer for the graphical PCal screen.

Turns the cells of a terminal (vt.VT) into a picture of an old monitor: a console font
drawn pixel by pixel, then phosphor glow, scanlines, a curved tube with darker corners,
and the power-on / power-off effect. It only computes pictures (numpy arrays); showing
them on the display is the job of the screen program.

Try it without any display: run a program in a pseudo-terminal and save a picture.

  python3 retromenu/crt.py -o menu.png -- python3 retromenu/retromenu.py
  python3 retromenu/crt.py -o amber.png --theme amber --size 1920x1080 --scale 2 -- htop
"""
import argparse
import math
import os
import struct
import sys
import time
import zlib

import numpy as np

try:
    from . import psf, vt as vtmod
except ImportError:  # run as a script
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import psf
    import vt as vtmod

# Phosphor colours per theme: glass (background), dim, normal, bright.
# The same RGB values PCal uses to repaint the Linux console.
PHOSPHOR = {
    "green": ("020a04", "1d6b34", "33ff66", "b4ffc8"),
    "amber": ("0a0600", "7a5000", "ffb000", "ffd98a"),
    "white": ("080808", "6a6a6a", "d0d0d0", "ffffff"),
    "cyan": ("000a0c", "1a6070", "33e0ff", "c0f6ff"),
}
# The xterm-256 colours the menu uses per theme for (normal, bright, dim): they are
# mapped onto the phosphor's normal, bright and dim so the menu looks exactly right.
MENU_COLOURS = {"green": (77, 120, 28), "amber": (214, 222, 130),
                "white": (250, 231, 243), "cyan": (80, 159, 30)}

EFFECT_LEVELS = {
    #        glow  scanlines curvature vignette afterglow
    "off":   (0.0, 0.0, 0.0, 0.0, 0.0),
    "light": (0.35, 0.25, 0.0, 0.25, 0.0),
    "full":  (0.55, 0.40, 0.045, 0.45, 0.5),
}
EFFECT_NAMES = ("glow", "scanlines", "curvature", "vignette", "afterglow")

# Pixel layouts: (offset, bits) per colour. RGBX = bytes R, G, B, X in memory (PNG order).
RGBX = {"bpp": 32, "r": (0, 8), "g": (8, 8), "b": (16, 8), "x": (24, 8)}
OUTSIDE = 100.0  # canvas value marking "outside the glass": always drawn black


def pack(r, g, b, fmt=RGBX):
    """Pack 0-255 colour channels into pixels of the given layout (uint32 or uint16)."""
    out = np.zeros(np.shape(r), np.uint32)
    for v, key in ((r, "r"), (g, "g"), (b, "b")):
        off, ln = fmt[key]
        out |= (np.asarray(v).astype(np.uint32) >> (8 - ln)) << off
    if "x" in fmt:
        off, ln = fmt["x"]
        out |= np.uint32((1 << ln) - 1) << np.uint32(off)
    return out if fmt["bpp"] == 32 else out.astype(np.uint16)


def _hex(s):
    return np.array([int(s[i:i + 2], 16) for i in (0, 2, 4)], np.float32) / 255


def xterm_palette():
    """The 256 xterm colours as a (256, 3) array of 0..1 values."""
    base = ["000000", "cd0000", "00cd00", "cdcd00", "0000ee", "cd00cd", "00cdcd", "e5e5e5",
            "7f7f7f", "ff0000", "00ff00", "ffff00", "5c5cff", "ff00ff", "00ffff", "ffffff"]
    pal = [_hex(h) for h in base]
    steps = [0, 95, 135, 175, 215, 255]
    for r in steps:
        for g in steps:
            for b in steps:
                pal.append(np.array([r, g, b], np.float32) / 255)
    for i in range(24):
        v = (8 + 10 * i) / 255
        pal.append(np.array([v, v, v], np.float32))
    return np.array(pal, np.float32)


PALETTE = xterm_palette()


def luminance(rgb):
    return float(0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2])


def best_scale(font, size, effects="full", min_cols=100, min_rows=30):
    """The biggest scale that still fits min_cols x min_rows characters on the screen."""
    for s in range(8, 1, -1):
        cols, rows = Renderer.grid(font, size, s, effects)
        if cols >= min_cols and rows >= min_rows:
            return s
    return 1


class Renderer:
    """
    Draws terminal cells as a CRT picture, redrawing only the rows that changed.

    font:    a psf.PSFFont         scale: screen pixels per font pixel (1, 2, 3...)
    theme:   green/amber/white/cyan, or "colour" for a colour monitor
    effects: "off", "light", "full", or a dict with any of
             glow, scanlines, curvature, vignette, afterglow (0..1)
    fmt:     pixel layout of .frame (RGBX for pictures; the display's own for the screen)

    Use: update(grid, cursor) after the terminal changed; it returns the screen rows
    [(top, bottom), ...] it redrew in .frame. While .animating (afterglow fading out),
    call update() again every few hundredths of a second.
    """

    def __init__(self, font, size, scale=1, theme="green", effects="full", margin=None, fmt=RGBX):
        self.font, self.scale = font, max(1, int(scale))
        self.out_w, self.out_h = size
        self.fmt = fmt
        lv = EFFECT_LEVELS.get(effects, EFFECT_LEVELS["full"]) if isinstance(effects, str) else None
        self.fx = dict(zip(EFFECT_NAMES, lv)) if lv else {k: float(effects.get(k, 0)) for k in EFFECT_NAMES}
        self.mono = theme in PHOSPHOR
        self.theme = theme if self.mono else "colour"
        self.cw, self.ch = font.width * self.scale, font.height * self.scale
        self.cols, self.rows = self.grid(font, size, self.scale, self.fx, margin)
        self.ox = (self.out_w - self.cols * self.cw) // 2
        self.oy = (self.out_h - self.rows * self.ch) // 2
        # rows of text around a change that get redrawn too, because the glow spills over
        self.glow_rows = int(math.ceil(18 / font.height)) if self.fx["glow"] > 0 else 0
        self.tiles = {}
        self.rowcache = {}
        self._build_lut()
        self._build_static()
        self.reset()

    @staticmethod
    def grid(font, size, scale, effects="full", margin=None):
        """How many (columns, rows) of characters fit on a screen of this size."""
        if isinstance(effects, str):
            effects = {"curvature": EFFECT_LEVELS.get(effects, EFFECT_LEVELS["full"])[2]}
        if margin is None:  # room for the curved glass
            margin = 0.02 + effects.get("curvature", 0) * 0.6
        usable_w = int(size[0] * (1 - 2 * margin))
        usable_h = int(size[1] * (1 - 2 * margin))
        return max(20, usable_w // (font.width * scale)), max(8, usable_h // (font.height * scale))

    # ------------------------------------------------------------ colours
    def _build_lut(self):
        """Intensity (0..1.5 in 1024 steps) -> phosphor pixel; entries 1024+ are black."""
        if not self.mono:
            return
        glass, dim, normal, bright = (_hex(h) for h in PHOSPHOR[self.theme])
        n, b, d = (luminance(PALETTE[i]) for i in MENU_COLOURS[self.theme])
        white = np.ones(3, np.float32)
        xs = [0.0, d, n, b, 1.0, 1.5]
        stops = [glass, dim, normal, bright, bright * 0.75 + white * 0.25, white]
        t = np.linspace(0, 1.5, 1024, dtype=np.float32)
        lut = np.stack([np.interp(t, xs, [s[c] for s in stops]) for c in range(3)], 1)
        lut8 = np.clip(lut, 0, 1) * 255 + 0.5
        px = pack(lut8[:, 0], lut8[:, 1], lut8[:, 2], self.fmt)
        self.lut_px = np.concatenate([px, np.zeros(1024, px.dtype)])

    def cell_colours(self, fg, bg, flags):
        """(foreground, background) for one cell: floats (mono) or RGB arrays (colour)."""
        if fg == vtmod.DEFAULT:
            fg = 7
        if 0 <= fg < 8 and flags & vtmod.BOLD:
            fg += 8
        f = self._colour(fg)
        b = self._colour(0 if bg == vtmod.DEFAULT else bg)
        if flags & vtmod.REV:
            f, b = b, f
        if flags & vtmod.DIM:
            f = f * 0.55
        if flags & vtmod.INVIS:
            f = b
        if self.mono:
            return luminance(f), luminance(b)
        return f, b

    @staticmethod
    def _colour(c):
        if c >= vtmod.RGB:
            c &= 0xFFFFFF
            return np.array([(c >> 16) & 255, (c >> 8) & 255, c & 255], np.float32) / 255
        return PALETTE[c & 255]

    # ------------------------------------------------------------ cells -> rows of pixels
    def tile(self, cell):
        t = self.tiles.get(cell)
        if t is not None:
            return t
        ch, fg, bg, flags = cell
        f, b = self.cell_colours(fg, bg, flags)
        g = self.font.glyph(ch) if ch != " " else np.zeros((self.font.height, self.font.width), np.float32)
        if flags & vtmod.BOLD and ch not in psf.BOX:  # a bit heavier, like a brighter beam
            g = np.maximum(g, np.pad(g, ((0, 0), (1, 0)))[:, :-1] * 0.6)
        if flags & vtmod.UNDER:
            g = g.copy()
            g[-1] = 1
        if self.mono:
            t = b + (f - b) * g
        else:
            t = b[None, None, :] + (f - b)[None, None, :] * g[:, :, None]
        t = t.astype(np.float32)
        if len(self.tiles) > 20000:
            self.tiles.clear()
        self.tiles[cell] = t
        return t

    def row_key(self, grid, y, cursor=None):
        row = tuple(grid[y][: self.cols]) if y < len(grid) else ()
        if cursor and cursor[1] == y and cursor[0] < len(row):
            cx = cursor[0]
            ch, fg, bg, fl = row[cx]
            row = row[:cx] + ((ch, fg, bg, fl ^ vtmod.REV),) + row[cx + 1:]
        return row

    def row_image(self, key):
        """One row of text at font resolution."""
        img = self.rowcache.get(key)
        if img is None:
            fh, w = self.font.height, self.cols * self.font.width
            shape = (fh, w) if self.mono else (fh, w, 3)
            if key:
                img = np.concatenate([self.tile(c) for c in key], axis=1)
                if img.shape[1] < w:
                    img = np.concatenate([img, np.zeros((fh, w - img.shape[1]) + shape[2:], np.float32)], 1)
            else:
                img = np.zeros(shape, np.float32)
            if len(self.rowcache) > 600:
                self.rowcache.clear()
            self.rowcache[key] = img
        return img

    def cells_image(self, grid, cursor=None):
        """The whole terminal at font resolution."""
        return np.concatenate([self.row_image(self.row_key(grid, y, cursor)) for y in range(self.rows)], 0)

    # ------------------------------------------------------------ fixed parts of the picture
    def _build_static(self):
        """Things that only depend on the picture size: scanline pattern, glass shape, remap."""
        s, sl = self.scale, self.fx["scanlines"]
        if s >= 2:
            pat = np.ones(s, np.float32)
            pat[-1] = 1 - sl
            if s >= 4:
                pat[0] = 1 - sl * 0.35
            self.scan = np.tile(pat, self.rows * self.font.height)[:, None]
        else:
            self.scan = np.where(np.arange(self.rows * self.ch) % 2, 1 - sl * 0.6, 1).astype(np.float32)[:, None]
        if not self.mono:
            self.scan = self.scan[..., None]
        W, H = self.out_w, self.out_h
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        u = (xx + 0.5) / W * 2 - 1
        v = (yy + 0.5) / H * 2 - 1
        del xx, yy
        k = self.fx["curvature"]
        self.remap = None
        if k > 0:  # barrel distortion: sample further out towards the edges
            su = u * (1 + k * v * v) * (1 + k * 0.5)
            sv = v * (1 + k * u * u) * (1 + k * 0.5)
            p = 14  # the glass: a rounded rectangle (superellipse); black bezel outside it
            inside = (np.abs(su) ** p + np.abs(sv) ** p) ** (1 / p) < 1
            fx = np.clip((su + 1) / 2 * W - 0.5, 0, W - 1.001)
            fy = np.clip((sv + 1) / 2 * H - 0.5, 0, H - 1.001)
            # which canvas rows each screen row reads, to know what to redraw after a change
            big = np.float32(1e9)
            self.src_lo = np.where(inside, fy, big).min(axis=1).astype(np.int32)
            self.src_hi = np.where(inside, fy, -big).max(axis=1).astype(np.int32) + 2
            flat_inside = inside.ravel()
            self.inside = np.flatnonzero(flat_inside)
            self.row_start = np.searchsorted(self.inside, np.arange(H + 1) * W)
            fx, fy = fx.ravel()[self.inside], fy.ravel()[self.inside]
            x0, y0 = fx.astype(np.int32), fy.astype(np.int32)
            self.remap = y0 * W + x0
            # bilinear sampling at scale 1, so thin strokes stay smooth instead of breaking up
            self.remap_w = (fx - x0, fy - y0) if self.scale == 1 else None
            del su, sv, fx, fy, inside
        shade = 1 - self.fx["vignette"] * np.clip((u * u * 0.8 + v * v) * 0.55, 0, 1) ** 1.5
        self.shade = shade.astype(np.float32) if self.mono else shade.astype(np.float32)[..., None]

    def reset(self):
        """Forget what is on screen: the next update() draws everything."""
        chans = () if self.mono else (3,)
        self.cbuf = np.zeros((self.out_h, self.out_w) + chans, np.float32)
        dt = np.uint32 if self.fmt["bpp"] == 32 else np.uint16
        self.frame = np.zeros((self.out_h, self.out_w), dt)
        self.finish_rows(self.cbuf, 0, self.out_h, self.frame)
        self.shown = [None] * self.rows
        self.fades = []

    # ------------------------------------------------------------ effects helpers
    @staticmethod
    def _blur(img, r):
        """Fast box blur (twice, so it looks soft) along both axes."""
        for axis in (0, 1):
            for _ in range(2):
                c = np.cumsum(np.pad(img, [(r + 1, r) if a == axis else (0, 0) for a in range(img.ndim)],
                                     mode="edge"), axis=axis, dtype=np.float32)
                hi = np.take(c, np.arange(2 * r + 1, c.shape[axis]), axis=axis)
                lo = np.take(c, np.arange(0, c.shape[axis] - 2 * r - 1), axis=axis)
                img = (hi - lo) / (2 * r + 1)
        return img

    @staticmethod
    def _upsample(img, H, W, r0=0, r1=None):
        """Bilinear resize of a small picture to H x W; only output rows r0..r1 are made."""
        r1 = H if r1 is None else r1
        h, w = img.shape[:2]
        xs = np.clip((np.arange(W, dtype=np.float32) + 0.5) * w / W - 0.5, 0, w - 1)
        x0 = xs.astype(np.int32)
        x1 = np.minimum(x0 + 1, w - 1)
        fx = (xs - x0)[None, :, None] if img.ndim == 3 else (xs - x0)[None, :]
        ys = np.clip((np.arange(r0, r1, dtype=np.float32) + 0.5) * h / H - 0.5, 0, h - 1)
        y0 = ys.astype(np.int32)
        y1 = np.minimum(y0 + 1, h - 1)
        rows = np.arange(y0.min(), y1.max() + 1) if len(ys) else np.arange(0)
        sub = img[rows[0]:rows[-1] + 1] if len(rows) else img[:0]
        a = sub[:, x0]
        tmp = a + (sub[:, x1] - a) * fx
        fy = (ys - y0)[:, None, None] if img.ndim == 3 else (ys - y0)[:, None]
        out = tmp[y0 - rows[0]]
        out += (tmp[y1 - rows[0]] - out) * fy
        return out

    # ------------------------------------------------------------ drawing
    def _draw_band(self, keys, a, b, now):
        """Redraw text rows a..b into the canvas (with scanlines and glow)."""
        s, fh, G = self.scale, self.font.height, self.glow_rows
        c0, c1 = max(0, a - G), min(self.rows - 1, b + G)
        base = np.concatenate([self.row_image(keys[y]) for y in range(c0, c1 + 1)], 0)
        y0, y1 = (a - c0) * fh, (b - c0 + 1) * fh
        part = base[y0:y1]
        ya, yb = self.oy + a * self.ch, self.oy + (b + 1) * self.ch
        xa, xb = self.ox, self.ox + self.cols * self.cw
        region = self.cbuf[ya:yb, xa:xb]
        ghost = None
        if self.fx["afterglow"] > 0 and self.shown[a] is not None:
            ghost = region.copy()  # what was there: it fades out instead of vanishing
            for f in [f for f in self.fades if f[0] < yb and f[1] > ya]:
                self.cbuf[f[0]:f[1], xa:xb] = f[2]
                self.fades.remove(f)
        region[...] = np.repeat(np.repeat(part, s, axis=0), s, axis=1) if s > 1 else part
        if self.fx["scanlines"] > 0:
            region *= self.scan[a * self.ch:(b + 1) * self.ch]
        glow = self.fx["glow"]
        if glow > 0:
            f = 4
            bh = -(-base.shape[0] // f) * f
            if bh != base.shape[0]:
                base = np.concatenate([base, np.zeros((bh - base.shape[0],) + base.shape[1:], np.float32)], 0)
            bw = base.shape[1] // f * f
            bb = base[:, :bw]
            rows4 = bb[0::4] + bb[1::4] + bb[2::4] + bb[3::4]
            small = (rows4[:, 0::4] + rows4[:, 1::4] + rows4[:, 2::4] + rows4[:, 3::4]) * (glow / 16)
            halo = self._upsample(self._blur(small, 2), bh * s, bw * s, y0 * s, y1 * s)
            region[:, :bw * s] += halo
        if ghost is not None and np.any(ghost > region):
            self.fades.append([ya, yb, region.copy(), ghost, now])
        return ya, yb

    def _tick_fades(self, now):
        """Afterglow: old pictures fade out over a few frames."""
        dirty, xa, xb = [], self.ox, self.ox + self.cols * self.cw
        rate = self.fx["afterglow"]
        for f in list(self.fades):
            ya, yb, clean, ghost, t0 = f
            level = rate ** ((now - t0) / 0.035) * 0.8
            if level < 0.04:
                self.cbuf[ya:yb, xa:xb] = clean
                self.fades.remove(f)
            else:
                np.maximum(clean, ghost * level, out=self.cbuf[ya:yb, xa:xb])
            dirty.append((ya, yb))
        return dirty

    @property
    def animating(self):
        return bool(self.fades)

    def update(self, grid, cursor=None, now=None):
        """Redraw what changed; returns the screen rows [(top, bottom)] updated in .frame."""
        now = time.monotonic() if now is None else now
        keys = [self.row_key(grid, y, cursor) for y in range(self.rows)]
        changed = [y for y in range(self.rows) if keys[y] != self.shown[y]]
        dirty = self._tick_fades(now) if self.fades else []
        if changed:
            G = self.glow_rows
            bands = []
            for y in changed:
                a, b = max(0, y - G), min(self.rows - 1, y + G)
                if bands and a <= bands[-1][1] + 1:
                    bands[-1][1] = max(bands[-1][1], b)
                else:
                    bands.append([a, b])
            for a, b in bands:
                dirty.append(self._draw_band(keys, a, b, now))
            self.shown = keys
        return self.finish_dirty(dirty)

    def finish_dirty(self, dirty):
        """Turn changed canvas rows into finished screen rows in .frame."""
        out = []
        for ya, yb in dirty:
            if self.remap is not None:
                hit = np.flatnonzero((self.src_hi >= ya) & (self.src_lo < yb))
                if not len(hit):
                    continue
                lo, hi = int(hit[0]), int(hit[-1]) + 1
            else:
                lo, hi = ya, yb
            out.append([lo, hi])
        out.sort()
        merged = []
        for lo, hi in out:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        for lo, hi in merged:
            self.finish_rows(self.cbuf, lo, hi, self.frame)
        return [tuple(m) for m in merged]

    def finish_rows(self, canvas, lo, hi, out):
        """Curved glass, vignette and colours for screen rows lo..hi of a canvas -> out."""
        W = self.out_w
        if self.remap is not None:
            s0, s1 = self.row_start[lo], self.row_start[hi]
            i = self.remap[s0:s1]
            flat = canvas.reshape(-1, *canvas.shape[2:])
            if self.remap_w is not None:
                wx, wy = self.remap_w[0][s0:s1], self.remap_w[1][s0:s1]
                if flat.ndim == 2:
                    wx, wy = wx[:, None], wy[:, None]
                a = flat[i]
                top = a + (flat[i + 1] - a) * wx
                b = flat[i + W]
                bot = b + (flat[i + W + 1] - b) * wx
                vals = top + (bot - top) * wy
            else:
                vals = flat[i]
            tmp = np.full(((hi - lo) * W,) + canvas.shape[2:], OUTSIDE, np.float32)
            tmp[self.inside[s0:s1] - lo * W] = vals
            tmp = tmp.reshape((hi - lo, W) + canvas.shape[2:])
        else:
            tmp = canvas[lo:hi].copy()
        tmp *= self.shade[lo:hi]
        if self.mono:
            out[lo:hi] = np.take(self.lut_px, np.minimum(tmp * (1023 / 1.5), 2047).astype(np.uint16))
        else:
            c = np.clip(tmp, 0, 1) * 255 + 0.5
            px = pack(c[..., 0], c[..., 1], c[..., 2], self.fmt)
            px[tmp[..., 0] > OUTSIDE / 4] = 0
            out[lo:hi] = px

    # ------------------------------------------------------------ whole pictures
    def canvas(self, grid, cursor=None):
        """The full canvas (before the glass) for a grid of cells."""
        self.reset()
        self.update(grid, cursor)
        return self.cbuf.copy()

    def finish(self, canvas, persist=False):
        """A whole finished picture of a canvas, as (h, w, 4) uint8 (RGBX layout)."""
        dt = np.uint32 if self.fmt["bpp"] == 32 else np.uint16
        out = np.zeros((self.out_h, self.out_w), dt)
        self.finish_rows(canvas, 0, self.out_h, out)
        return out.view(np.uint8).reshape(out.shape + (-1,))

    def picture(self):
        """The current frame as (h, w, 4) uint8."""
        return self.frame.view(np.uint8).reshape(self.frame.shape + (-1,))

    def render(self, term, cursor_on=True):
        """One finished picture of a vt.VT terminal, drawn from scratch."""
        cur = (term.x, term.y) if cursor_on and term.cursor_visible else None
        self.reset()
        self.update(term.grid, cur)
        return self.picture()

    # ------------------------------------------------------------ power on / off
    def power(self, canvas, t, on=True):
        """
        The tube switching on (t 0 -> 1) or off. on: a bright line opens into the picture.
        off: the picture collapses into a line, the line into a dot, and the dot fades.
        Returns a canvas for finish().
        """
        H, W = canvas.shape[:2]
        cy, cx = H // 2, W // 2
        out = np.zeros_like(canvas)
        line_h = max(2, self.scale * 2)
        if on:
            if t < 0.3:  # the line grows from the middle
                half = int(W / 2 * (t / 0.3) ** 0.6)
                out[cy - line_h // 2:cy + line_h // 2 + 1, cx - half:cx + half] = 1.45
                return out
            k = ((t - 0.3) / 0.7) ** 0.7
            return self._squash(canvas, max(k, 0.004), 1.0, boost=1 + 0.5 * (1 - k))
        if t < 0.45:  # squash to a line
            k = 1 - (t / 0.45) ** 0.8
            return self._squash(canvas, max(k, 0.004), 1.0, boost=1 + 0.6 * (1 - k))
        if t < 0.8:  # the line shrinks to a dot
            k = 1 - (t - 0.45) / 0.35
            half = max(line_h, int(W / 2 * k ** 1.5))
            out[cy - line_h // 2:cy + line_h // 2 + 1, cx - half:cx + half] = 1.45
            return out
        r = max(1, int(line_h * 1.5))
        out[cy - r:cy + r, cx - r:cx + r] = 1.5 * (1 - (t - 0.8) / 0.2)
        return out

    @staticmethod
    def _squash(canvas, ky, kx, boost=1.0):
        H, W = canvas.shape[:2]
        cy = H / 2
        ys = np.arange(H, dtype=np.float32)
        src = cy + (ys - cy) / ky
        valid = (src >= 0) & (src < H)
        rows = np.clip(src, 0, H - 1).astype(np.int32)
        out = canvas[rows] * boost
        out[~valid] = 0
        if ky < 0.02:  # nearly a line: make it a solid bright line
            out[int(cy) - 1:int(cy) + 2] = np.maximum(out[int(cy) - 1:int(cy) + 2], 1.4)
        return out


# ---------------------------------------------------------------- PNG output
def write_png(path, rgb):
    """Save a (h, w, 3) or RGBX (h, w, 4) uint8 array as PNG (no extra libraries needed)."""
    rgb = np.ascontiguousarray(rgb[..., :3])
    h, w = rgb.shape[:2]
    raw = np.concatenate([np.zeros((h, 1), np.uint8), rgb.reshape(h, w * 3)], axis=1).tobytes()

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------- try it: snapshot a program
def run_in_pty(cmd, rows, cols, wait=2.0, keys=(), env=None):
    """Run cmd in a pseudo-terminal of rows x cols and return the terminal afterwards."""
    import fcntl
    import pty
    import select
    import termios

    term = vtmod.VT(rows, cols)
    pid, fd = pty.fork()
    if pid == 0:
        e = dict(os.environ, TERM="xterm-256color", COLUMNS=str(cols), LINES=str(rows), PCAL_SCREEN="1")
        e.update(env or {})
        try:
            os.execvpe(cmd[0], cmd, e)
        finally:
            os._exit(127)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    import codecs
    dec = codecs.getincrementaldecoder("utf-8")("replace")
    start = time.time()
    schedule = sorted(keys)
    end = start + wait + (schedule[-1][0] if schedule else 0)
    while time.time() < end:
        while schedule and time.time() - start >= schedule[0][0]:
            os.write(fd, schedule.pop(0)[1].encode())
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try:
                data = os.read(fd, 65536)
            except OSError:
                break
            if not data:
                break
            term.feed(dec.decode(data))
            for rep in term.replies:
                os.write(fd, rep.encode())
            term.replies.clear()
    try:
        os.kill(pid, 15)
        os.waitpid(pid, 0)
    except OSError:
        pass
    os.close(fd)
    return term


def load_font(path=None, size=16):
    p = path or psf.find_font(size)
    if not p:
        sys.exit("no console font found: install the Terminus fonts (apk add font-terminus) or pass --font")
    return psf.PSFFont(p)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Save a CRT-style picture of a program's screen.")
    ap.add_argument("-o", "--out", default="screen.png", help="PNG to write")
    ap.add_argument("--size", default="1280x800", help="picture size, e.g. 1920x1080")
    ap.add_argument("--theme", default="green", help="green, amber, white, cyan or colour")
    ap.add_argument("--effects", default="full", help="off, light or full")
    ap.add_argument("--font", help="a PSF console font (default: Terminus)")
    ap.add_argument("--font-size", type=int, default=16, help="Terminus height: 12-32")
    ap.add_argument("--scale", type=int, default=0, help="pixels per font pixel (0 = biggest that fits 100x30)")
    ap.add_argument("--wait", type=float, default=2.0, help="seconds to let the program draw")
    ap.add_argument("--keys", default="", help=r"keys to press after 1 s, e.g. '\x1bOB\x1bOB' (Python escapes)")
    ap.add_argument("--power", choices=("on", "off"), help="save the power on/off animation as numbered PNGs")
    ap.add_argument("--frames", type=int, default=12)
    ap.add_argument("cmd", nargs="+", help="the program to run (after --)")
    a = ap.parse_args(argv)
    w, h = (int(v) for v in a.size.lower().split("x"))
    font = load_font(a.font, a.font_size)
    scale = a.scale or best_scale(font, (w, h), a.effects)
    r = Renderer(font, (w, h), scale, a.theme, a.effects)
    keys = [(1.0, a.keys.encode().decode("unicode_escape"))] if a.keys else []
    term = run_in_pty(a.cmd, r.rows, r.cols, a.wait, keys)
    t0 = time.time()
    frame = r.render(term)
    took = time.time() - t0
    if a.power:
        canvas = r.canvas(term.grid)
        base, ext = os.path.splitext(a.out)
        for i in range(a.frames):
            t = i / (a.frames - 1)
            write_png(f"{base}-{i:02d}{ext}", r.finish(r.power(canvas, t, a.power == "on"), persist=False))
    write_png(a.out, frame)
    print(f"{a.out}: {w}x{h}, {r.cols}x{r.rows} characters, font {os.path.basename(font.path)} "
          f"x{scale}, rendered in {took * 1000:.0f} ms")


if __name__ == "__main__":
    main()
