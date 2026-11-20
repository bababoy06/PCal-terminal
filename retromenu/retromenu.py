#!/usr/bin/env python3
"""
RetroMenu - the green-phosphor, arrow-key home screen of Pontes-Calos.

Keys (everywhere):
  UP / DOWN        move            (also k / j)
  RIGHT / ENTER    open / select   (also l)
  LEFT / ESC / q   back            (also h)
  PGUP / PGDN      scroll a page
  HOME / END       first / last item

Only uses the Python standard library (curses), so on Alpine: apk add python3
"""
import curses
import grp
import json
import locale
import mimetypes
import os
import pwd
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import textwrap
import time

VERSION = "0.2"
SYSTEM_NAME = "PONTES-CALOS"
TAGLINE = "A PONTES-CALOS SYSTEM"
COPYRIGHT = "(C) PONTES-CALOS DATA SYSTEMS"

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "retromenu")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PLUGIN_DIR = os.path.join(CONFIG_DIR, "plugins")

DEFAULT_CONFIG = {
    "theme": "green",
    "monitor_name": "Top",
    "monitor_cmd": "top",
    "shell": os.environ.get("SHELL") or "/bin/sh",
    "editor": os.environ.get("EDITOR") or "vi",
    "pager": "less",
    "show_hidden": False,
    "splash": True,
    "console_palette": True,
    "reboot_cmd": "reboot",
}

# ------------------------------------------------------------------ the look
# Per theme: 8-colour base, 256-colour (normal, bright, dim), and the RGB values
# used to repaint the real Linux console (background, normal, bright, dim).
THEMES = {
    "green": (curses.COLOR_GREEN, (77, 120, 28), ("020a04", "33ff66", "b4ffc8", "1d6b34")),
    "amber": (curses.COLOR_YELLOW, (214, 222, 130), ("0a0600", "ffb000", "ffd98a", "7a5000")),
    "white": (curses.COLOR_WHITE, (250, 231, 243), ("080808", "d0d0d0", "ffffff", "6a6a6a")),
    "cyan": (curses.COLOR_CYAN, (80, 159, 30), ("000a0c", "33e0ff", "c0f6ff", "1a6070")),
}

# A small figlet-style font, so the banner works in plain ASCII.
FONT = {
    "P": [" ___ ", "| _ \\", "|  _/", "|_|  "],
    "O": ["  ___  ", " / _ \\ ", "| (_) |", " \\___/ "],
    "N": [" _  _ ", "| \\| |", "| .` |", "|_|\\_|"],
    "T": [" _____ ", "|_   _|", "  | |  ", "  |_|  "],
    "E": [" ___ ", "| __|", "| _| ", "|___|"],
    "S": [" ___ ", "/ __|", "\\__ \\", "|___/"],
    "C": ["  ___ ", " / __|", "| (__ ", " \\___|"],
    "A": ["   _   ", "  /_\\  ", " / _ \\ ", "/_/ \\_\\"],
    "L": [" _    ", "| |   ", "| |__ ", "|____|"],
    "-": ["    ", " __ ", "|__|", "    "],
    " ": ["  ", "  ", "  ", "  "],
}


def render_banner(text):
    return ["".join(FONT[c][row] for c in text if c in FONT) for row in range(4)]


BANNER = render_banner(SYSTEM_NAME)

UNICODE_CHARS = dict(tl="╔", tr="╗", bl="╚", br="╝", h="═", v="║", lt="╠", rt="╣",
                     tt="╦", bt="╩", x="╬", ptr="►", sub="»", up="▲", down="▼",
                     block="█", shade="░")
ASCII_CHARS = dict(tl="+", tr="+", bl="+", br="+", h="=", v="|", lt="+", rt="+",
                   tt="+", bt="+", x="+", ptr=">", sub=">>", up="^", down="v",
                   block="#", shade=".")

K_UP = (curses.KEY_UP, ord("k"))
K_DOWN = (curses.KEY_DOWN, ord("j"))
K_FWD = (curses.KEY_RIGHT, ord("l"))
K_BACK = (curses.KEY_LEFT, ord("h"), 27, ord("q"))
K_ENTER = (10, 13, curses.KEY_ENTER)

EXIT = object()  # returned by the "Exit" item to leave the program


# --------------------------------------------------------------------- helpers
def item(label, action=None, sub=None, desc=""):
    """A menu entry. `sub` is a list of items, or a function returning one."""
    return {"label": label, "action": action, "sub": sub, "desc": desc}


def put(win, y, x, text, attr=0):
    """addstr that clips instead of crashing on small screens."""
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    if x < 0:
        text, x = text[-x:], 0
    text = text[: max(0, w - x)]
    try:
        win.addstr(y, x, text, attr)
    except curses.error:
        pass  # writing the bottom-right cell raises but still draws


def fit(text, width):
    """Pad/clip text to exactly `width` columns with one space of margin."""
    if width <= 0:
        return ""
    text = " " + text
    if len(text) > width:
        text = text[: max(0, width - 1)] + "~"
    return text.ljust(width)


def clamp_scroll(sel, top, height, n):
    if n == 0 or height <= 0:
        return max(0, min(sel, n - 1)) if n else 0, 0
    sel = max(0, min(sel, n - 1))
    if sel < top:
        top = sel
    elif sel >= top + height:
        top = sel - height + 1
    return sel, max(0, min(top, max(0, n - height)))


def human_size(n):
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return str(n)


def cursor(visible):
    try:
        curses.curs_set(visible)
    except curses.error:
        pass


