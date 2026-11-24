# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
pontes screen: the graphical PCal screen.

Runs a program (by default the PCal menu) in a pseudo-terminal and shows it as an old
CRT monitor, drawn straight onto the display through the Linux framebuffer (/dev/fb0).
No X or Wayland. Keys typed on the console go to the program; Alt+F1..F6 still switch
consoles. When there is no usable display, it simply runs the program in text mode.

  pontes screen                       the menu, with the theme and effects from Settings
  pontes screen -- htop               any other program
  pontes screen --bench               measure the drawing speed (no display needed)

Needs numpy, a console font (font-terminus) and access to /dev/fb0 (group "video").
"""
import argparse
import codecs
import fcntl
import json
import os
import re
import select
import signal
import struct
import sys
import termios
import time
import tty

try:
    import numpy as np
except ImportError:  # checked in entry(): without numpy the screen falls back to text mode
    np = None

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

CONFIG_FILE = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                           "retromenu", "config.json")
EFFECTS = {"off": "off", "subtle": "light", "light": "light", "full": "full"}

# ioctls (linux/fb.h, linux/kd.h, linux/vt.h)
FBIOGET_VSCREENINFO, FBIOGET_FSCREENINFO = 0x4600, 0x4602
KDGETMODE, KDSETMODE, KD_TEXT, KD_GRAPHICS = 0x4B3B, 0x4B3A, 0, 1
VT_GETMODE, VT_SETMODE, VT_RELDISP, VT_AUTO, VT_PROCESS, VT_ACKACQ = 0x5601, 0x5602, 0x5605, 0, 1, 2

# Keys as the Linux console sends them -> names (sent on as the xterm keys the program expects)
CONSOLE_KEYS = {
    "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
    "\x1b[1~": "home", "\x1b[4~": "end", "\x1b[H": "home", "\x1b[F": "end",
    "\x1b[[A": "f1", "\x1b[[B": "f2", "\x1b[[C": "f3", "\x1b[[D": "f4", "\x1b[[E": "f5",
}
CONSOLE_KEY_RE = re.compile("|".join(re.escape(k) for k in sorted(CONSOLE_KEYS, key=len, reverse=True)))


def log(msg):
    sys.stderr.write(f"pontes screen: {msg}\n")


SCREEN_EFFECTS = ("glow", "scanlines", "curvature", "afterglow")


MARK_FILE = "/run/pcal/screen-tty"  # which terminal is the menu's inside the graphical screen


def load_config(path=None):
    try:
        with open(path or CONFIG_FILE) as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def user_config_file(pw):
    """The menu's settings file of a user (a pwd entry)."""
    return os.path.join(pw.pw_dir, ".config", "retromenu", "config.json")


def resolve(cfg, theme=None, effects=None, font_size=0):
    """
    What the screen should look like: Settings (config.json), overridden by command line options.
    Effects OFF / SUBTLE / FULL is the starting point; Settings > Graphical screen can switch
    glow, scanlines, curvature and afterglow on or off one by one.
    """
    import crt
    level = EFFECTS.get(effects or cfg.get("effects", "subtle"), "light")
    preset = dict(zip(crt.EFFECT_NAMES, crt.EFFECT_LEVELS[level]))
    full = dict(zip(crt.EFFECT_NAMES, crt.EFFECT_LEVELS["full"]))
    fx = dict(preset)
    if not effects:  # chosen in Settings, not on the command line
        for k in SCREEN_EFFECTS:
            v = cfg.get("screen_" + k)
            if v is True:
                fx[k] = preset[k] or full[k]
            elif v is False:
                fx[k] = 0.0
    try:
        size = int(font_size or cfg.get("screen_font") or 0)
    except (TypeError, ValueError):
        size = 0
    return {"theme": theme or cfg.get("theme", "green"), "fx": fx, "level": level,
            "power": level != "off", "font_size": size}


