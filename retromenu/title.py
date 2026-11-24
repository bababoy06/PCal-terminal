# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The big title on the menu, the boot splash and the login screen.

Two levels, like the rest of a shared computer:
  system title   set by an admin, in /etc/pcal/config.json: the login screen always shows
                 it, and it is everyone's title until they choose their own
  my title       each user's own, in ~/.config/retromenu/config.json: their menu and splash

Change them in Settings > Title, or with:
  pontes title                      show both
  pontes title --system NAME        set the system title (as root, e.g. with doas)
  pontes title --system --reset     back to PONTES-CALOS

The line under the title, with the PCal version, never changes. The letters are a small
figlet-style font in plain ASCII, 4 rows high, so they work on every screen.
"""
import json
import os
import sys

DEFAULT = "PONTES-CALOS"
SYSTEM_CONFIG = "/etc/pcal/config.json"
MAX_LEN = 16
TAGLINE = "PCAL {version} -- A PONTES-CALOS SYSTEM"

FONT = {
    "A": ["   _   ", "  /_\\  ", " / _ \\ ", "/_/ \\_\\"],
    "B": [" ___ ", "| _ )", "| _ \\", "|___/"],
    "C": ["  ___ ", " / __|", "| (__ ", " \\___|"],
    "D": [" ___  ", "|   \\ ", "| |) |", "|___/ "],
    "E": [" ___ ", "| __|", "| _| ", "|___|"],
    "F": [" ___ ", "| __|", "| _| ", "|_|  "],
    "G": ["  ___ ", " / __|", "| (_ |", " \\___|"],
    "H": [" _  _ ", "| || |", "| __ |", "|_||_|"],
    "I": [" ___ ", "|_ _|", " | | ", "|___|"],
    "J": ["    _ ", " _ | |", "| || |", " \\__/ "],
    "K": [" _  __", "| |/ /", "| ' < ", "|_|\\_\\"],
    "L": [" _    ", "| |   ", "| |__ ", "|____|"],
    "M": [" __  __ ", "|  \\/  |", "| |\\/| |", "|_|  |_|"],
    "N": [" _  _ ", "| \\| |", "| .` |", "|_|\\_|"],
    "O": ["  ___  ", " / _ \\ ", "| (_) |", " \\___/ "],
    "P": [" ___ ", "| _ \\", "|  _/", "|_|  "],
    "Q": ["  ___  ", " / _ \\ ", "| (_) |", " \\__\\_\\"],
    "R": [" ___ ", "| _ \\", "|   /", "|_|_\\"],
    "S": [" ___ ", "/ __|", "\\__ \\", "|___/"],
    "T": [" _____ ", "|_   _|", "  | |  ", "  |_|  "],
    "U": [" _   _ ", "| | | |", "| |_| |", " \\___/ "],
    "V": ["__   __", "\\ \\ / /", " \\ V / ", "  \\_/  "],
    "W": ["__      __", "\\ \\    / /", " \\ \\/\\/ / ", "  \\_/\\_/  "],
    "X": ["__  __", "\\ \\/ /", " >  < ", "/_/\\_\\"],
    "Y": ["__   __", "\\ \\ / /", " \\ V / ", "  |_|  "],
    "Z": [" ____", "|_  /", " / / ", "/___|"],
    "0": ["  __  ", " /  \\ ", "| () |", " \\__/ "],
    "1": [" _ ", "/ |", "| |", "|_|"],
    "2": [" ___ ", "|_  )", " / / ", "/___|"],
    "3": [" ____", "|__ /", " |_ \\", "|___/"],
    "4": [" _ _  ", "| | | ", "|_  _|", "  |_| "],
    "5": [" ___ ", "| __|", "|__ \\", "|___/"],
    "6": ["  __ ", " / / ", "/ _ \\", "\\___/"],
    "7": [" ____ ", "|__  |", "  / / ", " /_/  "],
    "8": [" ___ ", "( _ )", "/ _ \\", "\\___/"],
    "9": [" ___ ", "/ _ \\", "\\_, /", " /_/ "],
    "-": ["    ", " __ ", "|__|", "    "],
    ".": ["   ", "   ", " _ ", "(_)"],
    "!": [" _ ", "| |", "|_|", "(_)"],
    "/": ["   __", "  / /", " / / ", "/_/  "],
    " ": ["  ", "  ", "  ", "  "],
}


def clean(text):
    """What can be shown: capitals, only characters the font has, at most MAX_LEN."""
    text = "".join(c for c in (text or "").upper() if c in FONT).strip()[:MAX_LEN].strip()
    return text or DEFAULT


def render(text):
    """The title as 4 lines of ASCII art."""
    text = clean(text)
    return ["".join(FONT[c][row] for c in text) for row in range(4)]


def tagline(version):
    return TAGLINE.format(version=version)


def _read(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def system_title():
    """The admin's title (the login screen's, and everyone's default)."""
    return clean(_read(SYSTEM_CONFIG).get("title"))


def user_title(cfg):
    """What a user's menu shows: their own title, or else the system title."""
    own = (cfg or {}).get("title")
    return clean(own) if own else system_title()


def set_system_title(name):
    """Write the system title (root only). None or the default removes it."""
    data = _read(SYSTEM_CONFIG)
    name = clean(name) if name else DEFAULT
    if name == DEFAULT:
        data.pop("title", None)
    else:
        data["title"] = name
    os.makedirs(os.path.dirname(SYSTEM_CONFIG), exist_ok=True)
    tmp = SYSTEM_CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o644)
    os.replace(tmp, SYSTEM_CONFIG)
    return name


def entry(argv=None):
    """pontes title [--system NAME | --system --reset]"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        own = _read(os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
                                 "retromenu", "config.json")).get("title")
        print(f"system title: {system_title()}")
        print(f"my title:     {clean(own) if own else '(the system title)'}")
        return 0
    if args[0] != "--system" or len(args) < 2:
        print(__doc__.strip())
        return 2
    if os.geteuid() != 0:
        print("only root can change the system title: try  doas pontes title --system ...", file=sys.stderr)
        return 1
    name = None if args[1] == "--reset" else " ".join(args[1:])
    print(f"system title: {set_system_title(name)}")
    return 0
