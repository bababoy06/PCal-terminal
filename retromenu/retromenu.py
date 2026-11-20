#!/usr/bin/env python3
"""
RetroMenu - a green-phosphor, arrow-key driven home screen for a lightweight Linux.

Keys (everywhere):
  UP / DOWN        move            (also k / j)
  RIGHT / ENTER    open / select   (also l)
  LEFT / ESC / q   back            (also h)
  PGUP / PGDN      scroll a page
  HOME / END       first / last item

Only uses the Python standard library (curses), so on Alpine: apk add python3
"""
import curses
import curses.textpad
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
import textwrap
import time

VERSION = "0.1"
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
    "reboot_cmd": "reboot",
}

THEMES = {
    "green": curses.COLOR_GREEN,
    "amber": curses.COLOR_YELLOW,
    "white": curses.COLOR_WHITE,
    "cyan": curses.COLOR_CYAN,
}

# Banner built from figlet-style letters, so it is easy to change.
_R = [" ____  ", "|  _ \\ ", "| |_) |", "|  _ < ", "|_| \\_\\"]
_E = [" _____ ", "| ____|", "|  _|  ", "| |___ ", "|_____|"]
_T = [" _____ ", "|_   _|", "  | |  ", "  | |  ", "  |_|  "]
_O = ["  ___  ", " / _ \\ ", "| | | |", "| |_| |", " \\___/ "]
BANNER = [" ".join(parts) for parts in zip(_R, _E, _T, _R, _O)]

K_UP = (curses.KEY_UP, ord("k"))
K_DOWN = (curses.KEY_DOWN, ord("j"))
K_FWD = (curses.KEY_RIGHT, ord("l"))
K_BACK = (curses.KEY_LEFT, ord("h"), 27, ord("q"))
K_ENTER = (10, 13, curses.KEY_ENTER)

EXIT = object()  # returned by the "Exit" item to leave the program


# --------------------------------------------------------------------- helpers
def item(label, action=None, sub=None):
    """A menu entry. `sub` is a list of items, or a function returning one."""
    return {"label": label, "action": action, "sub": sub}


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


def clamp_scroll(sel, top, height, n):
    if n == 0 or height <= 0:
        return 0, 0
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


# ------------------------------------------------------------------ file model
class Entry:
    __slots__ = ("name", "path", "is_dir", "is_link", "_de", "_size")

    def __init__(self, de):
        self.name = de.name
        self.path = de.path
        self._de = de  # DirEntry caches its stat() result
        self._size = None
        try:
            self.is_dir = de.is_dir()
        except OSError:
            self.is_dir = False
        try:
            self.is_link = de.is_symlink()
        except OSError:
            self.is_link = False

    def size(self):
        # Only called for rows on screen: big folders open without stat-ing every file.
        if self._size is None:
            try:
                self._size = self._de.stat().st_size
            except OSError:
                self._size = -1
        return self._size