# ---------------------------------------------------------------- the display
class Framebuffer:
    """The Linux framebuffer (/dev/fb0), or a plain file for testing (--fb-file)."""

    def __init__(self, path="/dev/fb0", size=None):
        import mmap

        import numpy as np
        if size:  # a test "display": a file of RGBX pixels
            self.w, self.h = size
            self.fmt = {"bpp": 32, "r": (0, 8), "g": (8, 8), "b": (16, 8), "x": (24, 8)}
            self.stride = self.w * 4
            self.fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o644)
            os.ftruncate(self.fd, self.stride * self.h)
            yoff = 0
        else:
            self.fd = os.open(path, os.O_RDWR)
            var = bytearray(160)
            fcntl.ioctl(self.fd, FBIOGET_VSCREENINFO, var)
            v = struct.unpack_from("8I12I", var)
            self.w, self.h, yoff, bpp = v[0], v[1], v[5], v[6]
            ro, rl, _, go, gl, _, bo, bl, _, to, tl, _ = v[8:20]
            fix = bytearray(struct.calcsize("@16sLIIIIHHHI") + 32)
            fcntl.ioctl(self.fd, FBIOGET_FSCREENINFO, fix)
            self.stride = struct.unpack_from("@16sLIIIIHHHI", fix)[-1]
            if bpp not in (16, 32):
                raise OSError(f"{bpp}-bit framebuffer not supported")
            self.fmt = {"bpp": bpp, "r": (ro, rl), "g": (go, gl), "b": (bo, bl)}
            if tl:
                self.fmt["x"] = (to, tl)
        self.bytespp = self.fmt["bpp"] // 8
        self.offset = yoff * self.stride
        self.mm = mmap.mmap(self.fd, self.offset + self.stride * self.h)
        dt = np.uint32 if self.bytespp == 4 else np.uint16
        self.pixels = np.ndarray((self.h, self.stride // self.bytespp), dt, self.mm, self.offset)

    def show(self, frame, rows=None):
        """Copy rows [(top, bottom), ...] of a frame to the display (all rows if None)."""
        for lo, hi in rows or [(0, self.h)]:
            self.pixels[lo:hi, :self.w] = frame[lo:hi]

    def clear(self):
        self.pixels[:] = 0

    def close(self):
        try:
            self.mm.close()
        finally:
            os.close(self.fd)


class Console:
    """The virtual console we run on: graphics mode, raw keys, and console switching."""

    def __init__(self):
        self.fd = sys.stdin.fileno()
        self.active = True
        self.redraw = False
        self.is_vt = False
        try:
            fcntl.ioctl(self.fd, KDGETMODE, bytearray(4))
            self.is_vt = True
        except OSError:
            pass
        self.saved_tty = termios.tcgetattr(self.fd) if os.isatty(self.fd) else None

    def __enter__(self):
        # Being stopped must still give the console back (text mode, normal keys).
        for sig in (signal.SIGTERM, signal.SIGHUP):
            signal.signal(sig, lambda n, _f: sys.exit(128 + n))
        if self.saved_tty:
            tty.setraw(self.fd)
        if self.is_vt:
            # The kernel stops drawing text; we get told before and after a console switch.
            fcntl.ioctl(self.fd, KDSETMODE, KD_GRAPHICS)
            signal.signal(signal.SIGUSR1, self._release)
            signal.signal(signal.SIGUSR2, self._acquire)
            fcntl.ioctl(self.fd, VT_SETMODE, struct.pack("bbhhh", VT_PROCESS, 0, signal.SIGUSR1, signal.SIGUSR2, 0))
        return self

    def _release(self, *_):  # leaving our console (Alt+Fn): stop drawing, then allow it
        self.active = False
        fcntl.ioctl(self.fd, VT_RELDISP, 1)

    def _acquire(self, *_):  # back on our console: redraw everything
        fcntl.ioctl(self.fd, VT_RELDISP, VT_ACKACQ)
        self.active = True
        self.redraw = True

    def __exit__(self, *exc):
        if self.is_vt:
            try:
                fcntl.ioctl(self.fd, VT_SETMODE, struct.pack("bbhhh", VT_AUTO, 0, 0, 0, 0))
            finally:
                fcntl.ioctl(self.fd, KDSETMODE, KD_TEXT)
        if self.saved_tty:
            termios.tcsetattr(self.fd, termios.TCSADRAIN, self.saved_tty)
        return False


# ---------------------------------------------------------------- the program inside
class Child:
    def __init__(self, cmd, rows, cols, user=None, mark=False):
        """user: a pwd entry to run the program as (when the screen runs as root).
        mark: this is the menu's terminal; written to MARK_FILE so the login shell and the
        menu can tell they are inside the graphical screen (login clears the environment)."""
        import pty
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            # no COLUMNS/LINES: curses would believe them over the real size, also after a resize
            env = dict(os.environ, TERM="xterm-256color", PCAL_SCREEN="1")
            env.pop("COLUMNS", None)
            env.pop("LINES", None)
            try:
                if mark and os.geteuid() == 0:
                    os.makedirs(os.path.dirname(MARK_FILE), exist_ok=True)
                    with open(MARK_FILE, "w") as f:
                        f.write(os.ttyname(0) + "\n")
                if user is not None:
                    if os.geteuid() == 0 and user.pw_uid != 0:
                        os.initgroups(user.pw_name, user.pw_gid)
                        os.setgid(user.pw_gid)
                        os.setuid(user.pw_uid)
                    env.update(HOME=user.pw_dir, USER=user.pw_name, LOGNAME=user.pw_name,
                               SHELL=user.pw_shell or "/bin/sh")
                    try:
                        os.chdir(user.pw_dir)
                    except OSError:
                        os.chdir("/")
                os.execvpe(cmd[0], cmd, env)
            except OSError as e:
                sys.stderr.write(f"{cmd[0]}: {e}\n")
            os._exit(127)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        self.status = None

    def resize(self, rows, cols):
        """New terminal size: the program gets SIGWINCH and redraws."""
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))

    def write(self, text):
        try:
            os.write(self.fd, text.encode())
        except OSError:
            pass

    def read(self):
        try:
            return os.read(self.fd, 65536)
        except OSError:  # the program has ended
            return b""

    def operator(self):
        """The user this terminal belongs to now, as a pwd entry, found from the kernel (never
        from what a program says): whoever logged in through the login screen. Every process
        started under this terminal's program counts; a non-root one wins (the most recently
        started), so a user's menu never opens root modules, whatever ran between (login, su,
        doas). Only when everything under it runs as root (root logged in) is it root."""
        import pwd
        procs = {}  # pid -> (parent pid, uid, start time)
        for d in os.listdir("/proc"):
            if not d.isdigit():
                continue
            try:
                with open(f"/proc/{d}/stat") as f:
                    fields = f.read().rsplit(")", 1)[1].split()
                with open(f"/proc/{d}/status") as f:
                    u = next(int(line.split()[1]) for line in f if line.startswith("Uid:"))
                procs[int(d)] = (int(fields[1]), u, int(fields[19]))
            except (OSError, ValueError, StopIteration, IndexError):
                continue
        children = {}
        for pid, (ppid, _, _) in procs.items():
            children.setdefault(ppid, []).append(pid)
        best, todo = None, [self.pid]
        while todo:
            pid = todo.pop()
            if pid in procs:
                _, u, start = procs[pid]
                key = (u != 0, start)
                if best is None or key > best[0]:
                    best = (key, u)
            todo.extend(children.get(pid, []))
        try:
            return pwd.getpwuid(best[1] if best else os.getuid())
        except KeyError:
            return None

    def wait(self):
        if self.status is None:
            _, st = os.waitpid(self.pid, 0)
            self.status = os.waitstatus_to_exitcode(st)
        return self.status

    def stop(self):
        try:
            os.kill(self.pid, signal.SIGHUP)
        except OSError:
            pass


