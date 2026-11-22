#!/usr/bin/env python3
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
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
import random
import re
import shlex
import shutil
import signal
import socket
import stat
import subprocess
import sys
import textwrap
import time

VERSION = "0.6"
SYSTEM_NAME = "PCAL"
SHORT_NAME = "PCal"
TAGLINE = f"PONTES-CALOS SYSTEM V{VERSION}"
COPYRIGHT = "(C) PONTES-CALOS DATA SYSTEMS"

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"), "retromenu")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
PLUGIN_DIR = os.path.join(CONFIG_DIR, "plugins")
HISTORY_FILE = os.path.join(CONFIG_DIR, "history")
HISTORY_MAX = 500

# Command line under the main menu: quick commands show their output inline;
# these need a real terminal, so they take over the screen instead.
CONSOLE_TIMEOUT = 5
CONSOLE_MAX_LINES = 500
FULLSCREEN_CMDS = {  # full-screen programs: no "press ENTER" afterwards
    "vi", "vim", "nvim", "nano", "micro", "emacs", "less", "more", "most", "man", "top", "htop",
    "btop", "mc", "tmux", "screen", "nmtui", "alsamixer", "w3m", "lynx", "links", "watch",
    "cfdisk", "ncdu", "ranger", "nnn",
}
INTERACTIVE_CMDS = {  # read from the keyboard: run in the terminal and pause after
    "sh", "ash", "bash", "zsh", "fish", "python", "python3", "node", "ssh", "telnet", "ftp", "sftp",
    "su", "sudo", "doas", "passwd", "apt", "apt-get", "pacman", "dnf", "fdisk", "parted", "crontab",
    "visudo", "read", "bc", "irssi", "weechat", "mutt",
}
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07|\x1b[()][A-Z0-9]|\r")

DEFAULT_CONFIG = {
    "theme": "green",
    "monitor_name": "Top",
    "monitor_cmd": "top",
    "shell": os.environ.get("SHELL") or "/bin/sh",
    "editor": os.environ.get("EDITOR") or "vi",
    "pager": "less",
    "show_hidden": False,
    "splash": True,
    "effects": "subtle",  # off | subtle | full
    "console_palette": True,
    "box_style": "auto",  # auto: single lines on the Linux console, double elsewhere
    "status_panel": False,  # SYSTEM STATUS next to the main menu (shortcut: s)
    "reboot_cmd": "reboot",
    "poweroff_cmd": "poweroff",
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
                     block="█", shade="░", bar_on="■", bar_off="·")
# Single lines: in practically every console font (many lack the double-line set)
SINGLE_CHARS = dict(UNICODE_CHARS, tl="┌", tr="┐", bl="└", br="┘", h="─", v="│", lt="├", rt="┤",
                    tt="┬", bt="┴", x="┼")
BOX_STYLES = ("auto", "double", "single", "ascii")
ASCII_CHARS = dict(tl="+", tr="+", bl="+", br="+", h="=", v="|", lt="+", rt="+",
                   tt="+", bt="+", x="+", ptr=">", sub=">>", up="^", down="v",
                   block="#", shade=".", bar_on="#", bar_off=".")

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


