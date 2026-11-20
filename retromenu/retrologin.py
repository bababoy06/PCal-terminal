#!/usr/bin/env python3
"""
RetroLogin - the green-phosphor login screen of Pontes-Calos.

Runs as root on tty1 (started by init), asks for a user name and password,
checks them against /etc/shadow and then hands over to `login -f USER`,
which sets up the session and starts the user's shell (and from there, RetroMenu).
When the user logs out, init starts this screen again.

  retrologin [--user NAME] [--login-cmd /bin/login]

Keys: TAB / ENTER / DOWN next field, UP previous field, ESC clear.
"""
import curses
import hmac
import json
import locale
import os
import pwd
import socket
import sys
import time
import warnings

SYSTEM_NAME = "PONTES-CALOS"
TAGLINE = "A PONTES-CALOS SYSTEM"
COPYRIGHT = "(C) PONTES-CALOS DATA SYSTEMS"
SHADOW = "/etc/shadow"
DELAY_AFTER_FAIL = 2  # seconds, every wrong password
LOCK_AFTER = 5  # wrong passwords in a row ...
LOCK_SECONDS = 30  # ... lock the screen this long

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
}
BANNER = ["".join(FONT[c][row] for c in SYSTEM_NAME) for row in range(4)]

THEMES = {  # 8-colour base, 256-colour (normal, bright, dim), Linux console RGB
    "green": (curses.COLOR_GREEN, (77, 120, 28), ("020a04", "33ff66", "b4ffc8", "1d6b34")),
    "amber": (curses.COLOR_YELLOW, (214, 222, 130), ("0a0600", "ffb000", "ffd98a", "7a5000")),
    "white": (curses.COLOR_WHITE, (250, 231, 243), ("080808", "d0d0d0", "ffffff", "6a6a6a")),
    "cyan": (curses.COLOR_CYAN, (80, 159, 30), ("000a0c", "33e0ff", "c0f6ff", "1a6070")),
}


