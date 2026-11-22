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
    #        glow  scanlines curvature vignette afterglow noise
    "off":   (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "light": (0.35, 0.25, 0.0, 0.25, 0.0, 0.0),
    "full":  (0.55, 0.40, 0.045, 0.45, 0.45, 0.015),
}


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
    Draws terminal cells as a CRT picture.

    font:   a psf.PSFFont        scale:  how many screen pixels per font pixel (1, 2, 3...)
    theme:  green/amber/white/cyan, or "colour" for a colour monitor
    effects: "off", "light", "full", or a dict with any of
             glow, scanlines, curvature, vignette, afterglow, noise (0..1)
    """

    def __init__(self, font, size, scale=1, theme="green", effects="full", margin=None):
        self.font, self.scale = font, max(1, int(scale))
        self.out_w, self.out_h = size
        lv = EFFECT_LEVELS.get(effects, EFFECT_LEVELS["full"]) if isinstance(effects, str) else None
        names = ("glow", "scanlines", "curvature", "vignette", "afterglow", "noise")
        self.fx = dict(zip(names, lv)) if lv else {k: float(effects.get(k, 0)) for k in names}
        self.mono = theme in PHOSPHOR
        self.theme = theme if self.mono else "colour"
        self.cw, self.ch = font.width * self.scale, font.height * self.scale
        self.cols, self.rows = self.grid(font, size, self.scale, self.fx, margin)
        self.ox = (self.out_w - self.cols * self.cw) // 2
        self.oy = (self.out_h - self.rows * self.ch) // 2
        self.tiles = {}
        self.rowcache = {}
        self.prev = None
        self._build_lut()
        self._build_static()

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
        """Intensity (0..1.5, 1024 steps) -> phosphor RGB, for the monochrome monitors."""
        if not self.mono:
            return
        glass, dim, normal, bright = (_hex(h) for h in PHOSPHOR[self.theme])
        n, b, d = (luminance(PALETTE[i]) for i in MENU_COLOURS[self.theme])
        self.levels = {"dim": d, "normal": n, "bright": b}
        white = np.ones(3, np.float32)
        xs = [0.0, d, n, b, 1.0, 1.5]
        stops = [glass, dim, normal, bright, bright * 0.75 + white * 0.25, white]
        t = np.linspace(0, 1.5, 1024, dtype=np.float32)
        self.lut = np.stack([np.interp(t, xs, [s[c] for s in stops]) for c in range(3)], 1)
        lut8 = (np.clip(self.lut, 0, 1) * 255 + 0.5).astype(np.uint32)
        # packed as 32-bit RGBX pixels: one lookup per pixel, and what displays want anyway
        self.lut32 = (lut8[:, 0] | lut8[:, 1] << 8 | lut8[:, 2] << 16 | np.uint32(255) << 24)
        if sys.byteorder == "big":
            self.lut32 = self.lut32.byteswap()

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

    # ------------------------------------------------------------ cells -> picture
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

    def cells_image(self, grid, cursor=None):
        """The terminal at font resolution: (rows*h, cols*w) intensity or (.., .., 3) RGB."""
        rows = []
        for y, row in enumerate(grid[: self.rows]):
            row = tuple(row[: self.cols])
            if cursor and cursor[1] == y:
                cx = cursor[0]
                if cx < len(row):
                    ch, fg, bg, fl = row[cx]
                    row = row[:cx] + ((ch, fg, bg, fl ^ vtmod.REV),) + row[cx + 1:]
            img = self.rowcache.get(row)
            if img is None:
                img = np.concatenate([self.tile(c) for c in row], axis=1)
                if len(self.rowcache) > 400:
                    self.rowcache.clear()
                self.rowcache[row] = img
            rows.append(img)
        img = np.concatenate(rows, axis=0)
        need_w, need_h = self.cols * self.font.width, self.rows * self.font.height
        if img.shape[0] < need_h or img.shape[1] < need_w:
            pad = [(0, need_h - img.shape[0]), (0, need_w - img.shape[1])] + ([(0, 0)] if img.ndim == 3 else [])
            img = np.pad(img, pad)
        return img

    # ------------------------------------------------------------ effects
    def _build_static(self):
        """Things that only depend on the picture size: scanline pattern, tube shape."""
        s = self.scale
        sl = self.fx["scanlines"]
        if s >= 2:
            pat = np.ones(s, np.float32)
            pat[-1] = 1 - sl
            if s >= 4:
                pat[0] = 1 - sl * 0.35
            self.scan = np.tile(pat, self.rows * self.font.height)[:, None]
        else:
            self.scan = np.where(np.arange(self.rows * self.ch) % 2, 1 - sl * 0.6, 1).astype(np.float32)[:, None]
        W, H = self.out_w, self.out_h
        yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
        u = (xx + 0.5) / W * 2 - 1
        v = (yy + 0.5) / H * 2 - 1
        del xx, yy
        k = self.fx["curvature"]
        self.remap = self.outside = None
        if k > 0:  # barrel distortion: sample further out towards the edges
            su = u * (1 + k * v * v) * (1 + k * 0.5)
            sv = v * (1 + k * u * u) * (1 + k * 0.5)
            # The glass: a rounded rectangle (superellipse); black bezel outside it.
            p = 14
            inside = ((np.abs(su) ** p + np.abs(sv) ** p) ** (1 / p) < 1).ravel()
            self.inside = np.flatnonzero(inside)
            self.outside = np.flatnonzero(~inside)
            fx = np.clip((su + 1) / 2 * W - 0.5, 0, W - 1.001).ravel()[self.inside]
            fy = np.clip((sv + 1) / 2 * H - 0.5, 0, H - 1.001).ravel()[self.inside]
            x0, y0 = fx.astype(np.int32), fy.astype(np.int32)
            self.remap = y0 * W + x0
            # bilinear sampling at scale 1, so thin strokes stay smooth instead of breaking up
            self.remap_w = (fx - x0, fy - y0) if self.scale == 1 else None
            del su, sv, fx, fy
        self.shade = (1 - self.fx["vignette"] * np.clip((u * u * 0.8 + v * v) * 0.55, 0, 1) ** 1.5
                      ).astype(np.float32)
        if not self.mono:
            self.shade = self.shade[..., None]

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
    def _upsample(img, H, W):
        """Bilinear resize of a small picture to H x W (first along x while it is small)."""
        h, w = img.shape[:2]
        xs = np.clip((np.arange(W, dtype=np.float32) + 0.5) * w / W - 0.5, 0, w - 1)
        x0 = xs.astype(np.int32)
        x1 = np.minimum(x0 + 1, w - 1)
        fx = (xs - x0)[None, :, None] if img.ndim == 3 else (xs - x0)[None, :]
        a = img[:, x0]
        tmp = a + (img[:, x1] - a) * fx
        ys = np.clip((np.arange(H, dtype=np.float32) + 0.5) * h / H - 0.5, 0, h - 1)
        y0 = ys.astype(np.int32)
        y1 = np.minimum(y0 + 1, h - 1)
        fy = (ys - y0)[:, None, None] if img.ndim == 3 else (ys - y0)[:, None]
        out = tmp[y0]
        out += (tmp[y1] - out) * fy
        return out

    def canvas(self, grid, cursor=None):
        """Stage 1: cells -> full-size picture with glow and scanlines (no glass yet)."""
        base = self.cells_image(grid, cursor)
        s = self.scale
        out = np.zeros((self.out_h, self.out_w) + base.shape[2:], np.float32)
        h, w = base.shape[0] * s, base.shape[1] * s
        region = out[self.oy:self.oy + h, self.ox:self.ox + w]
        region[...] = np.repeat(np.repeat(base, s, axis=0), s, axis=1) if s > 1 else base
        if self.fx["scanlines"] > 0:
            region *= self.scan if region.ndim == 2 else self.scan[..., None]
        glow = self.fx["glow"]
        if glow > 0:
            f = 4
            bh, bw = base.shape[0] // f * f, base.shape[1] // f * f
            b = base[:bh, :bw]
            rows = b[0::4] + b[1::4] + b[2::4] + b[3::4]
            small = (rows[:, 0::4] + rows[:, 1::4] + rows[:, 2::4] + rows[:, 3::4]) * (1 / 16)
            halo = self._upsample(self._blur(small * glow, 2), h, w)
            region += halo
        return out

    def finish(self, canvas, persist=True):
        """Stage 2: afterglow, curved glass, vignette, colours -> (h, w, 4) uint8 RGBX picture."""
        ag = self.fx["afterglow"]
        if persist and ag > 0:
            if self.prev is not None and self.prev.shape == canvas.shape:
                np.maximum(canvas, self.prev * ag, out=canvas)
            self.prev = canvas.copy()
        if self.remap is not None:
            flat = canvas.reshape(-1, *canvas.shape[2:])
            i = self.remap
            if self.remap_w is not None:
                W = canvas.shape[1]
                wx, wy = self.remap_w
                if flat.ndim == 2:
                    wx, wy = wx[:, None], wy[:, None]
                a = flat[i]
                top = a + (flat[i + 1] - a) * wx
                b = flat[i + W]
                bot = b + (flat[i + W + 1] - b) * wx
                vals = top + (bot - top) * wy
            else:
                vals = flat[i]
            res = np.zeros_like(flat)
            res[self.inside] = vals
            canvas = res.reshape(canvas.shape)
        canvas *= self.shade
        level = 1 - self.fx["noise"] * np.random.random() if self.fx["noise"] > 0 else 1
        if self.mono:
            idx = np.minimum(canvas * (1023 / 1.5 * level), 1023).astype(np.uint16)
            px = np.take(self.lut32, idx)
        else:
            c8 = (np.clip(canvas * level, 0, 1) * 255 + 0.5).astype(np.uint32)
            px = c8[..., 0] | c8[..., 1] << 8 | c8[..., 2] << 16 | np.uint32(255) << 24
            if sys.byteorder == "big":
                px = px.byteswap()
        if self.outside is not None:
            px.reshape(-1)[self.outside] = 0
        return px.view(np.uint8).reshape(px.shape + (4,))

    def render(self, term, cursor_on=True):
        """One finished frame of a vt.VT terminal."""
        cur = (term.x, term.y) if cursor_on and term.cursor_visible else None
        return self.finish(self.canvas(term.grid, cur))

    # ------------------------------------------------------------ power on / off
    def power(self, canvas, t, on=True):
        """
        The tube switching on (t 0 -> 1) or off. on: a bright line opens into the picture.
        off: the picture collapses into a line, the line into a dot, and the dot fades.
        Returns a canvas for finish(..., persist=False).
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