def translate_keys(data, term):
    """Console key codes -> what an xterm sends (honours the program's cursor key mode)."""
    return CONSOLE_KEY_RE.sub(lambda m: term.key(CONSOLE_KEYS[m.group(0)]), data)


# ---------------------------------------------------------------- main loop
class Pane:
    """A program on the screen: its process, its terminal, its text decoder."""

    def __init__(self, child, term):
        self.child, self.term = child, term
        self.dec = codecs.getincrementaldecoder("utf-8")("replace")


def menu_program():
    """How to start the PCal menu: the release program, or the Python source."""
    if getattr(sys, "frozen", False):
        return [sys.executable, "menu"]
    return [sys.executable, os.path.join(HERE, "retromenu.py")]


def run(cmd, fb, make, first=None, max_fps=40):
    """make(config_file) -> (renderer, power effect on?), called again when Settings change."""
    import vt
    renderer, effects_on = first or make(None)
    menu = Pane(Child(cmd, renderer.rows, renderer.cols, mark=True), vt.VT(renderer.rows, renderer.cols))
    session = Session(cmd, fb, make, renderer, effects_on, menu, max_fps)
    try:
        return session.loop()
    finally:
        for p in [menu] + (session.deck.panes() if session.deck else []):
            if p.child.status is None:
                p.child.stop()
        if os.geteuid() == 0:
            try:
                os.unlink(MARK_FILE)  # that terminal is no longer the menu's
            except OSError:
                pass