# ------------------------------------------------------------- password check
def _crypt_function():
    """crypt(3) without the `crypt` module (it was removed in Python 3.13)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            import crypt as _crypt  # Python <= 3.12
            return _crypt.crypt
        except ImportError:
            pass
    import ctypes
    import ctypes.util
    for name in (None, ctypes.util.find_library("crypt"), "libcrypt.so.1", "libcrypt.so.2"):
        try:
            fn = ctypes.CDLL(name).crypt  # musl has crypt() in libc itself
        except (OSError, AttributeError):
            continue
        fn.restype = ctypes.c_char_p
        fn.argtypes = (ctypes.c_char_p, ctypes.c_char_p)

        def crypt(password, salt, fn=fn):
            out = fn(password.encode(), salt.encode())
            return out.decode() if out else ""
        return crypt
    return None


CRYPT = _crypt_function()
# Used for unknown users, so a wrong name takes as long as a wrong password.
DUMMY_HASH = "$6$pontescalos$" + "x" * 86


def shadow_hash(user):
    try:
        with open(SHADOW) as f:
            for line in f:
                parts = line.rstrip("\n").split(":")
                if parts[0] == user and len(parts) > 1:
                    return parts[1]
    except OSError:
        pass
    return None


def check_password(user, password):
    """True only for an existing, unlocked account with a password that matches."""
    if CRYPT is None:
        return False
    try:
        pwd.getpwnam(user)
        hashed = shadow_hash(user)
    except KeyError:
        hashed = None
    if not hashed or hashed[0] in "!*":  # unknown, locked, or no password at all
        CRYPT(password, DUMMY_HASH)
        return False
    result = CRYPT(password, hashed)
    return bool(result) and hmac.compare_digest(result, hashed)


# ------------------------------------------------------------------ screen
def put(win, y, x, text, attr=0):
    h, w = win.getmaxyx()
    if y < 0 or y >= h or x >= w:
        return
    if x < 0:
        text, x = text[-x:], 0
    try:
        win.addstr(y, x, text[: max(0, w - x)], attr)
    except curses.error:
        pass


class LoginScreen:
    def __init__(self, scr, default_user, theme, effects="subtle"):
        self.scr = scr
        self.user = default_user
        self.password = ""
        self.field = 1 if default_user else 0
        self.message = ""
        self.fails = 0
        self.locked_until = 0
        self.fx = effects if effects in ("off", "subtle", "full") else "subtle"
        self.host = socket.gethostname().split(".")[0]
        try:
            self.tty = os.path.basename(os.ttyname(0))
        except OSError:
            self.tty = "console"
        enc = locale.nl_langinfo(locale.CODESET).upper().replace("-", "")
        u = enc == "UTF8"
        self.B = dict(tl="╔" if u else "+", tr="╗" if u else "+", bl="╚" if u else "+",
                      br="╝" if u else "+", h="═" if u else "=", v="║" if u else "|",
                      lt="╠" if u else "+", rt="╣" if u else "+", block="█" if u else "#",
                      shade="░" if u else ".", dot="•" if u else "*")
        curses.raw()
        scr.keypad(True)
        self.set_theme(theme)

    def set_theme(self, theme):
        base, rich, rgb = THEMES.get(theme, THEMES["green"])
        if os.environ.get("TERM") == "linux":  # repaint the console like RetroMenu does
            bg, normal, bright, dim = rgb
            seq = f"\033]P0{bg}\033]P{base:X}{normal}\033]P{base + 8:X}{bright}\033]P8{dim}"
            try:
                os.write(sys.stdout.fileno(), seq.encode())
            except OSError:
                pass
        if curses.has_colors():
            curses.start_color()
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
            self.dim = curses.color_pair(3)
            self.sel = curses.color_pair(4) | curses.A_BOLD
        else:
            self.norm, self.bold = curses.A_NORMAL, curses.A_BOLD
            self.dim, self.sel = curses.A_DIM, curses.A_REVERSE
        self.scr.bkgd(" ", self.norm)

    def draw(self, busy=None, refresh=True):
        H, W = self.scr.getmaxyx()
        B = self.B
        self.scr.erase()
        bw = len(BANNER[0])
        y = max(0, H // 2 - 10)
        if W >= bw + 2 and H >= 20:
            x = (W - bw) // 2
            for i, line in enumerate(BANNER):
                put(self.scr, y + i, x, line, self.bold)
            put(self.scr, y + 5, x + bw - len(TAGLINE), TAGLINE, self.norm)
            y += 7
        else:
            put(self.scr, y, max(0, (W - len(SYSTEM_NAME)) // 2), SYSTEM_NAME, self.bold)
            y += 2

        w = min(W, 50)
        iw = w - 2
        bx = (W - w) // 2
        title = "SYSTEM LOGIN"
        put(self.scr, y, bx, B["tl"] + B["h"] * iw + B["tr"], self.norm)
        put(self.scr, y + 1, bx, B["v"], self.norm)
        put(self.scr, y + 1, bx + 1, title.center(iw), self.bold)
        put(self.scr, y + 1, bx + w - 1, B["v"], self.norm)
        put(self.scr, y + 2, bx, B["lt"] + B["h"] * iw + B["rt"], self.norm)
        fw = iw - 14
        values = [self.user, B["dot"] * len(self.password)]
        for i, label in enumerate(("USER", "PASSWORD")):
            row = y + 3 + i
            put(self.scr, row, bx, B["v"] + " " * iw + B["v"], self.norm)
            focused = i == self.field and not busy
            put(self.scr, row, bx + 2, ("> " if focused else "  ") + f"{label:<9}: ",
                self.bold if focused else self.norm)
            text = values[i][-(fw - 1):]
            put(self.scr, row, bx + 15, text, self.bold)
            if focused:
                put(self.scr, row, bx + 15 + len(text), " ", self.sel)
        put(self.scr, y + 5, bx, B["v"] + " " * iw + B["v"], self.norm)
        put(self.scr, y + 6, bx, B["bl"] + B["h"] * iw + B["br"], self.norm)
        put(self.scr, y + 6, bx + 2, " AUTHORIZED ACCESS ONLY ", self.dim)

        msg = busy or self.message
        if self.locked_until > time.time():
            msg = f"TOO MANY ATTEMPTS -- LOCKED FOR {int(self.locked_until - time.time()) + 1}s"
        if msg:
            put(self.scr, y + 8, max(0, (W - len(msg)) // 2), msg, self.bold)
        if H >= 20:
            put(self.scr, H - 2, max(0, (W - len(COPYRIGHT)) // 2), COPYRIGHT, self.dim)
        info = f" {self.host}  {self.tty}   TAB next field  ENTER login  ESC clear   Alt+F2 other console"
        clock = "  " + time.strftime("%Y-%m-%d %H:%M:%S ")
        room = max(0, W - len(clock))
        put(self.scr, H - 1, 0, (info[:room].ljust(room) + clock)[:W], self.sel)
        if refresh:
            self.scr.refresh()

    # ---- CRT effects (same as RetroMenu; follows the user's Settings > Effects)
    def crt(self, on=True, busy=None):
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
        for kind, v in frames:
            if kind == "band":
                self.draw(busy=busy, refresh=False)
                for r in range(H):
                    if abs(r - mid) > v:
                        put(self.scr, r, 0, " " * W, self.norm)
                for r in (mid - v, mid + v):
                    if 0 <= r < H and v < mid:
                        put(self.scr, r, 0, self.B["h"] * W, self.bold)
            else:
                self.scr.erase()
                if kind == "line":
                    put(self.scr, mid, (W - v) // 2, self.B["h"] * v, self.bold)
                else:
                    put(self.scr, mid, W // 2, self.B["block"], self.bold)
            self.scr.refresh()
            time.sleep(0.025 if kind == "line" else 0.03)
        if not on:
            time.sleep(0.08)
            self.scr.erase()
            self.scr.refresh()

    def say(self, msg):
        """Show a status message; with full effects it is typed out."""
        if self.fx == "full":
            for i in range(0, len(msg), 2):
                self.message = msg[:i] + self.B["block"]
                self.draw()
                time.sleep(0.012)
        self.message = msg

    def progress(self, label, seconds):
        steps = max(1, int(seconds / 0.05))
        H, W = self.scr.getmaxyx()
        bar_w = min(30, W - 4)
        for s in range(steps + 1):
            self.draw(busy=label)
            filled = bar_w * s // steps
            y = max(0, H // 2 - 10) + (7 if H >= 20 and W >= len(BANNER[0]) + 2 else 2) + 10
            x = (W - bar_w) // 2
            put(self.scr, y, x, self.B["block"] * filled, self.bold)
            put(self.scr, y, x + filled, self.B["shade"] * (bar_w - filled), self.dim)
            self.scr.refresh()
            time.sleep(seconds / steps)

    def run(self):
        """Returns the user name once the right password was given."""
        self.crt(on=True)
        while True:
            self.draw()
            self.scr.timeout(1000)
            try:
                k = self.scr.get_wch()
            except curses.error:
                continue  # timeout: redraw the clock / lock countdown
            finally:
                self.scr.timeout(-1)
            if self.locked_until > time.time():
                continue
            if isinstance(k, str) and k.isprintable():
                if self.field == 0:
                    if len(self.user) < 32:
                        self.user += k
                elif len(self.password) < 256:
                    self.password += k
                self.message = ""
                continue
            code = ord(k) if isinstance(k, str) else k
            if code in (curses.KEY_BACKSPACE, 127, 8):
                if self.field == 0:
                    self.user = self.user[:-1]
                else:
                    self.password = self.password[:-1]
            elif code == 21:  # Ctrl-U
                if self.field == 0:
                    self.user = ""
                else:
                    self.password = ""
            elif code == 27:  # ESC clears everything
                self.user, self.password, self.field, self.message = "", "", 0, ""
            elif code in (9, curses.KEY_DOWN) or (code in (10, 13, curses.KEY_ENTER) and self.field == 0):
                if self.user:
                    self.field = 1
            elif code in (curses.KEY_UP, curses.KEY_BTAB):
                self.field = 0
            elif code in (10, 13, curses.KEY_ENTER):
                user, password = self.user.strip(), self.password
                self.password = ""
                self.draw(busy="VERIFYING...")
                if user and check_password(user, password):
                    self.fails = 0
                    self.progress("ACCESS GRANTED", 0.4)
                    self.crt(on=False, busy="ACCESS GRANTED")
                    return user
                self.fails += 1
                self.progress("VERIFYING...", DELAY_AFTER_FAIL)
                if CRYPT is None:
                    self.say("ERROR: NO crypt() ON THIS SYSTEM -- USE Alt+F2")
                else:
                    self.say(f"ACCESS DENIED ({self.fails})")
                if self.fails % LOCK_AFTER == 0:
                    self.locked_until = time.time() + LOCK_SECONDS


def user_settings(user):
    """Use the theme and effects the user picked in RetroMenu, so login and menu match."""
    try:
        home = pwd.getpwnam(user).pw_dir
        with open(os.path.join(home, ".config", "retromenu", "config.json")) as f:
            cfg = json.load(f)
        return cfg.get("theme", "green"), cfg.get("effects", "subtle")
    except (KeyError, OSError, ValueError, AttributeError):
        return "green", "subtle"


def main():
    args = sys.argv[1:]
    default_user, login_cmd = "", "/bin/login"
    while args:
        a = args.pop(0)
        if a == "--user" and args:
            default_user = args.pop(0)
        elif a == "--login-cmd" and args:
            login_cmd = args.pop(0)
        elif a in ("-h", "--help"):
            print(__doc__.strip())
            return 0
    if os.geteuid() != 0 and login_cmd == "/bin/login":
        print("retrologin must run as root (it is started by init on tty1).", file=sys.stderr)
        return 1
    if not os.environ.get("LANG"):
        os.environ["LANG"] = "C.UTF-8"  # init gives us no locale; boxes need UTF-8
    os.environ.setdefault("ESCDELAY", "25")
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        locale.setlocale(locale.LC_ALL, "C")

    theme, effects = user_settings(default_user)
    user = curses.wrapper(lambda scr: LoginScreen(scr, default_user, theme, effects).run())
    # Hand over: login -f skips its own password prompt (we already checked) and
    # sets up the session, utmp, HOME and the user's shell.
    os.execv(login_cmd, [os.path.basename(login_cmd), "-f", user])


def entry():
    """Start the login screen (used by `python3 retrologin.py` and by the pontes binary)."""
    try:
        return main()
    except KeyboardInterrupt:
        return 1
    except Exception as e:  # never leave anything usable behind; init restarts us
        print(f"retrologin: {e}", file=sys.stderr)
        time.sleep(3)
        return 1


if __name__ == "__main__":
    sys.exit(entry())
