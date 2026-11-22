#!/usr/bin/env python3
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
pontes - PCal (Pontes-Calos) multi-call program (like busybox).

  pontes menu  [args]      the RetroMenu home screen   (also: symlink named retromenu)
  pontes login [args]      the RetroLogin screen       (also: symlink named retrologin)
  pontes install [args]    the PCal installer           (also: symlink named pcal-install)
  pontes screen [args]     the graphical CRT screen      (also: symlink named pcal-screen)
  pontes --version

During development run the .py files directly; releases freeze this file
into a single program that doesn't need Python installed.
"""
import os
import sys

COMMANDS = {"menu": "retromenu", "login": "retrologin", "install": "retroinstall", "screen": "screen"}
ALIASES = {"retromenu": "menu", "retrologin": "login", "pcal-install": "install", "retroinstall": "install",
           "pcal-screen": "screen"}


def main():
    name = os.path.basename(sys.argv[0])
    if name.endswith(".py"):
        name = name[:-3]
    args = sys.argv[1:]
    cmd = ALIASES.get(name)
    if cmd is None:  # called as `pontes ...`
        cmd = args.pop(0) if args else "menu"
    if cmd in ("-V", "--version", "version"):
        import retromenu
        print(f"PCal {retromenu.VERSION} (Pontes-Calos)")
        return 0
    if cmd in ("-h", "--help", "help") or cmd not in COMMANDS:
        print(__doc__.strip())
        return 0 if cmd in ("-h", "--help", "help") else 2
    sys.argv = [f"pontes-{cmd}"] + args
    if cmd == "menu":
        import retromenu
        return retromenu.entry()
    if cmd == "login":
        import retrologin
        return retrologin.entry()
    if cmd == "screen":
        import screen
        return screen.entry(args)
    import retroinstall
    return retroinstall.entry()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