class Session:
    """Everything on the screen: the menu, and the Desk with its module windows."""

    def __init__(self, cmd, fb, make, renderer, effects_on, menu, max_fps):
        self.cmd, self.fb, self.make = cmd, fb, make
        self.renderer, self.effects_on = renderer, effects_on
        self.menu, self.max_fps = menu, max_fps
        self.deck = None
        self.view = "menu"  # or "deck"
        self.need = True
        self.user = None    # who uses the menu: Desk modules run as this user
        self.config_file = None
        self.opts = make.options(None)

    def spawn(self, cmd, rows, cols):
        return Child(cmd, rows, cols, user=self.user)

    def operator(self):
        """Find out who is using the menu now (after the login screen, that is who logged in)."""
        pw = self.menu.child.operator()
        if pw is not None:
            self.user, self.config_file = pw, user_config_file(pw)
        return pw

    def open_deck(self):
        import deck
        pw = self.operator()
        if self.deck is not None and pw is not None and self.deck.owner != pw.pw_uid:
            for w in list(self.deck.windows):  # someone else logged in: their deck, not the old one
                self.deck.close(w)
            self.deck = None
        if self.deck is None:
            self.deck = deck.Deck(self.renderer, menu_program(), self.spawn, self.config_file or CONFIG_FILE, pw)
        else:
            self.deck.log("OPERATOR ON DECK")
        self.view = "deck"
        self.renderer.reset()
        self.need = True

    def close_deck(self):
        self.view = "menu"
        self.renderer.reset()
        self.need = True

    def reload(self):
        """Settings changed: a new picture, maybe a new size for every program."""
        self.operator()
        opts = self.make.options(self.config_file)
        if opts == self.opts:  # nothing that changes the picture (the menu says hello at every start)
            return
        self.opts = opts
        r, self.effects_on = self.make(self.config_file)
        t = self.menu.term
        if (r.rows, r.cols) != (t.rows, t.cols):
            t.resize(r.rows, r.cols)
            t.feed("\x1b[2J")  # the program repaints everything for the new size
            self.menu.child.resize(r.rows, r.cols)
        self.renderer = r
        if self.deck:
            self.deck.set_renderer(r)
        r.reset()
        self.fb.show(r.frame)  # the whole picture: margins and glass may have changed
        self.need = True

    def panes(self):
        """(pane, window) for every running program; window is None for the menu."""
        out = [(self.menu, None)]
        if self.deck:
            out += [(w, w) for w in self.deck.panes()]
        return out

    def feed(self, pane, data):
        pane.term.feed(pane.dec.decode(data))
        if pane.term.replies:
            pane.child.write("".join(pane.term.replies))
            pane.term.replies.clear()
        cmds, pane.term.commands[:] = list(pane.term.commands), []
        if "reload" in cmds:
            self.reload()
        if "desk" in cmds and pane is self.menu:
            self.open_deck()

    def loop(self):
        fb = self.fb
        keydec = codecs.getincrementaldecoder("utf-8")("replace")
        with Console() as con:
            if self.effects_on:
                animate(fb, self.renderer, con, self.renderer.cbuf, on=True, seconds=0.7)
            fb.show(self.renderer.frame)
            blink_on, next_blink, last_draw = True, time.monotonic() + 0.5, 0.0
            next_tick = time.monotonic() + 1.0
            while True:
                now = time.monotonic()
                timeout = max(0.0, min(next_blink, next_tick if self.view == "deck" else next_blink) - now)
                if self.renderer.animating:
                    timeout = min(timeout, 0.03)
                panes = self.panes()
                fds = {p.child.fd: (p, w) for p, w in panes}
                r, _, _ = select.select(list(fds) + [con.fd], [], [], timeout)
                ended = False
                for fd in r:
                    if fd == con.fd:
                        continue
                    pane, win = fds[fd]
                    data = pane.child.read()
                    if not data:
                        if win is None:  # the menu has ended: so does the screen
                            ended = True
                        else:
                            pane.child.wait()
                            self.deck.close(win, ended=True)
                        self.need = True
                        continue
                    # take everything that is already there, so a screen redraw is done in one go
                    deadline = time.monotonic() + 0.012
                    while time.monotonic() < deadline and select.select([fd], [], [], 0.002)[0]:
                        more = pane.child.read()
                        if not more:
                            break
                        data += more
                    self.feed(pane, data)
                    if self.view == "menu" and win is None or self.view == "deck" and win is not None:
                        blink_on, next_blink, self.need = True, time.monotonic() + 0.5, True
                if ended:
                    break
                if con.fd in r:
                    keys = os.read(con.fd, 4096)
                    if keys:
                        text = keydec.decode(keys)
                        if self.view == "menu":
                            self.menu.child.write(translate_keys(text, self.menu.term))
                        elif self.deck.key(text, translate_keys) == "exit":
                            self.close_deck()
                        self.need = True
                now = time.monotonic()
                if now >= next_blink:
                    blink_on, next_blink, self.need = not blink_on, now + 0.5, True
                if now >= next_tick:
                    next_tick = now + 1.0
                    if self.view == "deck":
                        self.need = True
                if not con.active:
                    continue
                if con.redraw:
                    con.redraw = False
                    self.renderer.reset()
                    self.need = True
                if (self.need or self.renderer.animating) and now - last_draw >= 1 / self.max_fps:
                    if self.view == "menu":
                        t = self.menu.term
                        cursor = (t.x, t.y) if blink_on and t.cursor_visible else None
                        rows = self.renderer.update(t.grid, cursor, now)
                    else:
                        img = self.deck.compose(now, blink_on)
                        if not self.renderer.mono:
                            img = img[..., None] * np.array([0.3, 1.0, 0.45], np.float32)
                        rows = self.renderer.update_image(img, now)
                    if rows:
                        fb.show(self.renderer.frame, rows)
                    self.need, last_draw = False, now
            status = self.menu.child.wait()
            if self.effects_on and con.active:
                self.renderer.update(self.menu.term.grid)
                animate(fb, self.renderer, con, self.renderer.cbuf.copy(), on=False, seconds=0.6)
            fb.clear()
        return status


