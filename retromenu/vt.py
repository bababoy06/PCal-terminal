# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
A small terminal emulator core (xterm-256color subset) for the graphical PCal screen.

feed() takes the text a program writes; the screen is then in .grid: rows of cells,
each cell a tuple (char, fg, bg, flags). Colours are -1 (default), 0-255 (xterm
palette) or 0x1000000 | 0xRRGGBB (true colour). Anything the terminal must answer
(cursor position reports, device attributes) is collected in .replies.

It covers what PCal and ordinary console programs use: cursor movement, colours,
scroll regions, insert/delete, the alternate screen, the DEC line-drawing set,
application cursor keys and xterm mouse reporting.
"""
import re

BOLD, DIM, REV, UNDER, BLINK, INVIS = 1, 2, 4, 8, 16, 32
RGB = 0x1000000
DEFAULT = -1

# DEC special graphics (ESC ( 0): letters become line-drawing characters.
DEC_GRAPHICS = dict(zip("`afgjklmnopqrstuvwxyz{|}~",
                        "◆▒°±┘┐┌└┼⎺⎻─⎼⎽├┤┴┬│≤≥π≠£·"))

CSI_RE = re.compile(r"\x1b\[([?>=!]?)([0-9;:]*)([ -/]*)([@-~])")
OSC_RE = re.compile(r"\x1b\](.*?)(\x07|\x1b\\)", re.S)
LINUX_PAL_RE = re.compile(r"\x1b\]P([0-9a-fA-F])([0-9a-fA-F]{6})|\x1b\]R")
ESC_RE = re.compile(r"\x1b([()*+])(.)|\x1b([#%])(.)|\x1b([0-9A-Za-z=><~}|\\])", re.S)


class VT:
    def __init__(self, rows=30, cols=100):
        self.rows, self.cols = rows, cols
        self.replies = []          # text to send back to the program
        self.title = ""
        self.palette = {}          # Linux console palette changes (ESC ] P n rrggbb)
        self.dirty = True
        self.pending = ""          # an incomplete escape sequence from the last feed()
        self.reset()

    # ------------------------------------------------------------ state
    def reset(self):
        self.fg, self.bg, self.flags = DEFAULT, DEFAULT, 0
        self.grid = self._blank_grid()
        self.alt_grid = None
        self.x = self.y = 0
        self.wrap_next = False
        self.top, self.bot = 0, self.rows - 1
        self.saved = (0, 0, DEFAULT, DEFAULT, 0, False)
        self.cursor_visible = True
        self.app_cursor = False     # DECCKM: arrow keys send ESC O A instead of ESC [ A
        self.app_keypad = False
        self.autowrap = True
        self.insert_mode = False
        self.origin = False
        self.mouse = 0              # 0 off, 1000 clicks, 1002 drag, 1003 any motion
        self.mouse_sgr = False      # 1006: mouse reports as ESC [ < b ; x ; y M/m
        self.bracketed_paste = False
        self.charsets = ["B", "B"]
        self.shift = 0
        self.last = " "
        self.dirty = True

    def _blank(self):
        return (" ", self.fg, self.bg, 0) if self.bg != DEFAULT else (" ", DEFAULT, DEFAULT, 0)

    def _blank_row(self):
        b = self._blank()
        return [b] * self.cols

    def _blank_grid(self):
        return [[(" ", DEFAULT, DEFAULT, 0)] * self.cols for _ in range(self.rows)]

    def resize(self, rows, cols):
        def fit(grid):
            out = [(row + [(" ", DEFAULT, DEFAULT, 0)] * cols)[:cols] for row in grid[-rows:]]
            while len(out) < rows:
                out.append([(" ", DEFAULT, DEFAULT, 0)] * cols)
            return out
        self.grid = fit(self.grid)
        if self.alt_grid is not None:
            self.alt_grid = fit(self.alt_grid)
        self.rows, self.cols = rows, cols
        self.top, self.bot = 0, rows - 1
        self.x, self.y = min(self.x, cols - 1), min(self.y, rows - 1)
        self.dirty = True

    def text(self):
        """The screen as plain text (for tests)."""
        return "\n".join("".join(c[0] for c in row).rstrip() for row in self.grid)

    # ------------------------------------------------------------ output
    def put(self, ch):
        if self.wrap_next and self.autowrap:
            self.x = 0
            self.linefeed()
        self.wrap_next = False
        if self.charsets[self.shift] == "0":
            ch = DEC_GRAPHICS.get(ch, ch)
        row = self.grid[self.y]
        if self.insert_mode:
            row.insert(self.x, self._blank())
            del row[self.cols:]
        row[self.x] = (ch, self.fg, self.bg, self.flags)
        self.last = ch
        if self.x == self.cols - 1:
            self.wrap_next = True
        else:
            self.x += 1

    def linefeed(self):
        if self.y == self.bot:
            self.scroll_up(1)
        elif self.y < self.rows - 1:
            self.y += 1

    def scroll_up(self, n, top=None):
        top = self.top if top is None else top
        for _ in range(n):
            del self.grid[top]
            self.grid.insert(self.bot, self._blank_row())

    def scroll_down(self, n, top=None):
        top = self.top if top is None else top
        for _ in range(n):
            del self.grid[self.bot]
            self.grid.insert(top, self._blank_row())

    def feed(self, data):
        """Process text written by the program. Safe to call with partial sequences."""
        data = self.pending + data
        self.pending = ""
        self.dirty = True
        i, n = 0, len(data)
        while i < n:
            c = data[i]
            if c == "\x1b":
                j = self._escape(data, i)
                if j is None:  # incomplete: keep it for the next feed (unless it is junk)
                    if n - i < 256:
                        self.pending = data[i:]
                        return
                    j = i + 1
                i = j
                continue
            o = ord(c)
            if o >= 32 and o != 127:
                self.put(c)
            elif c == "\n" or c == "\x0b" or c == "\x0c":
                self.linefeed()
                self.wrap_next = False
            elif c == "\r":
                self.x, self.wrap_next = 0, False
            elif c == "\b":
                self.x, self.wrap_next = max(0, self.x - 1), False
            elif c == "\t":
                self.x = min(self.cols - 1, (self.x // 8 + 1) * 8)
            elif c == "\x0e":
                self.shift = 1
            elif c == "\x0f":
                self.shift = 0
            i += 1

    def _escape(self, data, i):
        """Handle the escape sequence at data[i]; return the index after it, or None if cut off."""
        if i + 1 >= len(data):
            return None
        nxt = data[i + 1]
        if nxt == "[":
            m = CSI_RE.match(data, i)
            if not m:
                rest = data[i + 2:i + 40]
                return None if re.fullmatch(r"[?>=!]?[0-9;:]*[ -/]*", rest) else i + 2
            self.csi(m.group(1), m.group(2), m.group(3), m.group(4))
            return m.end()
        if nxt == "]":
            m = LINUX_PAL_RE.match(data, i)
            if m:
                if m.group(1):
                    self.palette[int(m.group(1), 16)] = tuple(int(m.group(2)[k:k + 2], 16) for k in (0, 2, 4))
                else:
                    self.palette.clear()
                return m.end()
            m = OSC_RE.match(data, i)
            if not m:
                return None
            body = m.group(1)
            if body[:2] in ("0;", "2;"):
                self.title = body[2:]
            return m.end()
        if nxt in "P^_":  # device control strings: skip up to the terminator
            end = data.find("\x1b\\", i + 2)
            return None if end < 0 else end + 2
        m = ESC_RE.match(data, i)
        if not m:
            return i + 2
        if m.group(1):
            self.charsets[0 if m.group(1) == "(" else 1] = m.group(2)
        elif m.group(5):
            self.esc(m.group(5))
        return m.end()

    def esc(self, f):
        if f == "7":
            self.saved = (self.x, self.y, self.fg, self.bg, self.flags, self.origin)
        elif f == "8":
            self.x, self.y, self.fg, self.bg, self.flags, self.origin = self.saved
            self.wrap_next = False
        elif f == "D":
            self.linefeed()
        elif f == "E":
            self.x = 0
            self.linefeed()
        elif f == "M":  # reverse index
            if self.y == self.top:
                self.scroll_down(1)
            elif self.y > 0:
                self.y -= 1
        elif f == "c":
            self.reset()
        elif f == "=":
            self.app_keypad = True
        elif f == ">":
            self.app_keypad = False

    # ------------------------------------------------------------ CSI
    def sgr(self, ps):
        if not ps:
            ps = [0]
        i = 0
        while i < len(ps):
            v = ps[i]
            if v in (38, 48, 58) and i + 1 < len(ps):
                if ps[i + 1] == 5 and i + 2 < len(ps):
                    col, i = ps[i + 2] & 255, i + 3
                elif ps[i + 1] == 2 and i + 4 < len(ps):
                    col = RGB | (ps[i + 2] & 255) << 16 | (ps[i + 3] & 255) << 8 | (ps[i + 4] & 255)
                    i += 5
                else:
                    i += 2
                    continue
                if v == 38:
                    self.fg = col
                elif v == 48:
                    self.bg = col
                continue
            if v == 0:
                self.fg, self.bg, self.flags = DEFAULT, DEFAULT, 0
            elif v == 1:
                self.flags |= BOLD
            elif v == 2:
                self.flags |= DIM
            elif v == 4:
                self.flags |= UNDER
            elif v == 5:
                self.flags |= BLINK
            elif v == 7:
                self.flags |= REV
            elif v == 8:
                self.flags |= INVIS
            elif v == 22:
                self.flags &= ~(BOLD | DIM)
            elif v == 24:
                self.flags &= ~UNDER
            elif v == 25:
                self.flags &= ~BLINK
            elif v == 27:
                self.flags &= ~REV
            elif v == 28:
                self.flags &= ~INVIS
            elif 30 <= v <= 37:
                self.fg = v - 30
            elif v == 39:
                self.fg = DEFAULT
            elif 40 <= v <= 47:
                self.bg = v - 40
            elif v == 49:
                self.bg = DEFAULT
            elif 90 <= v <= 97:
                self.fg = v - 90 + 8
            elif 100 <= v <= 107:
                self.bg = v - 100 + 8
            i += 1

    def set_mode(self, priv, ps, on):
        for p in ps:
            if priv == "?":
                if p == 1:
                    self.app_cursor = on
                elif p == 6:
                    self.origin = on
                    self.x, self.y = 0, self.top if on else 0
                elif p == 7:
                    self.autowrap = on
                elif p == 25:
                    self.cursor_visible = on
                elif p in (1000, 1002, 1003):
                    self.mouse = p if on else 0
                elif p == 1006:
                    self.mouse_sgr = on
                elif p == 2004:
                    self.bracketed_paste = on
                elif p in (47, 1047, 1049):
                    if p == 1049 and on:
                        self.esc("7")
                    if on and self.alt_grid is None:
                        self.alt_grid, self.grid = self.grid, self._blank_grid()
                    elif not on and self.alt_grid is not None:
                        self.grid, self.alt_grid = self.alt_grid, None
                    if p == 1049 and not on:
                        self.esc("8")
            elif p == 4:
                self.insert_mode = on

    def csi(self, priv, p, inter, f):
        ps = [int(x) if x.isdigit() else 0 for x in p.replace(":", ";").split(";")] if p else []

        def a(k, d=1):
            return ps[k] if len(ps) > k and ps[k] else d

        if inter:  # e.g. CSI ? q, cursor style: not used
            return
        if f in "hl":
            self.set_mode(priv, ps, f == "h")
            return
        if priv == ">" or priv == "=":
            if f == "c":
                self.replies.append("\x1b[>0;10;1c")
            return
        if priv == "?":
            return
        self.wrap_next = False
        R, C = self.rows, self.cols
        if f == "m":
            self.sgr(ps)
        elif f in "Hf":
            off = self.top if self.origin else 0
            self.y = min(R - 1, a(0) - 1 + off)
            self.x = min(C - 1, a(1) - 1)
        elif f == "A":
            self.y = max(self.top if self.y >= self.top else 0, self.y - a(0))
        elif f in "Be":
            self.y = min(self.bot if self.y <= self.bot else R - 1, self.y + a(0))
        elif f in "Ca":
            self.x = min(C - 1, self.x + a(0))
        elif f == "D":
            self.x = max(0, self.x - a(0))
        elif f == "E":
            self.x, self.y = 0, min(R - 1, self.y + a(0))
        elif f == "F":
            self.x, self.y = 0, max(0, self.y - a(0))
        elif f in "G`":
            self.x = min(C - 1, a(0) - 1)
        elif f == "d":
            self.y = min(R - 1, a(0) - 1)
        elif f == "K":
            m, row, b = a(0, 0), self.grid[self.y], self._blank()
            lo, hi = (self.x, C) if m == 0 else (0, self.x + 1) if m == 1 else (0, C)
            row[lo:hi] = [b] * (hi - lo)
        elif f == "J":
            m, b = a(0, 0), self._blank()
            if m == 0:
                self.grid[self.y][self.x:] = [b] * (C - self.x)
                for r in range(self.y + 1, R):
                    self.grid[r] = self._blank_row()
            elif m == 1:
                self.grid[self.y][: self.x + 1] = [b] * (self.x + 1)
                for r in range(self.y):
                    self.grid[r] = self._blank_row()
            elif m in (2, 3):
                self.grid = [self._blank_row() for _ in range(R)]
        elif f == "X":
            n = min(a(0), C - self.x)
            self.grid[self.y][self.x:self.x + n] = [self._blank()] * n
        elif f == "b":
            for _ in range(min(a(0), 10000)):
                self.put(self.last)
        elif f == "@":
            row, n = self.grid[self.y], min(a(0), C - self.x)
            row[self.x:self.x] = [self._blank()] * n
            del row[C:]
        elif f == "P":
            row, n = self.grid[self.y], min(a(0), C - self.x)
            del row[self.x:self.x + n]
            row.extend([self._blank()] * n)
        elif f == "L":
            if self.top <= self.y <= self.bot:
                self.scroll_down(min(a(0), self.bot - self.y + 1), top=self.y)
        elif f == "M":
            if self.top <= self.y <= self.bot:
                self.scroll_up(min(a(0), self.bot - self.y + 1), top=self.y)
        elif f == "S":
            self.scroll_up(a(0))
        elif f == "T":
            self.scroll_down(a(0))
        elif f == "r":
            top, bot = a(0) - 1, a(1, R) - 1
            if 0 <= top < bot < R:
                self.top, self.bot = top, bot
                self.x, self.y = 0, (top if self.origin else 0)
        elif f == "s":
            self.esc("7")
        elif f == "u":
            self.esc("8")
        elif f == "n":
            if a(0, 0) == 6:
                y = self.y - (self.top if self.origin else 0)
                self.replies.append(f"\x1b[{y + 1};{self.x + 1}R")
            elif a(0, 0) == 5:
                self.replies.append("\x1b[0n")
        elif f == "c":
            self.replies.append("\x1b[?62;22c")
        elif f == "t":
            if a(0, 0) == 18:
                self.replies.append(f"\x1b[8;{R};{C}t")

    # ------------------------------------------------------------ input helpers
    def key(self, name):
        """The bytes a key sends, honouring application cursor mode. name: 'up', 'f1', ..."""
        ss3 = "\x1bO" if self.app_cursor else "\x1b["
        keys = {
            "up": ss3 + "A", "down": ss3 + "B", "right": ss3 + "C", "left": ss3 + "D",
            "home": ss3 + "H", "end": ss3 + "F",
            "insert": "\x1b[2~", "delete": "\x1b[3~", "pageup": "\x1b[5~", "pagedown": "\x1b[6~",
            "f1": "\x1bOP", "f2": "\x1bOQ", "f3": "\x1bOR", "f4": "\x1bOS",
            "f5": "\x1b[15~", "f6": "\x1b[17~", "f7": "\x1b[18~", "f8": "\x1b[19~",
            "f9": "\x1b[20~", "f10": "\x1b[21~", "f11": "\x1b[23~", "f12": "\x1b[24~",
            "enter": "\r", "tab": "\t", "backspace": "\x7f", "escape": "\x1b",
        }
        return keys.get(name, "")

    def mouse_report(self, button, col, row, press=True, motion=False):
        """What to send for a mouse event (button 0-2, 64/65 = wheel), or '' if not wanted."""
        if not self.mouse or (motion and self.mouse == 1000):
            return ""
        b = button + (32 if motion else 0)
        if self.mouse_sgr:
            return f"\x1b[<{b};{col + 1};{row + 1}{'M' if press else 'm'}"
        if not press:
            b = 3
        if col > 222 or row > 222:
            return ""
        return f"\x1b[M{chr(32 + b)}{chr(33 + col)}{chr(33 + row)}"
