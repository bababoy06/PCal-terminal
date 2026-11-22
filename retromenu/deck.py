# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Desk: PCal's command deck, drawn by the graphical screen (pontes screen).

  header        PCAL block, CENTRAL INTERFACE // DESK, clock
  left          numbered modules: 01 TERMINAL, 02 FILES, ... 00 SETTINGS
  middle        the module you opened, running in a window
  right         SYSTEM INTEGRITY: live meters and an event log
  bottom        open modules and key hints

Keys on the deck: 0-9 open a module, arrows + ENTER too, TAB switches between open
modules, X closes the one you see, Q goes back to the menu. Inside a module everything
goes to the program; F12 (or Ctrl+]) hands the keys back to the deck.

The deck is drawn as a picture at font resolution (intensities 0..1.5), which the CRT
renderer turns into phosphor with glow, scanlines and the rest (Renderer.update_image).
"""
import codecs
import json
import os
import re
import shlex
import time
from collections import deque

import numpy as np

try:
    from . import vt as vtmod
except ImportError:
    import vt as vtmod

TOKEN_RE = re.compile(r"\x1b\[\[[A-E]|\x1b\[[0-9;]*[~A-Za-z]|\x1bO[A-Za-z]|\x1b|.", re.S)
DECK_KEYS = ("\x1b[24~", "\x1d")  # F12, Ctrl+]: back to the deck from inside a module
KEYNAMES = {"\x1b[A": "up", "\x1bOA": "up", "\x1b[B": "down", "\x1bOB": "down",
            "\x1b[C": "right", "\x1bOC": "right", "\x1b[D": "left", "\x1bOD": "left",
            "\r": "enter", "\n": "enter", "\t": "tab", "\x1b": "esc", "\x1b[24~": "deck", "\x1d": "deck",
            "\x1b[1~": "home", "\x1b[H": "home", "\x1b[4~": "end", "\x1b[F": "end"}

SOON = {
    "STORE": ["THE SOFTWARE STORE IS NOT INSTALLED YET.", "",
              "IT WILL LET YOU BROWSE, SEARCH AND INSTALL", "ALPINE PACKAGES WITH THE ARROW KEYS."],
    "GAMES": ["NO GAMES LOADED YET.", "", "COMING: SNAKE, FALLING BLOCKS, LUNAR LANDER."],
    "IMAGES": ["THE IMAGE VIEWER IS NOT INSTALLED YET.", "",
               "IT WILL SHOW PICTURES IN PHOSPHOR ON THIS SCREEN."],
}


class Window:
    """One open module: a program in a pseudo-terminal, or an info card."""

    def __init__(self, module, child=None, term=None, info=None):
        self.module = module
        self.child, self.term, self.info = child, term, info
        self.dec = codecs.getincrementaldecoder("utf-8")("replace")

    @property
    def num(self):
        return self.module["num"]


class Deck:
    def __init__(self, renderer, menu_cmd, spawn, config_file):
        """spawn(cmd, rows, cols) -> a running Child (see screen.py)."""
        self.menu_cmd, self.spawn, self.config_file = menu_cmd, spawn, config_file
        self.windows = []          # open modules, in the order they were opened
        self.shown = None          # the window on screen (None: no module open)
        self.focus = "deck"        # "deck" or "window": who gets the keys
        self.sel = 0
        self.events = deque(maxlen=8)
        self.textcache = {}
        self.stats, self.stats_at = {}, 0.0
        self.status = None
        try:
            import pwd
            self.user = pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            self.user = os.environ.get("USER") or "operator"
        self.log("OPERATOR ON DECK")
        self.set_renderer(renderer)

    # ---------------------------------------------------------------- setup
    def set_renderer(self, r):
        """(Re)build the layout for a renderer (a new font size changes everything)."""
        self.r, self.font = r, r.font
        self.fw, self.fh = r.font.width, r.font.height
        self.C, self.R = r.cols, r.rows
        self.lv = r.levels
        self.textcache.clear()
        C, R = self.C, self.R
        self.side = 26 if C >= 104 else 0                    # SYSTEM INTEGRITY panel, in cells
        self.left = 16                                       # module column
        self.ax, self.ay = self.left + 2, 5                  # window area
        self.aw = C - self.ax - (self.side + 2 if self.side else 1)
        self.ah = R - self.ay - 3
        self.inner = (max(4, self.ah - 2), max(10, self.aw - 2))  # rows, cols of a module's terminal
        self.modules = self.load_modules()
        for w in self.windows:
            if w.term:
                w.term.resize(*self.inner)
                w.child.resize(*self.inner)

    def load_modules(self):
        try:
            with open(self.config_file) as f:
                cfg = json.load(f)
        except (OSError, ValueError):
            cfg = {}
        shell = shlex.split(cfg.get("shell") or os.environ.get("SHELL") or "/bin/sh") or ["/bin/sh"]
        mon = cfg.get("monitor_cmd") or "top"
        mods = [
            ("01", "TERMINAL", shell),
            ("02", "FILES", self.menu_cmd + ["--files"]),
            ("03", (cfg.get("monitor_name") or "MONITOR").upper()[:10], ["/bin/sh", "-c", mon]),
            ("04", "STORE", None),
            ("05", "GAMES", None),
            ("06", "IMAGES", None),
        ]
        plugin_dir = os.path.join(os.path.dirname(self.config_file), "plugins")
        try:
            names = sorted(n for n in os.listdir(plugin_dir) if n.endswith(".json"))
        except OSError:
            names = []
        n = 7
        for name in names:
            if n > 9:
                break
            try:
                with open(os.path.join(plugin_dir, name)) as f:
                    p = json.load(f)
            except (OSError, ValueError):
                continue
            if isinstance(p, dict) and p.get("name") and p.get("command"):
                mods.append((f"{n:02d}", p["name"].upper()[:10], ["/bin/sh", "-c", p["command"]]))
                n += 1
        mods.append(("00", "SETTINGS", self.menu_cmd + ["--settings"]))
        return [{"num": a, "name": b, "cmd": c} for a, b, c in mods]

    def log(self, text):
        self.events.appendleft(f"{time.strftime('%H:%M')} {text}")

    # ---------------------------------------------------------------- modules
    def open(self, module):
        """Open a module, or bring it to the front if it is already open."""
        for w in self.windows:
            if w.module["num"] == module["num"]:
                self.shown, self.focus = w, "window" if w.term else "deck"
                return
        if module["cmd"] is None:
            w = Window(module, info=SOON.get(module["name"], ["NOT AVAILABLE YET."]))
            self.log(f"MODULE {module['num']} OFFLINE")
        else:
            rows, cols = self.inner
            try:
                child = self.spawn(module["cmd"], rows, cols)
            except OSError:
                self.log(f"MODULE {module['num']} FAILED")
                return
            w = Window(module, child, vtmod.VT(rows, cols))
            self.log(f"MODULE {module['num']} ENGAGED")
        self.windows.append(w)
        self.shown, self.focus = w, ("window" if w.term else "deck")

    def close(self, w, ended=False):
        """A module window goes away (the program ended, or X was pressed)."""
        if w in self.windows:
            self.windows.remove(w)
        if w.child and not ended:
            w.child.stop()
        self.log(f"MODULE {w.num} {'DISENGAGED' if not ended else 'ENDED'}")
        if self.shown is w:
            self.shown = self.windows[-1] if self.windows else None
        self.focus = "deck"

    def panes(self):
        return [w for w in self.windows if w.child]

    # ---------------------------------------------------------------- keys
    def key(self, text, translate):
        """Handle typed text. Returns "exit" to leave the deck, else None.
        translate(text, term) turns console key codes into what the program expects."""
        if self.focus == "window" and self.shown and self.shown.child:
            parts = re.split("(" + "|".join(re.escape(k) for k in DECK_KEYS) + ")", text)
            for part in parts:
                if part in DECK_KEYS:
                    self.focus = "deck"
                elif part and self.focus == "window":
                    self.shown.child.write(translate(part, self.shown.term))
                elif part:
                    self.key(part, translate)
            return None
        for tok in TOKEN_RE.findall(text):
            name = KEYNAMES.get(tok, tok)
            n = len(self.modules)
            if name in ("up", "k"):
                self.sel = (self.sel - 1) % n
            elif name in ("down", "j"):
                self.sel = (self.sel + 1) % n
            elif name in ("enter", "right", "l"):
                self.open(self.modules[self.sel])
            elif name.isdigit() and len(name) == 1:
                for i, m in enumerate(self.modules):
                    if m["num"] == f"0{name}":
                        self.sel = i
                        self.open(m)
            elif name == "tab" and self.windows:
                i = self.windows.index(self.shown) if self.shown in self.windows else -1
                self.shown = self.windows[(i + 1) % len(self.windows)]
                self.focus = "window" if self.shown.child else "deck"
            elif name == "deck" and self.shown and self.shown.child:
                self.focus = "window"
            elif name in ("x", "X") and self.shown:
                self.close(self.shown)
            elif name in ("q", "Q", "esc"):
                if self.shown and not self.shown.child:  # an info card: close it first
                    self.close(self.shown)
                else:
                    return "exit"
        return None

    # ---------------------------------------------------------------- drawing helpers
    def text_img(self, s, fg, bg=0.0, k=1):
        key = (s, fg, bg, k)
        t = self.textcache.get(key)
        if t is None:
            glyphs = [self.font.glyph(c) for c in s] or [np.zeros((self.fh, self.fw), np.float32)]
            g = np.concatenate(glyphs, axis=1)
            if k > 1:
                g = np.repeat(np.repeat(g, k, axis=0), k, axis=1)
            t = (bg + (fg - bg) * g).astype(np.float32)
            if len(self.textcache) > 2000:
                self.textcache.clear()
            self.textcache[key] = t
        return t

    def text(self, img, x, y, s, fg, bg=0.0, k=1, maxw=None):
        """Text with its top-left corner at pixel (x, y)."""
        if maxw is not None:
            s = s[: max(0, maxw)]
        if not s:
            return
        t = self.text_img(s, fg, bg, k)
        h, w = t.shape
        H, W = img.shape[:2]
        h, w = min(h, H - y), min(w, W - x)
        if h > 0 and w > 0:
            img[y:y + h, x:x + w] = t[:h, :w]

    @staticmethod
    def fill(img, x, y, w, h, level):
        img[max(0, y):y + h, max(0, x):x + w] = level

    def frame(self, img, x, y, w, h, level, t=None):
        t = t or self.line
        img[y:y + t, x:x + w] = level
        img[y + h - t:y + h, x:x + w] = level
        img[y:y + h, x:x + t] = level
        img[y:y + h, x + w - t:x + w] = level

    def meter(self, img, x, y, w, h, frac, segs=8):
        """A block meter: filled segments, then outlined ones."""
        gap = max(2, self.fw // 4)
        sw = (w - gap * (segs - 1)) // segs
        on = int(round(max(0.0, min(1.0, frac)) * segs))
        for i in range(segs):
            sx = x + i * (sw + gap)
            if i < on:
                self.fill(img, sx, y, sw, h, self.lv["bright"])
            else:
                self.frame(img, sx, y, sw, h, self.lv["dim"], 1)

    # ---------------------------------------------------------------- live numbers
    def refresh_stats(self, now):
        if now - self.stats_at < 1.0:
            return
        self.stats_at = now
        if self.status is None:
            try:
                import retromenu
                self.status = retromenu.SystemStatus()
            except Exception:  # noqa: BLE001 - the deck still works without readouts
                self.status = False
        st = self.status
        s = {}
        if st:
            cpu = st.cpu()
            mem, disk = st.memory(), st.disk("/")
            s["cpu"] = cpu if cpu is not None else 0.0
            s["mem"] = mem[0] / mem[1] if mem else 0.0
            s["disk"] = disk[0] / disk[1] if disk else 0.0
            s["uptime"] = st.uptime().upper()
            s["net"] = st.network().split("  ")[0].upper()
        try:
            s["procs"] = str(sum(1 for d in os.listdir("/proc") if d.isdigit()))
        except OSError:
            s["procs"] = "?"
        try:
            with open("/proc/loadavg") as f:
                s["load"] = " ".join(f.read().split()[:3])
        except OSError:
            s["load"] = "?"
        self.stats = s

    # ---------------------------------------------------------------- the picture
    def compose(self, now, blink=True):
        """The whole deck at font resolution, as intensities."""
        self.refresh_stats(now)
        fw, fh, C, R, lv = self.fw, self.fh, self.C, self.R, self.lv
        self.line = max(1, round(fh / 14))
        dim, norm, bright = lv["dim"], lv["normal"], lv["bright"]
        img = np.zeros((R * fh, C * fw), np.float32)
        user = self.user

        # header: PCAL block, title, clock
        self.fill(img, fw, fh // 3, 10 * fw, fh * 2 + fh // 3, bright)
        self.text(img, fw + fw, fh // 3 + fh // 6, "PCAL", 0.0, bright, k=2)
        self.text(img, 12 * fw, fh // 3, "CENTRAL INTERFACE // DESK", norm)
        self.text(img, 12 * fw, fh // 3 + fh + fh // 4, f"PONTES-CALOS DATA SYSTEMS  UNIT 01  OPERATOR {user.upper()}",
                  dim, maxw=C - 12 - 16)
        cx = (C - 14) * fw
        self.frame(img, cx, fh // 3, 13 * fw, fh * 2 + fh // 3, norm)
        self.text(img, cx + fw * 2, fh // 3 + fh // 5, time.strftime("%H:%M:%S"), bright)
        self.text(img, cx + fw * 2, fh // 3 + fh + fh // 4, time.strftime("%a %d %b").upper(), dim)
        # hazard stripe
        sy, sh = 3 * fh + fh // 4, max(4, fh // 2)
        yy, xx = np.mgrid[0:sh, 0:(C - 2) * fw]
        img[sy:sy + sh, fw:(C - 1) * fw] = np.where(((xx + yy) // max(4, fw)) % 2 == 0, norm, 0.0)

        # module column
        self.text(img, fw, 4 * fh + fh // 4, "MODULES", dim)
        n = len(self.modules)
        avail = R - 5 - 3
        tall = avail >= n * 3 - 1
        step = 3 if tall else (2 if avail >= n * 2 else 1)
        open_nums = {w.num for w in self.windows}
        for i, m in enumerate(self.modules):
            y = (5 + i * step) * fh + (fh // 3 if step > 1 else 0)
            h = (2 * fh if tall else fh) + (fh // 3 if step > 1 and not tall else 0)
            x, w = fw, (self.left) * fw
            selected = i == self.sel and self.focus == "deck"
            active = self.shown is not None and self.shown.module["num"] == m["num"]
            if selected:
                self.fill(img, x, y, w, h, bright)
                fg, bg = 0.0, bright
            else:
                self.frame(img, x, y, w, h, bright if active else (norm if m["cmd"] else dim))
                fg, bg = (bright if active else norm), 0.0
            if tall:
                self.text(img, x + fw // 2 + 2, y + 1, m["num"], fg, bg, k=2)
                self.text(img, x + 6 * fw, y + fh // 6, m["name"], fg, bg, maxw=self.left - 6)
                tag = ("RUNNING" if m["cmd"] else "OPEN") if m["num"] in open_nums else \
                    ("READY" if m["cmd"] else "OFFLINE")
                self.text(img, x + 6 * fw, y + fh - fh // 8, tag, fg if selected else dim, bg, maxw=self.left - 6)
            else:
                mark = "*" if m["num"] in open_nums else " "
                self.text(img, x + fw // 2, y + (h - fh) // 2, f"{m['num']} {m['name']}{mark}", fg, bg,
                          maxw=self.left - 1)

        # window area
        ax, ay, aw, ah = self.ax * fw, self.ay * fh, self.aw * fw, self.ah * fh
        w = self.shown
        if w is None:
            self.frame(img, ax, ay, aw, ah, dim)
            msg = "SELECT MODULE"
            k = 2 if aw > len(msg) * fw * 2 + 4 * fw else 1
            self.text(img, ax + (aw - len(msg) * fw * k) // 2, ay + ah // 2 - fh * k, msg, norm, k=k)
            hint = "0-9 ENGAGE  ARROWS + ENTER  Q RETURN TO MENU"
            self.text(img, ax + max(fw, (aw - len(hint) * fw) // 2), ay + ah // 2 + fh, hint, dim,
                      maxw=self.aw - 2)
        else:
            focused = self.focus == "window" or not w.child
            self.frame(img, ax, ay, aw, ah, bright if focused else norm)
            title = f" {w.num}  {w.module['name']}"
            right = " F12 DECK " if focused and w.child else " X CLOSE  TAB NEXT "
            if focused:
                self.fill(img, ax, ay, aw, fh, bright)
                self.text(img, ax + fw // 2, ay, title, 0.0, bright, maxw=self.aw - len(right) - 2)
                self.text(img, ax + aw - len(right) * fw - fw // 2, ay, right, 0.0, bright)
            else:
                self.fill(img, ax, ay + fh - self.line, aw, self.line, norm)
                self.text(img, ax + fw // 2, ay, title, norm, maxw=self.aw - len(right) - 2)
                self.text(img, ax + aw - len(right) * fw - fw // 2, ay, right, dim)
            ix, iy = ax + fw, ay + fh + fh // 2
            if w.term:
                rows, cols = self.inner
                cur = (w.term.x, w.term.y) if blink and focused and w.term.cursor_visible else None
                for y in range(min(rows, w.term.rows)):
                    row = self.r.row_image(self.r.row_key(w.term.grid, y, cur))
                    part = row[:, :cols * fw]
                    if self.r.mono:
                        img[iy + y * fh:iy + (y + 1) * fh, ix:ix + part.shape[1]] = part
                    else:
                        img[iy + y * fh:iy + (y + 1) * fh, ix:ix + part.shape[1]] = part.mean(axis=2)
            else:
                self.text(img, ix + fw, iy + fh, "MODULE OFFLINE", bright, k=2)
                for j, line in enumerate(w.info):
                    self.text(img, ix + fw, iy + fh * (4 + j), line, norm, maxw=self.aw - 4)
                self.text(img, ix + fw, iy + fh * (5 + len(w.info)), "Q OR X TO CLOSE", dim)

        # SYSTEM INTEGRITY
        if self.side:
            px, py, pw, ph = (C - self.side - 1) * fw, 5 * fh, self.side * fw, (R - 8) * fh
            self.frame(img, px, py, pw, ph, norm)
            x = px + fw
            self.text(img, x, py + fh // 2, "SYSTEM INTEGRITY", bright)
            self.fill(img, x, py + fh + fh // 2, pw - 2 * fw, self.line, norm)
            y = py + 2 * fh
            mw = pw - 2 * fw
            st = self.stats
            for label, key in (("PROCESSOR", "cpu"), ("MEMORY", "mem"), ("STORAGE", "disk")):
                v = st.get(key, 0.0)
                self.text(img, x, y, label, dim)
                self.text(img, x + mw - 4 * fw, y, f"{round(v * 100):3d}%", norm)
                self.meter(img, x, y + fh + fh // 6, mw, fh - fh // 4, v)
                y += 2 * fh + fh // 2
            for label, key in (("UPTIME", "uptime"), ("PROCESSES", "procs"), ("LOAD", "load"), ("NETWORK", "net")):
                self.text(img, x, y, label, dim)
                self.text(img, x + 10 * fw, y, str(st.get(key, "?")), norm, maxw=self.side - 12)
                y += fh
            y += fh // 2
            self.text(img, x, y, "EVENT LOG", dim)
            y += fh + fh // 4
            bottom = py + ph - 2 * fh
            for ev in self.events:
                if y + fh > bottom:
                    break
                self.text(img, x, y, ev, norm, maxw=self.side - 2)
                y += fh
            self.fill(img, x, py + ph - fh - fh // 2, mw, fh + fh // 4, bright)
            self.text(img, x + fw // 2, py + ph - fh - fh // 2 + fh // 8, "ALL SYSTEMS NOMINAL", 0.0, bright,
                      maxw=self.side - 3)

        # taskbar
        ty = (R - 2) * fh + fh // 3
        self.fill(img, fw, ty, (C - 2) * fw, self.line, norm)
        y = (R - 1) * fh - fh // 6
        x = fw
        self.text(img, x, y, "OPEN:", dim)
        x += 6 * fw
        for w in self.windows:
            label = f" {w.num} {w.module['name']} "
            if w is self.shown:
                self.fill(img, x, y, len(label) * fw, fh, bright)
                self.text(img, x, y, label, 0.0, bright)
            else:
                self.text(img, x, y, label, dim)
            x += (len(label) + 1) * fw
        if not self.windows:
            self.text(img, x, y, "NONE", dim)
            x += 5 * fw
        prompt = "> AWAITING INPUT" + ("_" if blink else " ")
        if x + (len(prompt) + 2) * fw < C * fw - 36 * fw:
            self.text(img, x + 2 * fw, y, prompt, norm)
        hint = "F12 BACK TO DECK" if self.focus == "window" else "0-9 MODULE  TAB NEXT  X CLOSE  Q MENU"
        self.text(img, C * fw - (len(hint) + 1) * fw, y, hint, dim)
        return img