def animate(fb, renderer, con, canvas, on, seconds):
    """The power on/off effect, timed by the clock (slow machines just show fewer frames)."""
    import numpy as np
    start = time.monotonic()
    dt = np.uint32 if renderer.fmt["bpp"] == 32 else np.uint16
    frame = np.zeros_like(renderer.frame, dtype=dt)
    while con.active:
        t = (time.monotonic() - start) / seconds
        if t >= 1:
            break
        pic = renderer.power(canvas, t, on)
        renderer.finish_rows(pic, 0, renderer.out_h, frame)
        fb.show(frame)
    if not on:
        fb.clear()


# ---------------------------------------------------------------- choosing a font size
def choose_layout(w, h, font_size=0, scale=0, effects="light", font_path=None):
    """Font and scale for a screen: by default the biggest font that still fits 100x30 characters."""
    import crt
    import psf
    if font_path:
        font = psf.PSFFont(font_path)
        return font, scale or crt.best_scale(font, (w, h), effects)
    sizes = [font_size] if font_size else sorted(psf.TERMINUS_SIZES, reverse=True)
    seen, last = set(), None
    for size in sizes:
        path = psf.find_font(size)
        if not path or path in seen:
            continue
        seen.add(path)
        last = font = psf.PSFFont(path)
        cols, rows = crt.Renderer.grid(font, (w, h), scale or 1, effects)
        if font_size or (cols >= 100 and rows >= 30):
            return font, scale or crt.best_scale(font, (w, h), effects)
    if last is None:
        raise OSError("no console font found (apk add font-terminus)")
    return last, scale or 1


