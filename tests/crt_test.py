#!/usr/bin/env python3
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Checks for the graphical screen's building blocks: the terminal emulator (vt.py),
the console font reader (psf.py) and the CRT renderer (crt.py). Needs numpy.

  python3 tests/crt_test.py
  python3 tests/crt_test.py --menu     also run the real menu and render it
"""
import gzip
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "retromenu"))

try:
    import numpy as np
except ImportError:
    sys.exit("needs numpy (Alpine: apk add py3-numpy)")

import crt  # noqa: E402
import psf  # noqa: E402
import vt  # noqa: E402

FAILS = []


def check(name, ok, info=""):
    print(("ok    " if ok else "FAIL  ") + name + (f"  ({info})" if info and not ok else ""))
    if not ok:
        FAILS.append(name)


# ------------------------------------------------------------------ a tiny test font
def make_psf2(path, chars, w=8, h=16):
    """A PSF2 font where glyph i has row i % h lit (easy to recognise), with a unicode table."""
    rowb = (w + 7) // 8
    glyphs, table = b"", b""
    for i, ch in enumerate(chars):
        for y in range(h):
            glyphs += (b"\xff" * rowb) if y == i % h else (b"\x00" * rowb)
        table += ch.encode() + b"\xff"
    hdr = struct.pack("<4sIIIIIII", b"\x72\xb5\x4a\x86", 0, 32, 1, len(chars), h * rowb, h, w)
    with gzip.open(path, "wb") as f:
        f.write(hdr + glyphs + table)


def make_psf1(path, h=14):
    """A 256-glyph PSF1 font without a unicode table: glyph n = character n."""
    glyphs = bytes((n if y == 0 else 0) for n in range(256) for y in range(h))
    with open(path, "wb") as f:
        f.write(b"\x36\x04" + bytes([0, h]) + glyphs)


def test_fonts(tmp):
    p2 = os.path.join(tmp, "t.psf.gz")
    make_psf2(p2, "?AB█")
    f = psf.PSFFont(p2)
    check("psf2: size", (f.width, f.height) == (8, 16))
    check("psf2: unicode table", f.glyph("B")[2].all() and not f.glyph("B")[1].any())
    check("psf2: unknown char shows '?'", (f.glyph("Z") == f.glyph("?")).all())
    p1 = os.path.join(tmp, "t1.psf")
    make_psf1(p1)
    f1 = psf.PSFFont(p1)
    g = f1.glyph("A")[0]
    check("psf1: plain 256 glyphs", (f1.width, f1.height) == (8, 14) and list(np.packbits(g > 0)) == [65])
    return f


def test_box_glyphs():
    for h, w in ((16, 8), (32, 16), (20, 10)):
        g = {c: psf.drawn_glyph(c, h, w) > 0 for c in "═║╔╗╚╝╦╩╠╣╬─│┼"}
        # lines must reach the cell edges, so neighbouring cells join up

        def runs(a):
            return int(np.sum(a[1:] & ~a[:-1]) + a[0])
        check(f"box {w}x{h}: ═ is two lines across the cell",
              runs(g["═"][:, 0]) == 2 and (g["═"][:, 0] == g["═"][:, -1]).all())
        check(f"box {w}x{h}: ║ is two lines down the cell",
              runs(g["║"][0]) == 2 and (g["║"][0] == g["║"][-1]).all())
        check(f"box {w}x{h}: ╔ joins ═ and ║",
              (g["╔"][:, -1] == g["═"][:, -1]).all() and (g["╔"][-1] == g["║"][-1]).all()
              and not g["╔"][0].any() and not g["╔"][:, 0].any())
        check(f"box {w}x{h}: ╬ open in the middle", not g["╬"][h // 2, w // 2])
        check(f"box {w}x{h}: ┼ joins ─ and │",
              (g["┼"][:, 0] == g["─"][:, 0]).all() and (g["┼"][0] == g["│"][0]).all())


def test_vt():
    t = vt.VT(5, 20)
    t.feed("hello\r\nworld")
    check("vt: text and newline", t.text().split("\n")[:2] == ["hello", "world"])
    t.feed("\x1b[2J\x1b[3;4HX\x1b[1;31mR\x1b[0m")
    check("vt: cursor position", t.grid[2][3][0] == "X")
    check("vt: colours", t.grid[2][4][1] == 1 and t.grid[2][4][3] & vt.BOLD)
    t.feed("\x1b[38;5;120mA\x1b[38;2;1;2;3mB")
    check("vt: 256 and true colour", t.grid[2][5][1] == 120 and t.grid[2][6][1] == vt.RGB | 0x010203)
    t.feed("\x1b[")  # cut in the middle of a sequence ...
    t.feed("5;1H")   # ... and the rest arrives later
    check("vt: sequence split over two reads", (t.y, t.x) == (4, 0))
    t.feed("\x1b[1;1H\x1b[6n")
    check("vt: cursor position report", t.replies == ["\x1b[1;1R"])
    t.feed("\x1b[?1049h")
    check("vt: alternate screen is empty", t.text().strip() == "")
    t.feed("alt\x1b[?1049l")
    check("vt: back from alternate screen", "X" in t.text() and "alt" not in t.text())
    t = vt.VT(3, 10)
    t.feed("1\r\n2\r\n3\r\n4")
    check("vt: scrolls at the bottom", t.text() == "2\n3\n4")
    t.feed("\x1b(0lqk\x1b(B")
    check("vt: DEC line drawing", "┌─┐" in t.text())
    t.feed("\x1b[?1h")
    check("vt: application cursor keys", t.key("up") == "\x1bOA")
    t.feed("\x1b[?1000h\x1b[?1006h")
    check("vt: SGR mouse report", t.mouse_report(0, 4, 2) == "\x1b[<0;5;3M")
    t = vt.VT(2, 5)
    t.feed("abcdefg")
    check("vt: wraps at the right edge", t.text() == "abcde\nfg")


def test_renderer(font, tmp):
    for theme, fx, scale in (("green", "full", 1), ("amber", "light", 2), ("colour", "off", 1), ("cyan", "full", 2)):
        r = crt.Renderer(font, (400, 300), scale, theme, fx)
        t = vt.VT(r.rows, r.cols)
        t.feed("\x1b[1;1HAB \x1b[7mA\x1b[0m █")
        img = r.render(t)
        check(f"render {theme}/{fx}/x{scale}: picture size", img.shape == (300, 400, 4) and img.dtype == np.uint8)
        check(f"render {theme}/{fx}/x{scale}: something lit", int(img[..., :3].max()) > 150)
        for i, on in ((0.1, True), (0.6, True), (0.5, False), (0.95, False)):
            r.finish(r.power(r.canvas(t.grid), i, on), persist=False)
    check("render: grid fills the screen", r.cols * r.cw <= 400 and r.rows * r.ch <= 300)
    path = os.path.join(tmp, "x.png")
    crt.write_png(path, img)
    with open(path, "rb") as f:
        head = f.read(24)
    check("png: written", head[:8] == b"\x89PNG\r\n\x1a\n" and struct.unpack(">II", head[16:24]) == (400, 300))


def test_deck(font, tmp):
    """The Desk's picture and keys, without starting any programs."""
    import deck
    r = crt.Renderer(font, (1600, 900), 1, "amber", "light")
    started = []

    class FakeChild:
        def __init__(self, cmd, rows, cols):
            started.append((cmd, rows, cols))

        def write(self, text):
            started.append(("keys", text))

        def resize(self, rows, cols):
            pass

        def stop(self):
            pass

    d = deck.Deck(r, ["menu"], FakeChild, os.path.join(tmp, "none.json"))
    img = d.compose(0.0)
    check("deck: picture covers the text area", img.shape == (r.rows * font.height, r.cols * font.width))
    check("deck: something drawn", float(img.max()) > 0.5)
    d.key("4", lambda t, term: t)
    check("deck: offline module opens a card", d.shown is not None and d.shown.info is not None)
    check("deck: q closes the card first", d.key("q", lambda t, term: t) is None and d.shown is None)
    d.key("1", lambda t, term: t)
    check("deck: module 01 starts a program", started and started[0][1:] == d.inner and d.focus == "window")
    d.key("ls\r", lambda t, term: t)
    check("deck: keys go to the program", ("keys", "ls\r") in started)
    d.key("\x1b[24~", lambda t, term: t)
    check("deck: F12 gives the keys back to the deck", d.focus == "deck")
    check("deck: picture with a window", d.compose(1.0).shape == img.shape)
    check("deck: q returns to the menu", d.key("q", lambda t, term: t) == "exit")
    r.update_image(d.compose(2.0))
    check("render: deck picture becomes a frame", int(r.picture()[..., :3].max()) > 100)