class SystemStatus:
    """Live readouts for the home screen's SYSTEM STATUS panel.
    Only /proc, /sys and a couple of system calls: no programs are started."""

    def __init__(self):
        self.cpu_prev = None  # (busy, total) from the last /proc/stat read
        self.cpu_pct = None

    @staticmethod
    def bar(frac, width, B):
        filled = max(0, min(width, round(frac * width)))
        return B["bar_on"] * filled + B["bar_off"] * (width - filled)

    def cpu(self):
        try:
            with open("/proc/stat") as f:
                vals = [int(v) for v in f.readline().split()[1:]]
        except (OSError, ValueError):
            return None
        idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
        total = sum(vals[:8])
        if self.cpu_prev and total > self.cpu_prev[1]:
            busy = (total - idle) - self.cpu_prev[0]
            self.cpu_pct = max(0.0, min(1.0, busy / (total - self.cpu_prev[1])))
        self.cpu_prev = (total - idle, total)
        return self.cpu_pct

    @staticmethod
    def memory():
        mem = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    k, v = line.split(":", 1)
                    mem[k] = int(v.split()[0]) * 1024
        except (OSError, ValueError):
            return None
        total = mem.get("MemTotal", 0)
        used = total - mem.get("MemAvailable", mem.get("MemFree", 0))
        return (used, total) if total else None

    @staticmethod
    def disk(path="/"):
        try:
            st = os.statvfs(path)
        except OSError:
            return None
        total = st.f_blocks * st.f_frsize
        used = total - st.f_bfree * st.f_frsize
        return (used, total) if total else None

    @staticmethod
    def network():
        """'eth0 192.168.10.104' for the first interface that has an IPv4 address."""
        import fcntl
        import struct
        try:
            names = sorted(n for n in os.listdir("/sys/class/net") if n != "lo")
        except OSError:
            return "unknown"
        found = []
        for name in names:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
                    raw = fcntl.ioctl(sk.fileno(), 0x8915, struct.pack("256s", name[:15].encode()))  # SIOCGIFADDR
                found.append(f"{name} {socket.inet_ntoa(raw[20:24])}")
            except OSError:
                continue
        if not found:
            return "offline"
        return found[0] + (f"  +{len(found) - 1}" if len(found) > 1 else "")

    @staticmethod
    def uptime():
        try:
            with open("/proc/uptime") as f:
                secs = int(float(f.read().split()[0]))
        except (OSError, ValueError, IndexError):
            return "?"
        d, secs = divmod(secs, 86400)
        h, secs = divmod(secs, 3600)
        return (f"{d}d " if d else "") + f"{h}h{secs // 60:02d}m"

    @staticmethod
    def first_file(pattern_dir, suffix):
        try:
            for name in sorted(os.listdir(pattern_dir)):
                path = os.path.join(pattern_dir, name, suffix)
                if os.path.exists(path):
                    return path
        except OSError:
            pass
        return None

    def battery(self, bar_w, B):
        base = "/sys/class/power_supply"
        try:
            bats = sorted(n for n in os.listdir(base) if n.startswith("BAT"))
        except OSError:
            return None
        if not bats:
            return None
        try:
            with open(os.path.join(base, bats[0], "capacity")) as f:
                pct = int(f.read().strip())
            with open(os.path.join(base, bats[0], "status")) as f:
                state = f.read().strip().upper()
        except (OSError, ValueError):
            return None
        return f"{self.bar(pct / 100, bar_w, B)} {pct:3d}%  {state}"

    def temperature(self, B):
        path = self.first_file("/sys/class/thermal", "temp")
        if not path:
            return None
        try:
            with open(path) as f:
                c = int(f.read().strip()) / 1000
        except (OSError, ValueError):
            return None
        return f"{c:.0f}{'°' if B['block'] == '█' else ' '}C"

    def rows(self, user, host, bar_w, B):
        def usage(pair):
            if not pair:
                return "?"
            used, total = pair
            return f"{self.bar(used / total, bar_w, B)} {used * 100 // total:3d}%  {human_size(used)}/{human_size(total)}"

        cpu = self.cpu()
        try:
            with open("/proc/loadavg") as f:
                load = "  ".join(f.read().split()[:3])
        except OSError:
            load = "?"
        try:
            procs = str(sum(1 for d in os.listdir("/proc") if d.isdigit()))
        except OSError:
            procs = "?"
        rows = [
            ("USER", f"{user}@{host}"),
            ("DATE", time.strftime("%a %d %b %Y").upper()),
            ("TIME", time.strftime("%H:%M:%S")),
            ("UPTIME", self.uptime()),
            ("KERNEL", os.uname().release),
            ("", ""),
            ("CPU", f"{self.bar(cpu, bar_w, B)} {round(cpu * 100):3d}%" if cpu is not None else "measuring..."),
            ("MEMORY", usage(self.memory())),
            ("DISK /", usage(self.disk("/"))),
            ("LOAD", load),
            ("PROCESSES", procs),
            ("NETWORK", self.network()),
        ]
        bat = self.battery(bar_w, B)
        if bat:
            rows.append(("BATTERY", bat))
        temp = self.temperature(B)
        if temp:
            rows.append(("TEMP", temp))
        return rows


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