# ---------------------------------------------------------------- speed test
def bench(size, opts, scale, font_path=None):
    import crt
    w, h = size
    theme, effects = opts["theme"], opts["fx"]
    font, s = choose_layout(w, h, opts["font_size"], scale, effects, font_path)
    t0 = time.monotonic()
    r = crt.Renderer(font, size, s, theme, effects)
    setup = time.monotonic() - t0
    menu = [sys.executable, os.path.join(HERE, "retromenu.py")]
    down = "\x1b[B"
    term = crt.run_in_pty(menu, r.rows, r.cols, wait=2.0)
    r.reset()
    t0 = time.monotonic()
    r.update(term.grid)
    first = time.monotonic() - t0
    times = []
    for _ in range(3):
        r.reset()
        t0 = time.monotonic()
        r.update(term.grid)
        times.append(time.monotonic() - t0)
    full = min(times)
    # moving the selection: the real menu's screens before and after pressing Down
    term2 = crt.run_in_pty(menu, r.rows, r.cols, wait=1.5, keys=[(1.0, down)])
    moves = []
    for i in range(6):
        g = term2.grid if i % 2 == 0 else term.grid
        t0 = time.monotonic()
        r.update(g, now=time.monotonic())
        moves.append(time.monotonic() - t0)
    print(f"screen {w}x{h}: {r.cols}x{r.rows} characters, {os.path.basename(font.path)} x{s}, "
          f"theme {theme}, effects {opts['level']} "
          f"({', '.join(k for k in SCREEN_EFFECTS if effects[k]) or 'none'})")
    print(f"  setup          {setup * 1000:6.0f} ms   (once, at start)")
    print(f"  first picture  {first * 1000:6.0f} ms")
    print(f"  full redraw    {full * 1000:6.0f} ms   (a whole new screen)")
    print(f"  menu move      {sorted(moves)[len(moves) // 2] * 1000:6.0f} ms   (typical change)")
    return 0


# ---------------------------------------------------------------- entry
def parse_size(s):
    w, h = (int(v) for v in s.lower().split("x"))
    return w, h


def text_mode(cmd, why):
    if why:
        log(f"{why}; using text mode")
    try:
        os.execvp(cmd[0], cmd)
    except OSError as e:
        log(f"{cmd[0]}: {e}")
        return 127


def entry(argv=None):
    ap = argparse.ArgumentParser(prog="pontes screen", description="The graphical PCal screen.")
    ap.add_argument("--theme", help="green, amber, white, cyan or colour (default: from Settings)")
    ap.add_argument("--effects", help="off, light or full (default: from Settings)")
    ap.add_argument("--font-size", type=int, default=0, help="Terminus height 12-32 (default: automatic)")
    ap.add_argument("--font", help="a PSF console font file (default: Terminus)")
    ap.add_argument("--scale", type=int, default=0, help="pixels per font pixel (default: automatic)")
    ap.add_argument("--fb", default="/dev/fb0", help="framebuffer device")
    ap.add_argument("--fb-file", help="draw into this file instead of a display (testing)")
    ap.add_argument("--size", default="1920x1080", help="size for --fb-file and --bench")
    ap.add_argument("--bench", action="store_true", help="measure drawing speed and exit")
    ap.add_argument("--tty1", action="store_true",
                    help="started by the system on tty1: follow Settings > Display (text or graphical)")
    ap.add_argument("--user", help="with --tty1: whose Display setting counts (default: this user)")
    ap.add_argument("cmd", nargs="*", help="program to run (default: the PCal menu)")
    a = ap.parse_args(argv)
    if a.effects and a.effects not in EFFECTS:
        ap.error(f"--effects: choose off, light or full (not {a.effects!r})")
    cmd = a.cmd or menu_program()
    if a.tty1:
        import pwd
        try:
            pw = pwd.getpwnam(a.user) if a.user else pwd.getpwuid(os.getuid())
            display = load_config(user_config_file(pw)).get("display", "graphical")
        except KeyError:
            display = "graphical"
        if display == "text":
            return text_mode(cmd, None)
    try:
        import numpy  # noqa: F401
        import crt
    except ImportError:
        return text_mode(cmd, "numpy is not installed (apk add py3-numpy)")

    def options(config_file=None):
        return resolve(load_config(config_file), a.theme, a.effects, a.font_size)

    if a.bench:
        return bench(parse_size(a.size), options(), a.scale, a.font)
    try:
        fb = Framebuffer(a.fb_file, parse_size(a.size)) if a.fb_file else Framebuffer(a.fb)
    except OSError as e:
        return text_mode(cmd, f"no graphical display ({e})")

    def make(config_file=None):
        o = options(config_file)
        font, scale = choose_layout(fb.w, fb.h, o["font_size"], a.scale, o["fx"], a.font)
        return crt.Renderer(font, (fb.w, fb.h), scale, o["theme"], o["fx"], fmt=fb.fmt), o["power"]

    make.options = options

    try:
        first = make(None)  # check that it works before taking over the console
    except OSError as e:
        fb.close()
        return text_mode(cmd, str(e))
    try:
        return run(cmd, fb, make, first)
    finally:
        fb.close()


if __name__ == "__main__":
    sys.exit(entry())