def test_menu(tmp):
    """The real menu in a pseudo-terminal, read by vt.py and drawn by crt.py."""
    font = psf.PSFFont(psf.find_font(16)) if psf.find_font(16) else None
    if not font:
        check("menu: needs a console font (apk add font-terminus)", False)
        return
    r = crt.Renderer(font, (1280, 800), 1, "green", "full")
    menu = os.path.join(HERE, "..", "retromenu", "retromenu.py")
    t = crt.run_in_pty([sys.executable, menu], r.rows, r.cols, wait=3,
                       env={"HOME": tmp, "XDG_CONFIG_HOME": os.path.join(tmp, ".config")})
    check("menu: drawn through vt.py", "MAIN MENU" in t.text(), t.text()[:400])
    out = os.path.join(tmp, "menu.png")
    crt.write_png(out, r.render(t))
    print("      picture: " + out)


def test_screen(tmp, font_path=None):
    """pontes screen drawing into a file instead of a display, with keys typed on the 'console'."""
    import pty
    import select
    import signal
    import time
    fbfile = os.path.join(tmp, "fb.raw")
    args = [sys.executable, os.path.join(HERE, "..", "retromenu", "pontes.py"), "screen",
            "--fb-file", fbfile, "--size", "1024x768", "--effects", "full"]
    if font_path:
        args += ["--font", font_path]
    pid, fd = pty.fork()
    if pid == 0:
        os.environ.update(HOME=tmp, XDG_CONFIG_HOME=os.path.join(tmp, ".config"))
        os.execv(args[0], args)
    start, sent = time.time(), False
    while time.time() - start < 5:
        if not sent and time.time() - start > 3:
            os.write(fd, b"\x1b[B")  # Down, as the Linux console sends it
            sent = True
        if select.select([fd], [], [], 0.1)[0]:
            try:
                os.read(fd, 4096)
            except OSError:
                break
    pic = np.fromfile(fbfile, np.uint8).reshape(768, 1024, 4)
    check("screen: draws on the display", int(pic[..., 1].max()) > 150)
    os.kill(pid, signal.SIGTERM)
    os.waitpid(pid, 0)
    crt.write_png(os.path.join(tmp, "screen.png"), pic)
    print("      picture: " + os.path.join(tmp, "screen.png"))


def main():
    tmp = tempfile.mkdtemp(prefix="pcal-crt-")
    font = test_fonts(tmp)
    test_box_glyphs()
    test_vt()
    test_renderer(font, tmp)
    test_deck(font, tmp)
    if "--menu" in sys.argv:
        test_menu(tmp)
        test_screen(tmp)
    print(f"\n{'ALL OK' if not FAILS else str(len(FAILS)) + ' FAILED'}")
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