def console_flicker(theme):
    """One frame of a slightly dimmer phosphor, like an old tube (Linux console only)."""
    if os.environ.get("TERM") != "linux":
        return
    base = THEMES.get(theme, THEMES["green"])[0]
    normal = THEMES.get(theme, THEMES["green"])[2][1]
    dimmer = "".join(f"{int(int(normal[i:i + 2], 16) * 0.8):02x}" for i in (0, 2, 4))
    try:
        os.write(sys.stdout.fileno(), f"\033]P{base:X}{dimmer}".encode())
        time.sleep(0.04)
    except OSError:
        pass
    console_palette(theme)


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
        self.utf8 = enc.upper().replace("-", "") == "UTF8"
        self.B = self.box_chars()
        curses.raw()  # Ctrl-C / Ctrl-Z don't kill the home screen
        cursor(0)
        scr.keypad(True)
        self.colors = curses.has_colors()
        if self.colors:
            curses.start_color()
        self.apply_theme()
        self.files = FileManager(self)
        self.glow = {}  # table key -> (selected row, previous row, time of the change)
        self.status_panel = SystemStatus()
        self.powered = False  # has the power-on animation run yet?
        # command line state (kept while you move around the menus)
        self.con_cwd = os.path.expanduser("~")
        self.con_buf, self.con_cur, self.con_out, self.con_top, self.con_rows = "", 0, [], 0, 0
        self.history, self.hist_i, self.hist_saved = self.load_history(), None, ""

    def banner(self):
        """The title in line-art letters."""
        return BANNER

    def box_chars(self):
        style = self.cfg.get("box_style", "auto")
        if not self.utf8 or style == "ascii":
            return ASCII_CHARS
        if style == "double" or (style != "single" and os.environ.get("TERM") != "linux"):
            return UNICODE_CHARS
        return SINGLE_CHARS

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
            # On a 16-colour console "bold black" becomes colour 8, which PCal's palette makes
            # dim amber/green: unreadable on the bar. So no bold there.
            self.sel = curses.color_pair(4) | (curses.A_BOLD if curses.COLORS >= 256 else 0)
        self.scr.bkgd(" ", self.norm)

    # ---- the boxed table: used for every menu, popup and dialog
    # ---- CRT effects (Settings > Effects: off / subtle / full)
    GLOW_TIME = 0.22

    @property
    def fx(self):
        v = self.cfg.get("effects", "subtle")
        return v if v in ("off", "subtle", "full") else "subtle"

    def afterglow(self, key, sel):
        """Phosphor afterglow: the row you just left fades out instead of switching off."""
        if key is None or self.fx == "off":
            return -1, None
        now = time.monotonic()
        state = self.glow.get(key)
        if state is None:
            self.glow[key] = (sel, -1, 0.0)
        elif state[0] != sel:
            self.glow[key] = (sel, state[0], now)
        _, prev, t0 = self.glow[key]
        age = now - t0
        if prev < 0 or prev == sel or age >= self.GLOW_TIME:
            return -1, None
        if age < self.GLOW_TIME / 3:
            return prev, self.sel | curses.A_DIM  # the bar is still fading
        return prev, self.bold

    def anim_timeout(self, default):
        """Wake up quickly while an afterglow is still fading, otherwise use `default`."""
        now = time.monotonic()
        if any(now - t0 < self.GLOW_TIME for _, _, t0 in self.glow.values()):
            return 30
        return default

    def crt(self, draw, on=True):
        """Old monitor power on/off: the picture opens from (or collapses into) a bright line."""
        if self.fx == "off":
            return
        full = self.fx == "full"
        H, W = self.scr.getmaxyx()
        mid = H // 2
        steps = 8 if full else 5
        frames = [("band", max(1, -(-mid * i // steps))) for i in range(1, steps + 1)]
        if full:
            frames = [("line", max(1, W * i // 6)) for i in range(1, 7)] + frames
        if not on:
            frames.reverse()
            frames.append(("dot", 0))
        line = self.B["h"]
        try:
            for kind, v in frames:
                if kind == "band":
                    draw()
                    for r in range(H):
                        if abs(r - mid) > v:
                            put(self.scr, r, 0, " " * W, self.norm)
                    for r in (mid - v, mid + v):
                        if 0 <= r < H and v < mid:
                            put(self.scr, r, 0, line * W, self.bold)
                else:
                    self.scr.erase()
                    if kind == "line":
                        put(self.scr, mid, (W - v) // 2, line * v, self.bold)
                    else:
                        put(self.scr, mid, W // 2, self.B["block"], self.bold)
                self.scr.noutrefresh()
                curses.doupdate()
                time.sleep(0.025 if kind == "line" else 0.03)
            if not on:
                time.sleep(0.08)
                self.scr.erase()
                self.scr.refresh()
        except curses.error:
            pass

    def maybe_flicker(self):
        """Full effects on the Linux console: now and then the tube flickers for one frame."""
        if self.fx == "full" and self.cfg.get("console_palette") and random.random() < 0.03:
            console_flicker(self.cfg.get("theme"))

    @staticmethod
    def table_overhead(title, headers):
        return 2 + (2 if title else 0) + (2 if headers else 0)

    def draw_table(self, win, y, x, h, w, n, row_at, sel=-1, top=0, title=None,
                   headers=None, widths=(), pointer=True, footer=None, glow_key=None):
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
        glow_idx, glow_attr = self.afterglow(glow_key, sel)
        for i in range(body):
            idx = top + i
            if idx < n:
                values, attr = row_at(idx)
                values = list(values)
                if pointer and values:
                    values[0] = (B["ptr"] + " " if idx == sel else "  ") + values[0]
                line = cells(values)
                a = self.sel if idx == sel else (glow_attr if idx == glow_idx else (attr or self.norm))
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
        banner = self.banner()
        if H >= 26 and W >= len(banner[0]) + 4:
            x = (W - len(banner[0])) // 2
            for i, line in enumerate(banner):
                put(self.scr, 1 + i, x, line, self.bold)
            put(self.scr, 5, x + len(banner[0]) - len(TAGLINE), TAGLINE, self.norm)
            y = 7
        else:
            put(self.scr, 0, 1, f"PCAL {VERSION} :: PONTES-CALOS SYSTEM"[: W - 2], self.bold)
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

    # ---- the live command line under the main menu
    def short_cwd(self):
        home = os.path.expanduser("~")
        p = self.con_cwd
        if p == home or p.startswith(home + os.sep):
            p = "~" + p[len(home):]
        return p

    def draw_console(self, y0, x, w, H, focused, keep_footer):
        """Prompt line + output of the last command. Returns the number of output rows."""
        ps = f"{self.user}@{self.host}:{self.short_cwd()}$ "
        if len(ps) > w // 2:
            ps = "..." + ps[-(w // 2 - 3):]
        avail = max(1, w - len(ps) - 1)
        text, cur = self.con_buf, self.con_cur
        start = max(0, cur - avail + 1)
        put(self.scr, y0, x, ps, self.bold if focused else self.norm)
        put(self.scr, y0, x + len(ps), text[start:start + avail], self.bold)
        cx = x + len(ps) + cur - start
        if focused:
            put(self.scr, y0, cx, text[cur] if cur < len(text) else " ", self.sel)
        elif not text and int(time.time() * 2) % 2 == 0:
            put(self.scr, y0, cx, self.B["block"], self.bold)
        last = H - 3 if keep_footer else H - 2
        rows = max(0, last - y0)
        out = self.con_out
        self.con_top = max(0, min(self.con_top, len(out) - rows))
        for i in range(rows):
            j = self.con_top + i
            if j < len(out):
                line = out[j]
                attr = self.bold if j == 0 else (self.dim if line.startswith("[") else self.norm)
                put(self.scr, y0 + 1 + i, x, line[:w], attr)
        if len(out) > rows > 0:
            if self.con_top > 0:
                put(self.scr, y0 + 1, x + w - 1, self.B["up"], self.bold)
            if self.con_top + rows < len(out):
                put(self.scr, y0 + rows, x + w - 1, self.B["down"], self.bold)
        return rows

    def load_history(self):
        try:
            with open(HISTORY_FILE, errors="replace") as f:
                lines = [l.rstrip("\n") for l in f if l.strip()]
        except OSError:
            return []
        if len(lines) > 2 * HISTORY_MAX:  # trim the file now and then
            lines = lines[-HISTORY_MAX:]
            try:
                with open(HISTORY_FILE, "w") as f:
                    f.write("\n".join(lines) + "\n")
            except OSError:
                pass
        return lines[-HISTORY_MAX:]

    def add_history(self, cmd):
        if self.history and self.history[-1] == cmd:
            return
        self.history.append(cmd)
        del self.history[:-HISTORY_MAX]
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(HISTORY_FILE, "a") as f:
                f.write(cmd.replace("\n", " ") + "\n")
        except OSError:
            pass

    def console_key(self, k):
        """Handle a key while the prompt has focus. Returns "menu" to give focus back."""
        buf, cur = self.con_buf, self.con_cur
        if isinstance(k, str):
            self.con_buf = buf[:cur] + k + buf[cur:]
            self.con_cur = cur + len(k)
            self.hist_i = None
            return None
        if k in K_ENTER:
            return self.console_run()
        if k in (27, 9):  # ESC / TAB
            return "menu"
        if k == curses.KEY_UP:
            if not self.history or self.hist_i == 0:
                return "menu"
            if self.hist_i is None:
                self.hist_saved, self.hist_i = buf, len(self.history)
            self.hist_i -= 1
            self.con_buf = self.history[self.hist_i]
            self.con_cur = len(self.con_buf)
        elif k == curses.KEY_DOWN:
            if self.hist_i is not None:
                self.hist_i += 1
                if self.hist_i >= len(self.history):
                    self.con_buf, self.hist_i = self.hist_saved, None
                else:
                    self.con_buf = self.history[self.hist_i]
                self.con_cur = len(self.con_buf)
        elif k == curses.KEY_LEFT:
            self.con_cur = max(0, cur - 1)
        elif k == curses.KEY_RIGHT:
            self.con_cur = min(len(buf), cur + 1)
        elif k in (curses.KEY_HOME, 1):  # Ctrl-A
            self.con_cur = 0
        elif k in (curses.KEY_END, 5):  # Ctrl-E
            self.con_cur = len(buf)
        elif k in (curses.KEY_BACKSPACE, 127, 8):
            if cur > 0:
                self.con_buf, self.con_cur = buf[:cur - 1] + buf[cur:], cur - 1
        elif k == curses.KEY_DC:
            self.con_buf = buf[:cur] + buf[cur + 1:]
        elif k == 21:  # Ctrl-U
            self.con_buf, self.con_cur = "", 0
        elif k == 12:  # Ctrl-L
            self.con_out, self.con_top = [], 0
        elif k == curses.KEY_PPAGE:
            self.con_top = max(0, self.con_top - max(1, self.con_rows - 1))
        elif k == curses.KEY_NPAGE:
            self.con_top += max(1, self.con_rows - 1)
        return None

    def console_run(self):
        cmd = self.con_buf.strip()
        self.con_buf, self.con_cur, self.hist_i = "", 0, None
        if not cmd:
            self.open_terminal(self.con_cwd)
            return None
        self.add_history(cmd)
        if cmd in ("exit", "logout"):
            return "menu"
        if cmd in ("clear", "cls"):
            self.con_out, self.con_top = [], 0
            return None
        if cmd == "cd" or cmd.startswith("cd "):
            target = os.path.expanduser(cmd[2:].strip() or "~")
            target = os.path.normpath(os.path.join(self.con_cwd, target))
            if os.path.isdir(target) and os.access(target, os.X_OK):
                self.con_cwd = target
                self.con_out = []
            else:
                self.con_out = [f"$ {cmd}", f"cd: no such folder: {target}"]
            self.con_top = 0
            return None
        if cmd.startswith("!"):  # force the full terminal
            cmd = cmd[1:].strip()
            if cmd:
                self.run(cmd, cwd=self.con_cwd, pause=True)
                self.con_out, self.con_top = [f"$ {cmd}", "[ran in the full terminal]"], 0
            return None
        try:
            prog = os.path.basename(shlex.split(cmd)[0])
        except (ValueError, IndexError):
            prog = cmd.split()[0]
        if prog in FULLSCREEN_CMDS or prog in INTERACTIVE_CMDS:
            self.run(cmd, cwd=self.con_cwd, pause=prog in INTERACTIVE_CMDS)
            self.con_out, self.con_top = [f"$ {cmd}", "[ran in the full terminal]"], 0
            return None
        self.con_out, self.con_top = [f"$ {cmd}", "[running...]"], 0
        self.bg()
        curses.doupdate()
        self.con_out = [f"$ {cmd}"] + self.capture(cmd)
        self.con_top = len(self.con_out)  # show the end, like a terminal (PGUP scrolls back)
        return None

    def capture(self, cmd):
        """Run a quick command without leaving the menu and return its output lines."""
        env = dict(os.environ, TERM="dumb", PAGER="cat", NO_COLOR="1", RETROMENU="1",
                   COLUMNS=str(max(20, self.scr.getmaxyx()[1] - 4)))
        try:
            p = subprocess.Popen(cmd, shell=True, cwd=self.con_cwd, env=env, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                 start_new_session=True)  # own group, so a timeout kills children too
        except OSError as e:
            return [f"[could not run: {e.strerror}]"]
        note = None
        try:
            out, _ = p.communicate(timeout=CONSOLE_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except OSError:
                pass
            out, _ = p.communicate()
            note = f"[stopped after {CONSOLE_TIMEOUT}s -- start it with ! to run in the full terminal]"
        text = ANSI_RE.sub("", out.decode("utf-8", "replace")).expandtabs()
        lines = text.splitlines()[-CONSOLE_MAX_LINES:]
        if note:
            lines.append(note)
        elif p.returncode == 127:
            lines.append("[command not found]")
        elif p.returncode:
            lines.append(f"[exit code {p.returncode}]")
        return lines or ["[no output]"]

    @staticmethod
    def resolve(sub):
        return sub() if callable(sub) else sub

    def menu_row(self, it):
        label = it["label"]
        if it.get("sub") is not None:
            label += " " + self.B["sub"]
        return label

    @staticmethod
    def read_key(win):
        """get_wch -> int key codes for keys/controls, str for printable text, -1 on timeout."""
        try:
            k = win.get_wch()
        except curses.error:
            return -1
        if isinstance(k, str):
            return k if k.isprintable() else ord(k)
        return k

    # ---- generic full-screen menu (main menu, settings)
    def menu_screen(self, title, build, headers=("OPTION", "DESCRIPTION"), root=False):
        sel, top = 0, 0
        self.glow.pop("menu:" + title, None)
        geo = {"console": False}
        focus = False  # True while typing in the command line (main menu only)
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
                over = self.table_overhead(title, headers)
                c1 = max(len(l) for l in labels) + 6
                c2 = max([len(i.get("desc", "")) for i in items] + [len(headers[1])]) + 3
                tw = min(W, max(c1 + c2 + 3, len(title) + 6, 44))
                c1 = min(c1, max(10, tw // 2))
                # Wide screens: the home screen gets a SYSTEM STATUS panel next to the menu
                fits = root and W >= tw + 2 + 48 and H >= 22
                geo["panel_fits"] = fits
                wide = fits and self.cfg.get("status_panel")
                y = self.draw_header(H, W, info=not wide)
                spare = H - y - (4 if root else 3)  # room for the command line and status bar
                if wide:
                    pw = max(48, min(66, W - tw - 6))
                    total = tw + 2 + pw
                    tx = max(0, (W - total) // 2)
                    px = tx + tw + 2
                    bar_w = max(6, min(20, pw - 2 - 12 - 22))
                    srows = self.status_panel.rows(self.user, self.host, bar_w, self.B)
                    sover = self.table_overhead("SYSTEM STATUS", None)
                    th = max(over + 1, min(spare, max(n + over, len(srows) + sover)))
                    self.draw_table(self.scr, y, px, th, pw, len(srows),
                                    lambda i: (srows[i], self.bold if srows[i][0] in ("TIME",) else None),
                                    title="SYSTEM STATUS", widths=(11,), pointer=False)
                    cx, cw = tx, total
                else:
                    th = max(over + 1, min(spare, n + over))
                    tx = max(0, (W - tw) // 2)
                    cx, cw = tx, tw
                body = th - over
                _, top = clamp_scroll(sel, top, body, n)
                self.draw_table(self.scr, y, tx, th, tw, n, row_at, -1 if focus else sel, top,
                                title=title, headers=headers, widths=(c1,), glow_key="menu:" + title)
                keep_footer = H >= 20 and not (root and self.con_out)
                geo["console"] = root and y + th + 1 < H - 2
                if geo["console"]:
                    self.con_rows = self.draw_console(y + th + 1, cx, cw, H, focus, keep_footer)
                if keep_footer:
                    put(self.scr, H - 2, max(0, (W - len(COPYRIGHT)) // 2), COPYRIGHT, self.dim)
                if focus:
                    hints = "-- INSERT --  ENTER run  !cmd full terminal  UP/DN history  ESC menu"
                elif root:
                    hints = "UP/DN move  RIGHT/ENTER select  i command line  t terminal"
                    if fits:
                        hints += "  s status " + ("off" if wide else "on")
                else:
                    hints = "UP/DN move  RIGHT/ENTER select  LEFT back"
                self.status(H, W, hints)
                self.scr.noutrefresh()
                geo.update(y=y + over - 1, x=tx + c1 + 1, w=tw, body=body)

            self.bg = draw
            if not self.powered:  # first screen after start: switch the "monitor" on
                self.powered = True
                self.crt(draw, on=True)
            draw()
            curses.doupdate()
            self.scr.timeout(self.anim_timeout(500))  # clock, blinking cursor, afterglow
            k = self.read_key(self.scr)
            self.scr.timeout(-1)

            if k == -1:
                self.maybe_flicker()
                continue
            if k == curses.KEY_RESIZE:
                continue
            if focus:
                if self.console_key(k) == "menu":
                    focus = False
                continue
            if root and geo["console"] and k in ("i", 9):
                focus = True  # vim style: i (or TAB) = insert mode in the command line
                continue
            if root and k == "t":  # t = full terminal, in the command line's folder
                self.open_terminal(self.con_cwd)
                continue
            if root and k == "s" and geo.get("panel_fits"):  # s = show / hide SYSTEM STATUS
                self.set("status_panel", not self.cfg.get("status_panel"))
                continue
            if isinstance(k, str):
                k = ord(k)  # vim keys (h j k l q) in the other screens
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
                    self.crt(draw, on=False)  # switch the "monitor" off
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
        gkey = f"popup:{y}:{x}"
        self.glow.pop(gkey, None)

        def draw():
            nonlocal top
            _, top = clamp_scroll(sel, top, body, n)
            win.erase()
            self.draw_table(win, 0, 0, h, w, n, row_at, sel, top, title=title, glow_key=gkey)
            win.noutrefresh()

        while True:
            redraw()
            draw()
            curses.doupdate()
            win.timeout(self.anim_timeout(-1))
            k = win.getch()
            win.timeout(-1)
            if k == -1:
                continue
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

    def prompt(self, label, initial="", secret=False):
        """One-line text input. Returns the string, or None on ESC. secret: show dots (passwords)."""
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
                if secret:
                    text = ("•" if self.utf8 else "*") * len(buf)
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
        self.run(shell, cwd=path, note=f"\n[ {SHORT_NAME} terminal in {path} -- type 'exit' to return ]\n")

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
            item("Shut down", self.shutdown, desc="Power off the computer"),
            item("Log out", lambda: EXIT, desc="Back to the login screen"),
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
        if key == "box_style":
            self.B = self.box_chars()
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
                item("Status panel", lambda: self.set("status_panel", not c.get("status_panel")),
                     desc=onoff(c.get("status_panel")) + "  (next to the main menu; shortcut: s)"),
                item("Box style", desc=c.get("box_style", "auto").upper() + "  (lines of menus and boxes)",
                     sub=lambda: [item(v.upper(), lambda v=v: self.set("box_style", v)) for v in BOX_STYLES]),
                item("Effects", desc=self.fx.upper() + "  (CRT power on/off, afterglow)",
                     sub=lambda: [item(v.upper(), lambda v=v: self.set("effects", v))
                                  for v in ("off", "subtle", "full")]),
                item("Console palette", lambda: self.set("console_palette", not c["console_palette"]),
                     desc=onoff(c["console_palette"]) + " (Linux console only)"),
                item("Reboot command", lambda: self.edit("reboot_cmd", "Reboot command"),
                     desc=c["reboot_cmd"]),
                item("Shut down command", lambda: self.edit("poweroff_cmd", "Shut down command"),
                     desc=c["poweroff_cmd"]),
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
        self.power("Reboot now?", self.cfg["reboot_cmd"], "Rebooting...")

    def shutdown(self):
        self.power("Shut down now?", self.cfg["poweroff_cmd"], "Shutting down...")

    def power(self, question, cmd, note):
        """Reboot / shut down after a confirmation; uses doas or sudo when not root."""
        if not self.confirm(question):
            return
        if os.geteuid() != 0:
            for elevate in ("doas", "sudo"):
                if shutil.which(elevate):
                    cmd = f"{elevate} {cmd}"  # no password with the installer's doas rule
                    break
        self.crt(self.bg, on=False)
        self.run(cmd, pause=True, note=f"\n[ {note} ]\n")

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
        if show:
            self.powered = True
            self.crt(lambda: self.draw_splash([], 0, refresh=False), on=True)
        typewriter = self.fx == "full"
        for i, (label, fn) in enumerate(steps):
            step(label, fn)
            if show:
                progress = (i + 1) / (len(steps) + 1)
                if typewriter:  # the new line is typed out, like an old terminal
                    for c in range(0, 48, 4):
                        self.draw_splash(log, progress, typed=c)
                        time.sleep(0.008)
                self.draw_splash(log, progress)
                time.sleep(0.05)
        if show:
            for f in range(4):  # finish the bar
                self.draw_splash(log, (len(steps) + (f + 1) / 4) / (len(steps) + 1))
                time.sleep(0.03)
            time.sleep(0.15)

    def draw_splash(self, log, progress, typed=None, refresh=True):
        H, W = self.scr.getmaxyx()
        self.scr.erase()
        banner = self.banner()
        bw = len(banner[0])
        y = max(0, H // 2 - 9)
        if W >= bw + 2:
            x = (W - bw) // 2
            for j, line in enumerate(banner):
                put(self.scr, y + j, x, line, self.bold)
            put(self.scr, y + 5, x + bw - len(TAGLINE), TAGLINE, self.norm)
        else:
            x = 1
            put(self.scr, y, 1, SYSTEM_NAME, self.bold)
        lx = max(1, (W - 44) // 2)
        for j, (label, result) in enumerate(log):
            dots = "." * max(2, 30 - len(label))
            if typed is not None and j == len(log) - 1:  # typewriter: show only part of the last line
                text = f"{label} {dots} {result}"[:typed]
                put(self.scr, y + 8 + j, lx, text, self.norm)
                put(self.scr, y + 8 + j, lx + len(text), self.B["block"], self.bold)
                continue
            put(self.scr, y + 8 + j, lx, f"{label} {dots} ", self.norm)
            put(self.scr, y + 8 + j, lx + len(label) + len(dots) + 2, result, self.bold)
        bar_w = max(10, min(44, W - 4))
        filled = int(bar_w * min(1.0, progress))
        by = y + 9 + 4
        put(self.scr, by, (W - bar_w) // 2, self.B["block"] * filled, self.bold)
        put(self.scr, by, (W - bar_w) // 2 + filled, self.B["shade"] * (bar_w - filled), self.dim)
        put(self.scr, min(H - 1, by + 3), max(0, (W - len(COPYRIGHT)) // 2), COPYRIGHT, self.dim)
        if refresh:
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
                             glow_key="files:" + self.cwd,
                             title=title, headers=headers, widths=widths,
                             footer=f"{n} ITEM" + ("" if n == 1 else "S") + ("  +HIDDEN" if a.cfg["show_hidden"] else ""))
                if self.error:
                    put(a.scr, over - 1, 3, f"CANNOT READ FOLDER: {self.error}", a.bold)
                elif n == 0:
                    put(a.scr, over - 1, 3, "(EMPTY FOLDER - PRESS ENTER FOR OPTIONS)", a.dim)
                a.status(H, W, "ENTER actions  RIGHT open  LEFT up  t terminal  . hidden  q menu")
                a.scr.noutrefresh()
                self.name_x = (sum(widths) + len(widths) + 4) if wide else 14
                self.body_y = over - 1

            self.name_x, self.body_y = 10, 5
            a.bg = draw
            draw()
            curses.doupdate()
            a.scr.timeout(a.anim_timeout(1000))
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
            elif k == ord("t"):  # terminal in the folder you're looking at
                a.open_terminal(self.cwd)

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


def entry():
    """Start the menu (used by `python3 retromenu.py` and by the pontes binary)."""
    if len(sys.argv) > 1 and sys.argv[1] in ("-V", "--version"):
        print(f"retromenu {VERSION}")
        return 0
    os.environ.setdefault("ESCDELAY", "25")  # ESC feels instant instead of a 1s delay
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    finally:
        console_palette_reset()
    return 0


if __name__ == "__main__":
    sys.exit(entry())