# ------------------------------------------------------------------------- app
class App:
    def __init__(self, scr):
        self.scr = scr
        self.cfg = load_config()
        self.plugins = []
        self.bg = lambda: None  # redraws whatever screen is currently below a dialog
        self.host = socket.gethostname()
        try:
            self.user = pwd.getpwuid(os.getuid()).pw_name
        except KeyError:
            self.user = str(os.getuid())
        curses.raw()  # Ctrl-C / Ctrl-Z don't kill the home screen
        cursor(0)
        scr.keypad(True)
        self.colors = curses.has_colors()
        if self.colors:
            curses.start_color()
        self.apply_theme()
        self.files = FileManager(self)

    # ---- look
    def apply_theme(self):
        if self.colors:
            fg = THEMES.get(self.cfg.get("theme"), curses.COLOR_GREEN)
            curses.init_pair(1, fg, curses.COLOR_BLACK)
            self.norm = curses.color_pair(1)
        else:
            self.norm = curses.A_NORMAL
        self.bold = self.norm | curses.A_BOLD
        self.sel = self.norm | curses.A_REVERSE | curses.A_BOLD
        self.dim = self.norm | curses.A_DIM
        self.scr.bkgd(" ", self.norm)

    def box(self, win, y, x, h, w, title=None):
        try:
            curses.textpad.rectangle(win, y, x, y + h - 1, x + w - 1)
        except curses.error:
            pass
        if title:
            put(win, y, x + 2, f"[ {title} ]"[: max(0, w - 4)], self.bold)

    def draw_rows(self, win, y, x, h, w, n, row_at, sel, top):
        """Draw list rows `top..top+h`. row_at(i) -> (left_text, right_text)."""
        for i in range(h):
            idx = top + i
            if idx >= n:
                break
            text, right = row_at(idx)
            avail = w - len(right) - 3
            if avail < 1:
                right, avail = "", w - 2
            if len(text) > avail:
                text = text[: max(0, avail - 1)] + "~"
            line = (" " + text.ljust(avail) + " " + right + " ")[:w].ljust(w)
            put(win, y + i, x, line, self.sel if idx == sel else self.norm)
        if n > h > 0:  # scroll markers
            if top > 0:
                put(win, y, x + w - 1, "^", self.bold)
            if top + h < n:
                put(win, y + h - 1, x + w - 1, "v", self.bold)

    def draw_banner(self, H, W):
        if H >= 22 and W >= len(BANNER[0]) + 2:
            for i, line in enumerate(BANNER):
                put(self.scr, 1 + i, (W - len(line)) // 2, line, self.bold)
            sub = f":: {self.user}@{self.host} :: RetroMenu v{VERSION} ::"
            put(self.scr, 7, (W - len(sub)) // 2, sub, self.dim)
            return 9
        put(self.scr, 0, 1, f"RETRO :: {self.user}@{self.host}", self.bold)
        return 1

    def status(self, H, W, hints):
        clock = time.strftime("%H:%M:%S")
        left = f" {hints}"
        room = max(0, W - len(clock) - 2)
        put(self.scr, H - 1, 0, (left[:room].ljust(room) + " " + clock + " ")[:W], self.sel)

    @staticmethod
    def label(it):
        return (it["label"], ">" if it.get("sub") is not None else "")

    @staticmethod
    def resolve(sub):
        return sub() if callable(sub) else sub

    # ---- generic full-screen menu (main menu, settings)
    def menu_screen(self, title, build, root=False):
        sel, top = 0, 0
        geo = {}
        while True:
            items = build()
            n = len(items)
            sel = max(0, min(sel, n - 1))
            rows = [self.label(i) for i in items]

            def draw():
                nonlocal top
                H, W = self.scr.getmaxyx()
                self.scr.erase()
                y = self.draw_banner(H, W)
                bw = min(W, max(30, max(len(r[0]) for r in rows) + 8, len(title) + 8))
                bh = max(3, min(H - y - 2, n + 2))
                bx = max(0, (W - bw) // 2)
                inner = bh - 2
                _, top = clamp_scroll(sel, top, inner, n)
                self.box(self.scr, y, bx, bh, bw, title)
                self.draw_rows(self.scr, y + 1, bx + 1, inner, bw - 2, n, rows.__getitem__, sel, top)
                back = "" if root else "  LEFT back"
                self.status(H, W, f"UP/DN move  RIGHT/ENTER select{back}")
                self.scr.noutrefresh()
                geo.update(y=y, x=bx, w=bw, inner=inner)

            self.bg = draw
            draw()
            curses.doupdate()
            self.scr.timeout(1000)  # wake up every second for the clock
            k = self.scr.getch()
            self.scr.timeout(-1)

            if k in (-1, curses.KEY_RESIZE):
                continue
            if k in K_UP:
                sel = (sel - 1) % n
            elif k in K_DOWN:
                sel = (sel + 1) % n
            elif k == curses.KEY_PPAGE:
                sel = max(0, sel - geo["inner"])
            elif k == curses.KEY_NPAGE:
                sel = min(n - 1, sel + geo["inner"])
            elif k == curses.KEY_HOME:
                sel = 0
            elif k == curses.KEY_END:
                sel = n - 1
            elif k in K_FWD or k in K_ENTER:
                y = geo["y"] + 1 + sel - top
                if self.open_item(items[sel], y, geo["x"] + geo["w"] - 2, draw) is EXIT:
                    return EXIT
            elif k in K_BACK and not root:
                return None

    def open_item(self, it, y, x, redraw):
        act = it.get("action")
        if it.get("sub") is not None:
            act = self.popup(self.resolve(it["sub"]), y, x, redraw)
        if act:
            return act()
        return None

    # ---- drop-down menu; RIGHT opens sub-options, LEFT goes back one level
    def popup(self, items, y, x, redraw, title=None):
        """Returns the chosen leaf's action (not yet run), or None if backed out."""
        if not items:
            return None
        H, W = self.scr.getmaxyx()
        rows = [self.label(i) for i in items]
        n = len(items)
        w = min(W, max(18, max(len(r[0]) for r in rows) + 6, len(title) + 6 if title else 0))
        h = min(n + 2, H - 1)
        if h < 3:
            return None
        y = max(0, min(y, H - 1 - h))
        x = max(0, min(x, W - w))
        win = curses.newwin(h, w, y, x)
        win.keypad(True)
        win.bkgd(" ", self.norm)
        sel, top, inner = 0, 0, h - 2

        def draw():
            nonlocal top
            _, top = clamp_scroll(sel, top, inner, n)
            win.erase()
            win.box()
            if title:
                put(win, 0, 2, f" {title} "[: w - 4], self.bold)
            self.draw_rows(win, 1, 1, inner, w - 2, n, rows.__getitem__, sel, top)
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
                sel = max(0, sel - inner)
            elif k == curses.KEY_NPAGE:
                sel = min(n - 1, sel + inner)
            elif k in K_FWD or k in K_ENTER:
                it = items[sel]
                if it.get("sub") is not None:
                    act = self.popup(self.resolve(it["sub"]), y + 1 + sel - top, x + w - 1,
                                     lambda: (redraw(), draw()))
                    if act:
                        return act
                else:
                    return it.get("action") or (lambda: None)
            elif k in K_BACK or k == curses.KEY_RESIZE:
                return None

    # ---- dialogs
    def confirm(self, question):
        answer = []
        items = [item("No", lambda: answer.append(False)), item("Yes", lambda: answer.append(True))]
        H, W = self.scr.getmaxyx()
        act = self.popup(items, H // 2 - 2, (W - len(question) - 6) // 2, self.bg, title=question)
        if act:
            act()
        return bool(answer and answer[0])

    def prompt(self, label, initial=""):
        """One-line text input. Returns the string, or None on ESC."""
        H, W = self.scr.getmaxyx()
        w = min(W, max(44, len(label) + 6, len(initial) + 6))
        h = 5
        win = curses.newwin(h, w, max(0, (H - h) // 2), max(0, (W - w) // 2))
        win.keypad(True)
        win.bkgd(" ", self.norm)
        buf = list(initial)
        field = w - 4
        cursor(1)
        try:
            while True:
                self.bg()
                win.erase()
                win.box()
                put(win, 0, 2, f" {label} "[: w - 4], self.bold)
                text = "".join(buf)
                vis = text[-(field - 1):] if len(text) >= field else text
                put(win, 2, 2, vis.ljust(field), self.bold | curses.A_UNDERLINE)
                put(win, 3, 2, "ENTER ok   ESC cancel"[:field], self.dim)
                try:
                    win.move(2, 2 + len(vis))
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
        """Scrollable info box (used for Details and messages)."""
        H, W = self.scr.getmaxyx()
        w = min(W, max(40, max((len(l) for l in lines), default=0) + 4, len(title) + 6))
        wrapped = []
        for line in lines:
            wrapped += textwrap.wrap(line, max(10, w - 4), subsequent_indent="    ",
                                     drop_whitespace=False) or [""]
        h = max(5, min(H - 1, len(wrapped) + 4))
        win = curses.newwin(h, w, max(0, (H - h) // 2), max(0, (W - w) // 2))
        win.keypad(True)
        win.bkgd(" ", self.norm)
        inner = max(1, h - 4)
        top = 0
        while True:
            self.bg()
            win.erase()
            win.box()
            put(win, 0, 2, f" {title} "[: w - 4], self.bold)
            for i in range(inner):
                if top + i < len(wrapped):
                    put(win, 1 + i, 2, wrapped[top + i][: w - 4])
            hint = "LEFT/ENTER close" + ("   UP/DN scroll" if len(wrapped) > inner else "")
            put(win, h - 2, 2, hint[: w - 4], self.dim)
            win.noutrefresh()
            curses.doupdate()
            k = win.getch()
            maxtop = max(0, len(wrapped) - inner)
            if k in K_UP:
                top = max(0, top - 1)
            elif k in K_DOWN:
                top = min(maxtop, top + 1)
            elif k == curses.KEY_PPAGE:
                top = max(0, top - inner)
            elif k == curses.KEY_NPAGE:
                top = min(maxtop, top + inner)
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
        self.run(shell, cwd=path, note=f"\n[ RetroMenu terminal in {path} -- type 'exit' to return ]\n")

    # ---- main menu
    def main_items(self):
        c = self.cfg
        items = [
            item("Files", self.files.run),
            item("Terminal", lambda: self.open_terminal(os.path.expanduser("~"))),
            item(c["monitor_name"], lambda: self.run(c["monitor_cmd"])),
        ]
        items += [self.plugin_item(p) for p in self.plugins]
        if not self.plugins:
            items.append(item("Placeholder", self.plugin_help))
        items += [
            item("Settings", self.settings),
            item("Reboot", self.reboot),
            item("Exit", lambda: EXIT),
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

        if p.get("options") or p.get("options_cmd"):
            return item(p["name"], sub=build)
        if p.get("command"):
            return item(p["name"], launch(p["command"], p.get("pause", False)))
        return item(p["name"], lambda: self.show_text(p["name"], ["This plugin has no command or options."]))

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
        if key == "theme":
            self.apply_theme()

    def edit(self, key, label):
        val = self.prompt(label, str(self.cfg[key]))
        if val is not None and val.strip():
            self.set(key, val.strip())

    def settings(self):
        c = self.cfg
        onoff = lambda v: "on" if v else "off"

        def build():
            return [
                item(f"Theme ........... {c['theme']}",
                     sub=lambda: [item(t, lambda t=t: self.set("theme", t)) for t in THEMES]),
                item(f"Monitor name .... {c['monitor_name']}",
                     lambda: self.edit("monitor_name", "Name in main menu")),
                item(f"Monitor command . {c['monitor_cmd']}",
                     lambda: self.edit("monitor_cmd", "Command to run")),
                item(f"Shell ........... {c['shell']}", lambda: self.edit("shell", "Shell")),
                item(f"Editor .......... {c['editor']}", lambda: self.edit("editor", "Editor")),
                item(f"Pager ........... {c['pager']}", lambda: self.edit("pager", "Pager")),
                item(f"Hidden files .... {onoff(c['show_hidden'])}",
                     lambda: self.set("show_hidden", not c["show_hidden"])),
                item(f"Boot splash ..... {onoff(c['splash'])}",
                     lambda: self.set("splash", not c["splash"])),
                item(f"Reboot command .. {c['reboot_cmd']}",
                     lambda: self.edit("reboot_cmd", "Reboot command")),
                item("Reload plugins", self.reload_plugins),
                item("Reset to defaults", sub=[item("No"), item("Yes, reset", self.reset)]),
            ]

        self.menu_screen("SETTINGS", build)

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

    # ---- boot splash (does real work: loads plugins and pre-reads ~)
    def splash(self):
        steps = [
            ("LOADING CONFIG", lambda: None),
            ("SCANNING PLUGINS", self.reload_plugins),
            ("INDEXING HOME", self.files.load),
            ("READY", lambda: None),
        ]
        for i, (name, fn) in enumerate(steps):
            fn()
            if not self.cfg.get("splash"):
                continue
            H, W = self.scr.getmaxyx()
            self.scr.erase()
            y = max(0, H // 2 - 5)
            for j, line in enumerate(BANNER):
                put(self.scr, y + j, (W - len(line)) // 2, line, self.bold)
            bar_w = max(10, min(40, W - 6))
            filled = bar_w * (i + 1) // len(steps)
            bar = "[" + "#" * filled + "." * (bar_w - filled) + "]"
            put(self.scr, y + 7, (W - len(bar)) // 2, bar, self.bold)
            put(self.scr, y + 8, (W - len(name)) // 2, name, self.dim)
            self.scr.refresh()
            time.sleep(0.06)


# ---------------------------------------------------------------- file manager
class FileManager:
    def __init__(self, app):
        self.app = app
        self.cwd = os.path.expanduser("~")
        self.cache = {}  # path -> (mtime_ns, show_hidden, entries); reused if unchanged
        self.pos = {}  # path -> (sel, top), so going back lands where you were
        self.entries = []
        self.error = None
        self.sel = self.top = 0
        self.inner = 10

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

    def row(self, i):
        e = self.entries[i]
        if e.is_dir:
            return ("[+] " + e.name + "/" + ("@" if e.is_link else ""), "<DIR>")
        size = e.size()
        return ("    " + e.name + ("@" if e.is_link else ""), human_size(size) if size >= 0 else "?")

    def run(self):
        a = self.app
        while True:
            self.load()  # one cheap stat; rescans only if the folder changed
            n = len(self.entries)
            self.sel, self.top = clamp_scroll(self.sel, self.top, self.inner, n)

            def draw():
                H, W = a.scr.getmaxyx()
                a.scr.erase()
                path = self.cwd
                room = W - 12
                if len(path) > room:
                    path = "..." + path[-(room - 3):]
                put(a.scr, 0, 0, (" FILES :: " + path).ljust(W), a.bold)
                bh = max(3, H - 2)
                self.inner = bh - 2
                self.sel, self.top = clamp_scroll(self.sel, self.top, self.inner, n)
                a.box(a.scr, 1, 0, bh, W, f"{n} items")
                if self.error:
                    put(a.scr, 2, 2, f"Cannot read folder: {self.error}", a.bold)
                elif n == 0:
                    put(a.scr, 2, 2, "(empty folder - press ENTER for options)", a.dim)
                else:
                    a.draw_rows(a.scr, 2, 1, self.inner, W - 2, n, self.row, self.sel, self.top)
                a.status(H, W, "ENTER actions  RIGHT open  LEFT up  . hidden  q menu")
                a.scr.noutrefresh()

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
                self.sel = max(0, self.sel - self.inner)
            elif k == curses.KEY_NPAGE:
                self.sel = min(n - 1, self.sel + self.inner)
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
        y = 2 + self.sel - self.top
        x = min(8 + len(ent.name), 40) if ent else 8
        title = ent.name if ent else os.path.basename(self.cwd) or "/"
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
            f"Name     : {ent.name}",
            f"Path     : {ent.path}",
            f"Type     : {kind}",
        ]
        if stat.S_ISLNK(m):
            try:
                lines.append(f"Points to: {os.readlink(ent.path)}")
            except OSError:
                pass
        if ent.is_dir:
            try:
                lines.append(f"Contains : {len(os.listdir(ent.path))} items")
            except OSError:
                lines.append("Contains : (no access)")
        else:
            lines.append(f"Size     : {human_size(st.st_size)} ({st.st_size} bytes)")
        mime = mimetypes.guess_type(ent.name)[0]
        lines += [
            f"Perms    : {stat.filemode(m)} ({oct(m & 0o7777)})",
            f"Owner    : {owner}:{group}",
            f"Modified : {fmt(st.st_mtime)}",
            f"Accessed : {fmt(st.st_atime)}",
            f"Changed  : {fmt(st.st_ctime)}",
            f"Inode    : {st.st_ino}   Links: {st.st_nlink}",
            f"MIME     : {mime or 'unknown'}",
        ]
        if shutil.which("file"):
            try:
                out = subprocess.run(["file", "-b", ent.path], capture_output=True, text=True, timeout=2)
                lines.append(f"Content  : {out.stdout.strip()}")
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
    app.menu_screen("MENU", app.main_items, root=True)


if __name__ == "__main__":
    os.environ.setdefault("ESCDELAY", "25")  # ESC feels instant instead of a 1s delay
    locale.setlocale(locale.LC_ALL, "")
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
