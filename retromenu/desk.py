# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The Desk: PCal's command deck.

A full-screen board of tiles for your programs, plugins, games and tools, with live
telemetry and a news ticker, reached from the main menu through a warp animation.
Arrow keys (or h j k l) move, ENTER opens, 1-9 open a tile directly, q goes back.
"""
import curses
import math
import os
import random
import sys
import time

TILE_W, TILE_H = 22, 7

# Small pictures for the tiles: plain ASCII, so they work on every screen.
ICONS = {
    "terminal": [" .--------. ", " | >_     | ", " '--------' "],
    "files":    ["  ___       ", " |   \\____ ", " |________| "],
    "monitor":  [" |    /\\    ", " | /\\/  \\/\\ ", " '---------' "],
    "store":    ["   ______   ", "  /_____/|  ", "  |_$___|/  "],
    "games":    [" .--------. ", "(  +    o  )", " '--------' "],
    "images":   [" .--------. ", " | /\\/\\ o | ", " '--------' "],
    "settings": ["   _/\\_    ", "  <_ o _>   ", "    \\/     "],
    "plugin":   ["   ____     ", "  [_++_]    ", "            "],
    "menu":     ["  ______    ", "  |====|    ", "  |====|    "],
}

TICKER = [
    "ALL SYSTEMS NOMINAL", "PHOSPHOR AT OPERATING TEMPERATURE", "LINK {net}",
    "UPTIME {uptime}", "{procs} PROCESSES ACTIVE", "LOAD {load}",
    "PONTES-CALOS DATA SYSTEMS", "WELCOME ABOARD, {user}",
]


class Desk:
    def __init__(self, app):
        self.app = app
        self.rm = sys.modules[type(app).__module__]  # helpers of the menu program
        self.sel = 0
        self.tick = 0

    # ---------------------------------------------------------------- tiles
    def tiles(self):
        a, c, rm = self.app, self.app.cfg, self.rm
        home = os.path.expanduser("~")
        t = [
            ("TERMINAL", "terminal", "Full command line", lambda: a.open_terminal(home)),
            ("FILES", "files", "Browse and manage", a.files.run),
            (c["monitor_name"].upper(), "monitor", "Running processes", lambda: a.run(c["monitor_cmd"])),
            ("STORE", "store", "Get software", lambda: self.soon("STORE", [
                "The PCal software store is on its way.",
                "Browse, search and install Alpine packages with the arrow keys."])),
            ("GAMES", "games", "Play something", lambda: self.soon("GAMES", [
                "Games are on their way: snake first, then falling blocks and lunar lander."])),
            ("IMAGES", "images", "Phosphor viewer", lambda: self.soon("IMAGES", [
                "The phosphor image viewer is on its way.",
                "It shows pictures in green or amber on the graphical screen."])),
        ]
        for p in a.plugins:
            it = a.plugin_item(p)
            act = it["action"] or (lambda it=it: a.open_item(it, 2, 2, self.draw))
            t.append((p["name"].upper()[:16], "plugin", p.get("description", "Plugin")[:18], act))
        t += [
            ("SETTINGS", "settings", "Look and feel", a.settings),
            ("MAIN MENU", "menu", "Back to the menu", None),
        ]
        return t

    def soon(self, title, lines):
        self.app.show_text(title + " // COMING SOON", lines)

    # ---------------------------------------------------------------- drawing
    def box(self, y, x, h, w, attr, title=None):
        B, put, scr = self.app.B, self.rm.put, self.app.scr
        put(scr, y, x, B["tl"] + B["h"] * (w - 2) + B["tr"], attr)
        for r in range(1, h - 1):
            put(scr, y + r, x, B["v"], attr)
            put(scr, y + r, x + w - 1, B["v"], attr)
        put(scr, y + h - 1, x, B["bl"] + B["h"] * (w - 2) + B["br"], attr)
        if title:
            put(scr, y, x + 2, f" {title} ", attr)

    def brackets(self, y, x, h, w, attr):
        """HUD corner marks around an area."""
        put, scr = self.rm.put, self.app.scr
        tl, tr, bl, br = ("┌", "┐", "└", "┘") if self.app.utf8 and self.app.B["h"] != "=" else ("+",) * 4
        for (yy, xx, ch) in ((y, x, tl), (y, x + w - 1, tr), (y + h - 1, x, bl), (y + h - 1, x + w - 1, br)):
            put(scr, yy, xx, ch, attr)

    def layout(self, H, W, n):
        side = 30 if W >= 3 * (TILE_W + 2) + 4 + 32 else 0
        area_w = W - 4 - (side + 2 if side else 0)
        cols = max(1, min(n, area_w // (TILE_W + 2)))
        rows = -(-n // cols)
        tile_h = TILE_H if 3 + rows * (TILE_H + 1) + 3 <= H else max(4, (H - 6) // rows - 1)
        grid_w = cols * (TILE_W + 2) - 2
        x0 = 2 + max(0, (area_w - grid_w) // 2)
        grid_h = rows * (tile_h + 1) - 1
        block_h = max(grid_h, 17 if side else 0) + 2  # + the caption line above the tiles
        y0 = 2 + max(0, (H - 5 - block_h) // 2) + 2
        return cols, rows, tile_h, x0, side, y0

    def draw(self, shown=None, flicker=None):
        """Draw the Desk. shown: how many tiles are visible yet (for the intro)."""
        a, put, scr = self.app, self.rm.put, self.app.scr
        H, W = scr.getmaxyx()
        scr.erase()
        tiles = self.tiles()
        n = len(tiles)
        self.sel = max(0, min(self.sel, n - 1))
        cols, rows, th, x0, side, y0 = self.layout(H, W, n)
        self.cols = cols
        # top strip
        left = f" PCAL // DESK "
        right = f" {a.user}@{a.host}  {time.strftime('%a %d %b').upper()}  {time.strftime('%H:%M:%S')} "
        put(scr, 0, 0, a.B["h"] * W, a.dim)
        put(scr, 0, 2, left, a.bold)
        put(scr, 0, max(len(left) + 4, W - len(right) - 2), right, a.norm)
        # tiles
        shown = n if shown is None else shown
        caption = f"COMMAND DECK  ::  {n} MODULES ONLINE"
        put(scr, y0 - 2, x0, caption, a.dim)
        for i, (label, icon, desc, _) in enumerate(tiles[:shown]):
            r, cidx = divmod(i, cols)
            y, x = y0 + r * (th + 1), x0 + cidx * (TILE_W + 2)
            if y + th > H - 3:
                break
            on = i == self.sel
            attr = a.bold if on else a.dim
            if flicker is not None and i == flicker:
                attr = a.sel
            self.box(y, x, th, TILE_W, attr)
            if on:
                self.brackets(y - 1 if y > 1 else y, x - 1 if x > 0 else x, th + 2 if y > 1 else th + 1,
                              TILE_W + 2, a.bold)
            art = ICONS.get(icon, ICONS["plugin"])
            if th >= 7:
                for j, line in enumerate(art):
                    put(scr, y + 1 + j, x + (TILE_W - len(line)) // 2, line, a.bold if on else a.norm)
            name = f"{i + 1} {label}" if i < 9 else label
            put(scr, y + th - 3 if th >= 7 else y + 1, x + 2, self.rm.fit(name, TILE_W - 4),
                a.sel if on else a.bold)
            if th >= 5:
                put(scr, y + th - 2, x + 2, self.rm.fit(desc, TILE_W - 4), a.norm if on else a.dim)
        # telemetry
        if side:
            self.telemetry(y0 - 1, W - side - 2, min(H - y0 - 3, 17), side)
        # ticker
        self.ticker(H - 2, W)
        hints = "ARROWS move  ENTER open  1-9 quick open  q back to menu"
        a.status(H, W, hints)
        scr.noutrefresh()

    def telemetry(self, y, x, h, w):
        a, put, scr = self.app, self.rm.put, self.app.scr
        self.box(y, x, h, w, a.dim, "TELEMETRY")
        bar_w = max(6, w - 16)
        rows = dict(a.status_panel.rows(a.user, a.host, bar_w, a.B))
        def pct(v):
            return v.split("%")[0] + "%" if "%" in v else v

        lines = [("CPU", pct(rows.get("CPU", ""))), ("MEM", pct(rows.get("MEMORY", ""))),
                 ("DISK", pct(rows.get("DISK /", ""))), ("", ""),
                 ("UP", rows.get("UPTIME", "")), ("LOAD", rows.get("LOAD", "")),
                 ("PROC", rows.get("PROCESSES", "")), ("NET", rows.get("NETWORK", ""))]
        for i, (k, v) in enumerate(lines[: h - 3]):
            put(scr, y + 2 + i, x + 2, k.ljust(5), a.dim)
            put(scr, y + 2 + i, x + 8, self.rm.fit(v, w - 10), a.norm)
        # a tiny scanning wave, for the look
        wave_y = y + h - 2
        if h >= 12:
            chars = "_.-~^~-."
            line = "".join(chars[(i + self.tick) % len(chars)] for i in range(w - 4))
            put(scr, wave_y, x + 2, line, a.dim)

    def ticker(self, y, W):
        a, put, scr = self.app, self.rm.put, self.app.scr
        rows = dict(a.status_panel.rows(a.user, a.host, 6, a.B))
        info = dict(net=rows.get("NETWORK", "?").upper(), uptime=rows.get("UPTIME", "?").upper(),
                    procs=rows.get("PROCESSES", "?"), load=rows.get("LOAD", "?"), user=a.user.upper())
        sep = "  ///  "
        text = sep.join(t.format(**info) for t in TICKER) + sep
        start = self.tick % len(text)
        line = (text[start:] + text) [: W - 4]
        put(scr, y, 2, line, a.dim)

    # ---------------------------------------------------------------- animation
    def warp(self, inward=False):
        """Hyperspace: streaks rush out from the middle of the screen (or back in)."""
        a, put, scr = self.app, self.rm.put, self.app.scr
        if a.fx == "off":
            return
        H, W = scr.getmaxyx()
        cy, cx = H / 2, W / 2
        rnd = random.Random(7)
        stars = [(rnd.uniform(0, 2 * math.pi), rnd.uniform(0.05, 0.35)) for _ in range(70)]
        frames = 12 if a.fx == "full" else 7
        order = range(frames, 0, -1) if inward else range(1, frames + 1)
        try:
            for f in order:
                scr.erase()
                t = f / frames
                for ang, start in stars:
                    dx, dy = math.cos(ang), math.sin(ang) * 0.5
                    r0 = (start + t * 1.6) * max(W, H * 2) * 0.5
                    length = 1 + int(t * t * 9)
                    ch = "-" if abs(dx) > 2 * abs(dy) * 2 else ("|" if abs(dy) * 2 > 2 * abs(dx) else
                                                               ("\\" if dx * dy > 0 else "/"))
                    for k in range(length):
                        rr = r0 + k
                        yy, xx = int(cy + dy * rr), int(cx + dx * rr * 1.0)
                        if 0 <= yy < H and 0 <= xx < W - 1:
                            put(scr, yy, xx, ch, a.bold if k == length - 1 else a.norm)
                core = max(1, int(6 * (1 - t)))
                put(scr, int(cy), max(0, int(cx) - core), "=" * (core * 2), a.bold)
                scr.noutrefresh()
                curses.doupdate()
                time.sleep(0.03 if a.fx == "full" else 0.035)
        except curses.error:
            pass

    def intro(self):
        """Tiles flicker in one by one."""
        if self.app.fx == "off":
            return
        n = len(self.tiles())
        try:
            for i in range(1, n + 1):
                self.draw(shown=i, flicker=i - 1)
                curses.doupdate()
                time.sleep(0.03)
        except curses.error:
            pass

    # ---------------------------------------------------------------- loop
    def run(self):
        a, rm = self.app, self.rm
        self.warp()
        self.intro()
        while True:
            tiles = self.tiles()
            n = len(tiles)
            a.bg = self.draw
            self.draw()
            curses.doupdate()
            a.scr.timeout(200 if a.fx != "off" else 1000)
            k = a.read_key(a.scr)
            a.scr.timeout(-1)
            if k == -1:
                self.tick += 1
                continue
            if k == curses.KEY_RESIZE:
                continue
            cols = getattr(self, "cols", 1)
            if isinstance(k, str) and k.isdigit() and k != "0":
                if int(k) <= n:
                    self.sel = int(k) - 1
                    k = 10
                else:
                    continue
            if isinstance(k, str):
                k = ord(k)
            if k in (curses.KEY_LEFT, ord("h")):
                self.sel = (self.sel - 1) % n
            elif k in (curses.KEY_RIGHT, ord("l")):
                self.sel = (self.sel + 1) % n
            elif k in rm.K_UP:
                self.sel = self.sel - cols if self.sel - cols >= 0 else self.sel
            elif k in rm.K_DOWN:
                self.sel = self.sel + cols if self.sel + cols < n else (n - 1 if self.sel // cols < (n - 1) // cols else self.sel)
            elif k in (27, ord("q"), curses.KEY_BACKSPACE, 127):
                break
            elif k in rm.K_ENTER:
                act = tiles[self.sel][3]
                if act is None:
                    break
                act()
                a.scr.clear()
        self.warp(inward=True)
        a.scr.clear()
        return None