def sysinfo():
    """Tiny /proc readout for the home screen (three small reads, no subprocess)."""
    parts = []
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                k, v = line.split(":", 1)
                mem[k] = int(v.split()[0]) * 1024
                if len(mem) > 5:
                    break
        total = mem.get("MemTotal", 0)
        used = total - mem.get("MemAvailable", mem.get("MemFree", 0))
        parts.append(f"MEM {human_size(used)}/{human_size(total)}")
    except (OSError, ValueError):
        pass
    try:
        with open("/proc/loadavg") as f:
            parts.append("LOAD " + f.read().split()[0])
    except (OSError, IndexError):
        pass
    try:
        with open("/proc/uptime") as f:
            s = int(float(f.read().split()[0]))
        d, s = divmod(s, 86400)
        h, s = divmod(s, 3600)
        parts.append("UP " + (f"{d}d" if d else "") + f"{h}h{s // 60:02d}m")
    except (OSError, ValueError, IndexError):
        pass
    return "  ".join(parts)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    tmp = CONFIG_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, CONFIG_FILE)  # atomic: never leaves a half-written config


def load_plugins():
    """Every *.json file in the plugin folder becomes a main-menu entry."""
    plugins = []
    try:
        names = sorted(os.listdir(PLUGIN_DIR))
    except OSError:
        return plugins
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(PLUGIN_DIR, name)) as f:
                p = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(p, dict) and p.get("name"):
            plugins.append(p)
    return plugins


def console_palette(theme):
    """On the real Linux console, repaint the 16 colours to a phosphor palette."""
    if os.environ.get("TERM") != "linux":
        return
    bg, normal, bright, dim = THEMES.get(theme, THEMES["green"])[2]
    base = THEMES.get(theme, THEMES["green"])[0]
    seq = f"\033]P0{bg}\033]P{base:X}{normal}\033]P{base + 8:X}{bright}\033]P8{dim}"
    try:
        os.write(sys.stdout.fileno(), seq.encode())
    except OSError:
        pass


def console_palette_reset():
    if os.environ.get("TERM") == "linux":
        try:
            os.write(sys.stdout.fileno(), b"\033]R")
        except OSError:
            pass


# ------------------------------------------------------------------ file model
class Entry:
    __slots__ = ("name", "path", "is_dir", "is_link", "_de", "_st")

    def __init__(self, de):
        self.name = de.name
        self.path = de.path
        self._de = de  # DirEntry caches its stat() result
        self._st = False
        try:
            self.is_dir = de.is_dir()
        except OSError:
            self.is_dir = False
        try:
            self.is_link = de.is_symlink()
        except OSError:
            self.is_link = False

    def st(self):
        # Only called for rows on screen: big folders open without stat-ing every file.
        if self._st is False:
            try:
                self._st = self._de.stat()
            except OSError:
                try:
                    self._st = self._de.stat(follow_symlinks=False)
                except OSError:
                    self._st = None
        return self._st


