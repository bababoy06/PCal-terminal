# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
The big title on the menu, the boot splash and the login screen.

The text can be changed in Settings > Title (letters, digits, space and - . ! /);
the line under it, with the PCal version, stays the same. The letters are a small
figlet-style font in plain ASCII, 4 rows high, so they work on every screen.
"""
DEFAULT = "PONTES-CALOS"
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