# ------------------------------------------------------------------------- app
class App:
    def __init__(self, scr):
        self.scr = scr
        self.cfg = load_config()
        self.plugins = []
        self.bg = lambda: None  # redraws whatever screen is currently below a dialog
        self.host = socket.gethostname().split(".")[0]
        try:
            self.user = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            self.user = str(os.getuid())
        try:  # what ncurses can really draw (Python itself may claim UTF-8 in the C locale)
            enc = locale.nl_langinfo(locale.CODESET)
        except (AttributeError, ValueError):
            enc = locale.getpreferredencoding(False) or ""
        enc = enc.upper().replace("-", "")
        self.B = UNICODE_CHARS if enc == "UTF8" else ASCII_CHARS
        curses.raw()  # Ctrl-C / Ctrl-Z don't kill the home screen
        cursor(0)
        scr.keypad(True)
        self.colors = curses.has_colors()
        if self.colors:
            curses.start_color()
        self.apply_theme()
        self.files = FileManager(self)

    # ---- colours
    def apply_theme(self):
        theme = self.cfg.get("theme")
        base, rich, _ = THEMES.get(theme, THEMES["green"])
        if self.cfg.get("console_palette"):
            console_palette(theme)
        if not self.colors:
            self.norm = curses.A_NORMAL
            self.bold = curses.A_BOLD
            self.dim = curses.A_DIM
            self.sel = curses.A_REVERSE | curses.A_BOLD
        else:
            black = curses.COLOR_BLACK
            if curses.COLORS >= 256:
                n, b, d = rich
                black = 16
            else:
                n = b = d = base
            curses.init_pair(1, n, black)
            curses.init_pair(2, b, black)
            curses.init_pair(3, d, black)
            curses.init_pair(4, black, b)
            self.norm = curses.color_pair(1)
            self.bold = curses.color_pair(2) | curses.A_BOLD
            self.dim = curses.color_pair(3) | (curses.A_DIM if curses.COLORS < 256 else 0)
            self.sel = curses.color_pair(4) | curses.A_BOLD
        self.scr.bkgd(" ", self.norm)

    # ---- the boxed table: used for every menu, popup and dialog
    @staticmethod
    def table_overhead(title, headers):
        return 2 + (2 if title else 0) + (2 if headers else 0)

    def draw_table(self, win, y, x, h, w, n, row_at, sel=-1, top=0, title=None,
                   headers=None, widths=(), pointer=True, footer=None):
        """
        Double-line box like an old terminal reference card.
        widths: widths of every column except the last, which takes the rest.
        row_at(i) -> (cells, attr). Returns the number of body rows.
        """
        B = self.B
        iw = w - 2
        seps, acc = [], 0
        for cw in widths:
            acc += cw
            seps.append(acc)
            acc += 1

        def hline(left, right, cross):
            chars = [B["h"]] * iw
            for s in seps:
                if 0 <= s < iw:
                    chars[s] = cross
            return left + "".join(chars) + right

        def cells(values):
            values = list(values) + [""] * (len(widths) + 1 - len(values))
            parts = [fit(values[i], cw) for i, cw in enumerate(widths)]
            parts.append(fit(values[len(widths)], iw - sum(widths) - len(widths)))
            return B["v"].join(parts)

        row = y
        put(win, row, x, hline(B["tl"], B["tr"], B["tt"] if headers and not title else B["h"]), self.norm)
        row += 1
        if title:
            put(win, row, x, B["v"], self.norm)
            put(win, row, x + 1, title[:iw].center(iw), self.bold)
            put(win, row, x + w - 1, B["v"], self.norm)
            put(win, row + 1, x, hline(B["lt"], B["rt"], B["tt"] if headers else B["h"]), self.norm)
            row += 2
        if headers:
            put(win, row, x, B["v"] + cells(headers) + B["v"], self.bold)
            put(win, row + 1, x, hline(B["lt"], B["rt"], B["x"]), self.norm)
            row += 2
        body = max(0, y + h - 1 - row)
        for i in range(body):
            idx = top + i
            if idx < n:
                values, attr = row_at(idx)
                values = list(values)
                if pointer and values:
                    values[0] = (B["ptr"] + " " if idx == sel else "  ") + values[0]
                line = cells(values)
                a = self.sel if idx == sel else (attr or self.norm)
            else:
                line, a = cells([""]), self.norm
            put(win, row + i, x, B["v"], self.norm)
            put(win, row + i, x + 1, line, a)
            put(win, row + i, x + w - 1, B["v"], self.norm)
        put(win, y + h - 1, x, hline(B["bl"], B["br"], B["bt"]), self.norm)
        if n > body > 0:  # scroll markers on the right border
            if top > 0:
                put(win, row, x + w - 1, B["up"], self.bold)
            if top + body < n:
                put(win, row + body - 1, x + w - 1, B["down"], self.bold)
        if footer and w > len(footer) + 6:
            put(win, y + h - 1, x + 2, f" {footer} ", self.dim)
        return body

    # ---- screen furniture
    def draw_header(self, H, W, info=True):
        """Big banner + tagline when there is room. Returns the next free row."""
        if H >= 26 and W >= len(BANNER[0]) + 4:
            x = (W - len(BANNER[0])) // 2
            for i, line in enumerate(BANNER):
                put(self.scr, 1 + i, x, line, self.bold)
            put(self.scr, 5, x + len(BANNER[0]) - len(TAGLINE), TAGLINE, self.norm)
            y = 7
        else:
            put(self.scr, 0, 1, f"{SYSTEM_NAME} :: {TAGLINE}"[: W - 2], self.bold)
            y = 1
        if info:
            line = f"USER {self.user}  HOST {self.host}  {sysinfo()}"
            put(self.scr, y, max(0, (W - len(line)) // 2), line[:W], self.dim)
            y += 2
        return y

    def status(self, H, W, hints):
        clock = time.strftime("%H:%M:%S")
        room = max(0, W - len(clock) - 2)
        put(self.scr, H - 1, 0, ((" " + hints)[:room].ljust(room) + " " + clock + " ")[:W], self.sel)

    def prompt_line(self, y, W):
        """A fake shell prompt with a blinking block cursor, like an idle terminal."""
        blink = self.B["block"] if int(time.time() * 2) % 2 == 0 else " "
        text = f"{self.user}@{self.host}:~$ "
        x = max(0, (W - len(text) - 30) // 2)
        put(self.scr, y, x, text, self.norm)
        put(self.scr, y, x + len(text), blink, self.bold)

    @staticmethod
    def resolve(sub):
        return sub() if callable(sub) else sub

    def menu_row(self, it):
        label = it["label"]
        if it.get("sub") is not None:
            label += " " + self.B["sub"]
        return label

    # ---- generic full-screen menu (main menu, settings)
    def menu_screen(self, title, build, headers=("OPTION", "DESCRIPTION"), root=False):
        sel, top = 0, 0
        geo = {}
        while True:
            items = build()
            n = len(items)
            sel = max(0, min(sel, n - 1))
            labels = [self.menu_row(i) for i in items]

            def row_at(i):
                return (labels[i], items[i].get("desc", "")), None

            def draw():
                nonlocal top
                H, W = self.scr.getmaxyx()
                self.scr.erase()
                y = self.draw_header(H, W)
                over = self.table_overhead(title, headers)
                c1 = max(len(l) for l in labels) + 6
                c2 = max([len(i.get("desc", "")) for i in items] + [len(headers[1])]) + 3
                tw = min(W, max(c1 + c2 + 3, len(title) + 6, 44))
                c1 = min(c1, max(10, tw // 2))
                spare = H - y - 3  # leave room for the prompt line and status bar
                th = max(over + 1, min(spare, n + over))
                tx = max(0, (W - tw) // 2)
                body = th - over
                _, top = clamp_scroll(sel, top, body, n)
                self.draw_table(self.scr, y, tx, th, tw, n, row_at, sel, top,
                                title=title, headers=headers, widths=(c1,))
                if y + th + 1 < H - 2:
                    self.prompt_line(y + th + 1, W)
                if H >= 20:
                    put(self.scr, H - 2, max(0, (W - len(COPYRIGHT)) // 2), COPYRIGHT, self.dim)
                back = "" if root else "  LEFT back"
                self.status(H, W, f"UP/DN move  RIGHT/ENTER select{back}")
                self.scr.noutrefresh()
                geo.update(y=y + over - 1, x=tx + c1 + 1, w=tw, body=body)

            self.bg = draw
            draw()
            curses.doupdate()
            self.scr.timeout(500)  # wake up for the clock and the blinking cursor
            k = self.scr.getch()
            self.scr.timeout(-1)

            if k in (-1, curses.KEY_RESIZE):
                continue
            if k in K_UP:
                sel = (sel - 1) % n
            elif k in K_DOWN:
                sel = (sel + 1) % n
            elif k == curses.KEY_PPAGE:
                sel = max(0, sel - geo["body"])
            elif k == curses.KEY_NPAGE:
                sel = min(n - 1, sel + geo["body"])
            elif k == curses.KEY_HOME:
                sel = 0
            elif k == curses.KEY_END:
                sel = n - 1
            elif k in K_FWD or k in K_ENTER:
                y = geo["y"] + sel - top
                if self.open_item(items[sel], y, geo["x"], draw) is EXIT:
                    return EXIT
            elif k in K_BACK and not root:
                return None

    def open_item(self, it, y, x, redraw):
        act = it.get("action")
        if it.get("sub") is not None:
            act = self.popup(self.resolve(it["sub"]), y, x, redraw, title=it["label"].upper())
        if act:
            return act()
        return None

    # ---- drop-down menu; RIGHT opens sub-options, LEFT goes back one level
    def popup(self, items, y, x, redraw, title=None):
        """Returns the chosen leaf's action (not yet run), or None if backed out."""
        if not items:
            return None
        H, W = self.scr.getmaxyx()
        labels = [it["label"] for it in items]
        n = len(items)
        lw = max(len(l) for l in labels)
        sub = self.B["sub"]

        def row_at(i):
            mark = sub if items[i].get("sub") is not None else " "
            return (labels[i].ljust(lw + 2) + mark,), None

        over = self.table_overhead(title, None)
        w = min(W, max(lw + 9, len(title) + 6 if title else 0, 20))
        h = min(n + over, H - 1)
        if h < over + 1:
            return None
        y = max(0, min(y, H - 1 - h))
        x = max(0, min(x, W - w))
        win = curses.newwin(h, w, y, x)
        win.keypad(True)
        win.bkgd(" ", self.norm)
        sel, top = 0, 0
        body = h - over

        def draw():
            nonlocal top
            _, top = clamp_scroll(sel, top, body, n)
            win.erase()
            self.draw_table(win, 0, 0, h, w, n, row_at, sel, top, title=title)
            win.noutrefresh()

        while True:
            redraw()
            draw()
            curses.doupdate()
            k = win.getch()
            if k in K_UP:
                sel = (sel - 1) % n
            elif k in K_DOWN:
                sel = (sel + 1) % n
            elif k == curses.KEY_PPAGE:
                sel = max(0, sel - body)
            elif k == curses.KEY_NPAGE:
                sel = min(n - 1, sel + body)
            elif k in K_FWD or k in K_ENTER:
                it = items[sel]
                if it.get("sub") is not None:
                    act = self.popup(self.resolve(it["sub"]), y + over - 1 + sel - top, x + w - 1,
                                     lambda: (redraw(), draw()), title=it["label"].upper())
                    if act:
                        return act
                else:
                    return it.get("action") or (lambda: None)
            elif k in K_BACK or k == curses.KEY_RESIZE:
                return None

    # ---- dialogs
    def confirm(self, question):
        answer = []
        items = [item("NO", lambda: answer.append(False)), item("YES", lambda: answer.append(True))]
        H, W = self.scr.getmaxyx()
        act = self.popup(items, H // 2 - 3, (W - len(question) - 8) // 2, self.bg, title=question.upper())
        if act:
            act()
        return bool(answer and answer[0])

    def prompt(self, label, initial=""):
        """One-line text input. Returns the string, or None on ESC."""
        H, W = self.scr.getmaxyx()
        w = min(W, max(48, len(label) + 8, len(initial) + 8))
        h = 5
        win = curses.newwin(h, w, max(0, (H - h) // 2), max(0, (W - w) // 2))
        win.keypad(True)
        win.bkgd(" ", self.norm)
        buf = list(initial)
        field = w - 5
        cursor(1)
        try:
            while True:
                self.bg()
                win.erase()
                text = "".join(buf)
                vis = text[-(field - 1):] if len(text) >= field else text
                self.draw_table(win, 0, 0, h, w, 1, lambda i: ((vis,), self.bold), pointer=False,
                                title=label.upper(), footer="ENTER ok  ESC cancel")
                try:
                    win.move(3, 2 + len(vis))
                except curses.error:
                    pass
                win.noutrefresh()
                curses.doupdate()
                try:
                    ch = win.get_wch()
                except curses.error:
                    continue
                if ch in ("\n", "\r", curses.KEY_ENTER):
                    return "".join(buf)
                if ch == "\x1b":
                    return None
                if ch in (curses.KEY_BACKSPACE, "\x7f", "\b"):
                    if buf:
                        buf.pop()
                elif ch == "\x15":  # Ctrl-U clears the line
                    buf.clear()
                elif isinstance(ch, str) and ch.isprintable():
                    buf.append(ch)
        finally:
            cursor(0)

    def show_text(self, title, lines):
        """Scrollable info card (used for Details and messages)."""
        H, W = self.scr.getmaxyx()
        w = min(W, max(44, max((len(l) for l in lines), default=0) + 6, len(title) + 8))
        wrapped = []
        for line in lines:
            wrapped += textwrap.wrap(line, max(10, w - 5), subsequent_indent="    ",
                                     drop_whitespace=False) or [""]
        over = self.table_overhead(title, None)
        h = max(over + 1, min(H - 1, len(wrapped) + over))
        body = h - over
        win = curses.newwin(h, w, max(0, (H - h) // 2), max(0, (W - w) // 2))
        win.keypad(True)
        win.bkgd(" ", self.norm)
        top = 0
        maxtop = max(0, len(wrapped) - body)
        while True:
            self.bg()
            win.erase()
            hint = "LEFT/ENTER close" + ("  UP/DN scroll" if maxtop else "")
            self.draw_table(win, 0, 0, h, w, len(wrapped), lambda i: ((wrapped[i],), None),
                            top=top, title=title.upper(), pointer=False, footer=hint)
            win.noutrefresh()
            curses.doupdate()
            k = win.getch()
            if k in K_UP:
                top = max(0, top - 1)
            elif k in K_DOWN:
                top = min(maxtop, top + 1)
            elif k == curses.KEY_PPAGE:
                top = max(0, top - body)
            elif k == curses.KEY_NPAGE:
                top = min(maxtop, top + body)
            elif k in K_BACK or k in K_ENTER or k in K_FWD or k == curses.KEY_RESIZE:
                return

    # ---- running other programs
    def run(self, cmd, cwd=None, pause=False, note=None):
        """Leave curses, run a program in the real terminal, come back."""
        curses.def_prog_mode()
        curses.endwin()
        if note:
            print(note)
        env = dict(os.environ, RETROMENU="1")
        try:
            rc = subprocess.call(cmd, shell=isinstance(cmd, str), cwd=cwd, env=env)
        except OSError as e:
            print(f"Could not start {cmd!r}: {e.strerror}")
            rc = 127
        if pause or rc == 127:  # 127 = command not found
            try:
                input("\n[ press ENTER to return to the menu ]")
            except EOFError:
                pass
        curses.reset_prog_mode()
        self.scr.clear()  # force a full repaint
        cursor(0)
        return rc

    def open_terminal(self, path):
        shell = shlex.split(self.cfg["shell"]) or ["/bin/sh"]
        self.run(shell, cwd=path, note=f"\n[ {SYSTEM_NAME} terminal in {path} -- type 'exit' to return ]\n")

    # ---- main menu
    def main_items(self):
        c = self.cfg
        items = [
            item("Files", self.files.run, desc="Browse and manage files"),
            item("Terminal", lambda: self.open_terminal(os.path.expanduser("~")),
                 desc="Open the full command line"),
            item(c["monitor_name"], lambda: self.run(c["monitor_cmd"]), desc="Running processes"),
        ]
        items += [self.plugin_item(p) for p in self.plugins]
        if not self.plugins:
            items.append(item("Placeholder", self.plugin_help, desc="Add your own programs"))
        items += [
            item("Settings", self.settings, desc="Customize this home screen"),
            item("Reboot", self.reboot, desc="Restart the system"),
            item("Exit", lambda: EXIT, desc="Leave the menu"),
        ]
        return items

    # ---- plugins
    def plugin_item(self, p):
        def launch(cmd, pause=False):
            return lambda: self.run(cmd, pause=pause)

        def option(o):
            if o.get("options"):
                return item(o.get("label", "?"), sub=lambda: [option(x) for x in o["options"]])
            if o.get("command"):
                return item(o.get("label", "?"), launch(o["command"], o.get("pause", False)))
            return item(o.get("label", "?"))

        def build():
            opts = list(p.get("options", []))
            if p.get("options_cmd"):
                opts += self.enumerate_options(p["options_cmd"])
            items = []
            if p.get("command"):
                items.append(item("Launch", launch(p["command"], p.get("pause", False))))
            return items + [option(o) for o in opts]

        desc = p.get("description", "Plugin")
        if p.get("options") or p.get("options_cmd"):
            return item(p["name"], sub=build, desc=desc)
        if p.get("command"):
            return item(p["name"], launch(p["command"], p.get("pause", False)), desc=desc)
        return item(p["name"], lambda: self.show_text(p["name"], ["This plugin has no command or options."]),
                    desc=desc)

    def enumerate_options(self, cmd):
        """Ask a program for its own options: it must print a JSON list on stdout."""
        try:
            out = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=3).stdout
            data = json.loads(out)
            if isinstance(data, list):
                return [o for o in data if isinstance(o, dict)]
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        return [{"label": "(could not read options)"}]

    def plugin_help(self):
        self.show_text("PLUGINS", [
            "Add a program to this menu by dropping a .json file in:",
            f"  {PLUGIN_DIR}",
            "",
            "Example:",
            '  { "name": "Network",',
            '    "description": "Wifi and IP tools",',
            '    "command": "nmtui",',
            '    "options": [',
            '      { "label": "Show IP", "command": "ip a", "pause": true },',
            '      { "label": "More", "options": [ ... ] } ] }',
            "",
            "Or let the program list its own options:",
            '  "options_cmd": "myprog --retromenu-options"',
            "  (it must print a JSON list of options)",
            "",
            "Then: Settings > Reload plugins.",
        ])

    def reload_plugins(self):
        self.plugins = load_plugins()

    # ---- settings
    def set(self, key, value):
        self.cfg[key] = value
        try:
            save_config(self.cfg)
        except OSError as e:
            self.show_text("ERROR", [f"Could not save settings: {e.strerror}"])
        if key in ("theme", "console_palette"):
            if key == "console_palette" and not value:
                console_palette_reset()
            self.apply_theme()

    def edit(self, key, label):
        val = self.prompt(label, str(self.cfg[key]))
        if val is not None and val.strip():
            self.set(key, val.strip())

    def settings(self):
        c = self.cfg
        onoff = lambda v: "ON" if v else "OFF"

        def build():
            return [
                item("Theme", desc=c["theme"].upper(),
                     sub=lambda: [item(t.upper(), lambda t=t: self.set("theme", t)) for t in THEMES]),
                item("Monitor name", lambda: self.edit("monitor_name", "Name in main menu"),
                     desc=c["monitor_name"]),
                item("Monitor command", lambda: self.edit("monitor_cmd", "Command to run"),
                     desc=c["monitor_cmd"]),
                item("Shell", lambda: self.edit("shell", "Shell"), desc=c["shell"]),
                item("Editor", lambda: self.edit("editor", "Editor"), desc=c["editor"]),
                item("Pager", lambda: self.edit("pager", "Pager"), desc=c["pager"]),
                item("Hidden files", lambda: self.set("show_hidden", not c["show_hidden"]),
                     desc=onoff(c["show_hidden"])),
                item("Boot splash", lambda: self.set("splash", not c["splash"]), desc=onoff(c["splash"])),
                item("Console palette", lambda: self.set("console_palette", not c["console_palette"]),
                     desc=onoff(c["console_palette"]) + " (Linux console only)"),
                item("Reboot command", lambda: self.edit("reboot_cmd", "Reboot command"),
                     desc=c["reboot_cmd"]),
                item("Reload plugins", self.reload_plugins, desc=f"{len(self.plugins)} loaded"),
                item("Reset to defaults", sub=[item("NO"), item("YES, RESET", self.reset)]),
            ]

        self.menu_screen("SETTINGS", build, headers=("SETTING", "VALUE"))

    def reset(self):
        self.cfg.clear()
        self.cfg.update(DEFAULT_CONFIG)
        self.set("theme", self.cfg["theme"])

    # ---- reboot
    def reboot(self):
        if not self.confirm("Reboot now?"):
            return
        cmd = self.cfg["reboot_cmd"]
        if os.geteuid() != 0:
            for elevate in ("doas", "sudo"):
                if shutil.which(elevate):
                    cmd = f"{elevate} {cmd}"  # asks for the password in the terminal
                    break
        self.run(cmd, pause=True, note="\n[ Rebooting... ]\n")

    # ---- boot screen (does real work while it draws)
    def splash(self):
        log = []

        def step(label, fn):
            result = fn()
            log.append((label, result or "OK"))

        def plugins():
            self.reload_plugins()
            return f"{len(self.plugins)} FOUND"

        def home():
            self.files.load()
            return f"{len(self.files.entries)} ENTRIES"

        steps = [
            ("CONFIGURATION", lambda: None),
            ("TERMINAL", lambda: f"{curses.COLORS} COLOURS" if self.colors else "MONO"),
            ("PLUGINS", plugins),
            ("HOME DIRECTORY", home),
        ]
        show = self.cfg.get("splash")
        for i, (label, fn) in enumerate(steps):
            step(label, fn)
            if show:
                self.draw_splash(log, (i + 1) / (len(steps) + 1))
                time.sleep(0.05)
        if show:
            for f in range(4):  # finish the bar
                self.draw_splash(log, (len(steps) + (f + 1) / 4) / (len(steps) + 1))
                time.sleep(0.03)
            time.sleep(0.15)

    def draw_splash(self, log, progress):
        H, W = self.scr.getmaxyx()
        self.scr.erase()
        bw = len(BANNER[0])
        y = max(0, H // 2 - 9)
        if W >= bw + 2:
            x = (W - bw) // 2
            for j, line in enumerate(BANNER):
                put(self.scr, y + j, x, line, self.bold)
            put(self.scr, y + 5, x + bw - len(TAGLINE), TAGLINE, self.norm)
        else:
            x = 1
            put(self.scr, y, 1, SYSTEM_NAME, self.bold)
        lx = max(1, (W - 44) // 2)
        for j, (label, result) in enumerate(log):
            dots = "." * max(2, 30 - len(label))
            put(self.scr, y + 8 + j, lx, f"{label} {dots} ", self.norm)
            put(self.scr, y + 8 + j, lx + len(label) + len(dots) + 2, result, self.bold)
        bar_w = max(10, min(44, W - 4))
        filled = int(bar_w * min(1.0, progress))
        by = y + 9 + 4
        put(self.scr, by, (W - bar_w) // 2, self.B["block"] * filled, self.bold)
        put(self.scr, by, (W - bar_w) // 2 + filled, self.B["shade"] * (bar_w - filled), self.dim)
        put(self.scr, min(H - 1, by + 3), max(0, (W - len(COPYRIGHT)) // 2), COPYRIGHT, self.dim)
        self.scr.refresh()


# ---------------------------------------------------------------- file manager
class FileManager:
    HEADERS_WIDE = ("PERMISSIONS", "SIZE", "MODIFIED", "NAME")
    WIDTHS_WIDE = (14, 9, 18)
    HEADERS_NARROW = ("SIZE", "NAME")
    WIDTHS_NARROW = (10,)

    def __init__(self, app):
        self.app = app
        self.cwd = os.path.expanduser("~")
        self.cache = {}  # path -> (mtime_ns, show_hidden, entries); reused if unchanged
        self.pos = {}  # path -> (sel, top), so going back lands where you were
        self.entries = []
        self.error = None
        self.sel = self.top = 0
        self.body = 10

    def load(self, force=False):
        path = self.cwd
        hidden = self.app.cfg["show_hidden"]
        try:
            mtime = os.stat(path).st_mtime_ns
        except OSError as e:
            self.entries, self.error = [], e.strerror
            return
        cached = self.cache.get(path)
        if not force and cached and cached[0] == mtime and cached[1] == hidden:
            self.entries, self.error = cached[2], None
            return
        entries, self.error = [], None
        try:
            with os.scandir(path) as it:  # one syscall batch, no per-file stat
                for de in it:
                    if hidden or not de.name.startswith("."):
                        entries.append(Entry(de))
        except OSError as e:
            self.error = e.strerror
        entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        self.cache.pop(path, None)
        self.cache[path] = (mtime, hidden, entries)
        while len(self.cache) > 64:  # keep memory small
            self.cache.pop(next(iter(self.cache)))
        self.entries = entries

    def row(self, i, wide):
        a = self.app
        e = self.entries[i]
        st = e.st()
        name = e.name + ("/" if e.is_dir else "") + (" @" if e.is_link else "")
        if e.is_dir:
            size = "<DIR>"
        else:
            size = human_size(st.st_size) if st else "?"
        attr = a.bold if e.is_dir else (a.dim if e.name.startswith(".") else a.norm)
        if not wide:
            return (size.rjust(7), name), attr
        perms = stat.filemode(st.st_mode) if st else "?" * 10
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)) if st else ""
        return (perms, size.rjust(6), mtime, name), attr

    def run(self):
        a = self.app
        while True:
            self.load()  # one cheap stat; rescans only if the folder changed
            n = len(self.entries)
            self.sel, self.top = clamp_scroll(self.sel, self.top, self.body, n)

            def draw():
                H, W = a.scr.getmaxyx()
                a.scr.erase()
                wide = W >= 70
                headers = self.HEADERS_WIDE if wide else self.HEADERS_NARROW
                widths = self.WIDTHS_WIDE if wide else self.WIDTHS_NARROW
                path = self.cwd
                room = W - 14
                if len(path) > room:
                    path = "..." + path[-(room - 3):]
                title = f"FILES :: {path}"
                over = a.table_overhead(title, headers)
                self.body = max(1, H - 1 - over)
                self.sel, self.top = clamp_scroll(self.sel, self.top, self.body, n)
                a.draw_table(a.scr, 0, 0, H - 1, W, n, lambda i: self.row(i, wide), self.sel, self.top,
                             title=title, headers=headers, widths=widths,
                             footer=f"{n} ITEM" + ("" if n == 1 else "S") + ("  +HIDDEN" if a.cfg["show_hidden"] else ""))
                if self.error:
                    put(a.scr, over - 1, 3, f"CANNOT READ FOLDER: {self.error}", a.bold)
                elif n == 0:
                    put(a.scr, over - 1, 3, "(EMPTY FOLDER - PRESS ENTER FOR OPTIONS)", a.dim)
                a.status(H, W, "ENTER actions  RIGHT open  LEFT up  . hidden  q menu")
                a.scr.noutrefresh()
                self.name_x = (sum(widths) + len(widths) + 4) if wide else 14
                self.body_y = over - 1

            self.name_x, self.body_y = 10, 5
            a.bg = draw
            draw()
            curses.doupdate()
            a.scr.timeout(1000)
            k = a.scr.getch()
            a.scr.timeout(-1)

            if k in (-1, curses.KEY_RESIZE):
                continue
            if k in K_UP:
                self.sel = max(0, self.sel - 1)
            elif k in K_DOWN:
                self.sel = min(n - 1, self.sel + 1)
            elif k == curses.KEY_PPAGE:
                self.sel = max(0, self.sel - self.body)
            elif k == curses.KEY_NPAGE:
                self.sel = min(n - 1, self.sel + self.body)
            elif k == curses.KEY_HOME:
                self.sel = 0
            elif k == curses.KEY_END:
                self.sel = n - 1
            elif k in K_FWD:
                if n and self.entries[self.sel].is_dir:
                    self.enter(self.entries[self.sel])
                else:
                    self.action_menu(draw)
            elif k in (curses.KEY_LEFT, ord("h"), curses.KEY_BACKSPACE, 127):
                self.go_up()
            elif k in K_ENTER:
                self.action_menu(draw)
            elif k in (ord("q"), 27):
                return
            elif k == ord("."):
                a.set("show_hidden", not a.cfg["show_hidden"])

    # ---- navigation
    def enter(self, ent):
        if not os.access(ent.path, os.R_OK | os.X_OK):
            self.app.show_text("DENIED", [f"Permission denied: {ent.path}"])
            return
        self.pos[self.cwd] = (self.sel, self.top)
        self.cwd = os.path.normpath(ent.path)
        self.sel, self.top = self.pos.get(self.cwd, (0, 0))

    def go_up(self):
        parent = os.path.dirname(self.cwd) or "/"
        if parent == self.cwd:
            return
        child = os.path.basename(self.cwd)
        self.pos[self.cwd] = (self.sel, self.top)
        self.cwd = parent
        self.load()
        self.sel, self.top = self.pos.get(parent, (0, 0))
        for i, e in enumerate(self.entries):
            if e.name == child:  # land on the folder you just left
                self.sel = i
                break

    # ---- the ENTER drop-down
    def action_menu(self, draw):
        ent = self.entries[self.sel] if self.entries else None
        y = self.body_y + self.sel - self.top
        x = self.name_x + (min(len(ent.name), 24) if ent else 0)
        title = (ent.name if ent else os.path.basename(self.cwd) or "/").upper()
        act = self.app.popup(self.actions(ent), y, x, draw, title=title)
        if act:
            act()

    def actions(self, ent):
        a, c = self.app, self.app.cfg
        here = ent.path if ent and ent.is_dir else self.cwd
        items = []
        if ent and ent.is_dir:
            items.append(item("Open folder", lambda: self.enter(ent)))
        elif ent:
            opts = [
                item(f"View ({c['pager']})", lambda: a.run(shlex.split(c["pager"]) + [ent.path])),
                item(f"Edit ({c['editor']})", lambda: a.run(shlex.split(c["editor"]) + [ent.path])),
            ]
            if os.access(ent.path, os.X_OK):
                opts.append(item("Run it", lambda: a.run([ent.path], cwd=self.cwd, pause=True)))
            opts.append(item("Other program...", lambda: self.open_with(ent)))
            items.append(item("Open with", sub=opts))
        items.append(item("Open terminal here", lambda: a.open_terminal(here)))
        if ent:
            items.append(item("Details", lambda: self.details(ent)))
        items.append(item("New", sub=[item("File...", self.new_file), item("Folder...", self.new_folder)]))
        if ent:
            items.append(item("Rename...", lambda: self.rename(ent)))
            kind = "folder" if ent.is_dir and not ent.is_link else "file"
            items.append(item("Delete", sub=[item("No, keep it"),
                                             item(f"Yes, delete {kind}", lambda: self.delete(ent))]))
        items.append(item("Refresh", lambda: self.load(force=True)))
        return items

    # ---- actions
    def open_with(self, ent):
        prog = self.app.prompt("Open with program")
        if prog and prog.strip():
            self.app.run(shlex.split(prog) + [ent.path], cwd=self.cwd)

    def details(self, ent):
        try:
            st = os.lstat(ent.path)
        except OSError as e:
            self.app.show_text("ERROR", [f"{ent.path}: {e.strerror}"])
            return
        m = st.st_mode
        kind = ("symlink" if stat.S_ISLNK(m) else "directory" if stat.S_ISDIR(m) else
                "regular file" if stat.S_ISREG(m) else "device" if stat.S_ISCHR(m) or stat.S_ISBLK(m) else
                "pipe" if stat.S_ISFIFO(m) else "socket" if stat.S_ISSOCK(m) else "other")
        try:
            owner = pwd.getpwuid(st.st_uid).pw_name
        except KeyError:
            owner = str(st.st_uid)
        try:
            group = grp.getgrgid(st.st_gid).gr_name
        except KeyError:
            group = str(st.st_gid)
        fmt = lambda t: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))
        lines = [
            f"NAME     : {ent.name}",
            f"PATH     : {ent.path}",
            f"TYPE     : {kind}",
        ]
        if stat.S_ISLNK(m):
            try:
                lines.append(f"TARGET   : {os.readlink(ent.path)}")
            except OSError:
                pass
        if ent.is_dir:
            try:
                lines.append(f"CONTAINS : {len(os.listdir(ent.path))} items")
            except OSError:
                lines.append("CONTAINS : (no access)")
        else:
            lines.append(f"SIZE     : {human_size(st.st_size)} ({st.st_size} bytes)")
        mime = mimetypes.guess_type(ent.name)[0]
        lines += [
            f"PERMS    : {stat.filemode(m)} ({oct(m & 0o7777)})",
            f"OWNER    : {owner}:{group}",
            f"MODIFIED : {fmt(st.st_mtime)}",
            f"ACCESSED : {fmt(st.st_atime)}",
            f"CHANGED  : {fmt(st.st_ctime)}",
            f"INODE    : {st.st_ino}   LINKS: {st.st_nlink}",
            f"MIME     : {mime or 'unknown'}",
        ]
        if shutil.which("file"):
            try:
                out = subprocess.run(["file", "-b", ent.path], capture_output=True, text=True, timeout=2)
                lines.append(f"CONTENT  : {out.stdout.strip()}")
            except (OSError, subprocess.SubprocessError):
                pass
        self.app.show_text("DETAILS", lines)

    def _ask_name(self, label, initial=""):
        name = self.app.prompt(label, initial)
        if name is None:
            return None
        name = name.strip()
        if not name or "/" in name or name in (".", ".."):
            self.app.show_text("ERROR", ["Invalid name."])
            return None
        return name

    def _select(self, name):
        self.load(force=True)
        for i, e in enumerate(self.entries):
            if e.name == name:
                self.sel = i
                return

    def new_file(self):
        name = self._ask_name("New file name")
        if name:
            try:
                open(os.path.join(self.cwd, name), "x").close()
                self._select(name)
            except OSError as e:
                self.app.show_text("ERROR", [f"Could not create {name}: {e.strerror}"])

    def new_folder(self):
        name = self._ask_name("New folder name")
        if name:
            try:
                os.mkdir(os.path.join(self.cwd, name))
                self._select(name)
            except OSError as e:
                self.app.show_text("ERROR", [f"Could not create {name}: {e.strerror}"])

    def rename(self, ent):
        name = self._ask_name("Rename to", ent.name)
        if not name or name == ent.name:
            return
        target = os.path.join(self.cwd, name)
        if os.path.lexists(target):
            self.app.show_text("ERROR", [f"{name} already exists."])
            return
        try:
            os.rename(ent.path, target)
            self._select(name)
        except OSError as e:
            self.app.show_text("ERROR", [f"Could not rename: {e.strerror}"])

    def delete(self, ent):
        try:
            if ent.is_dir and not ent.is_link:
                shutil.rmtree(ent.path)
            else:
                os.remove(ent.path)
        except OSError as e:
            self.app.show_text("ERROR", [f"Could not delete {ent.name}: {e.strerror}"])
        self.load(force=True)


# ------------------------------------------------------------------------ main
def main(scr):
    app = App(scr)
    app.splash()
    app.menu_screen("MAIN MENU", app.main_items, root=True)


if __name__ == "__main__":
    os.environ.setdefault("ESCDELAY", "25")  # ESC feels instant instead of a 1s delay
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    finally:
        console_palette_reset()
