#!/usr/bin/env python3
# Copyright (C) 2026 bababoy06 and Pontes-Calos contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""
pontes - Pontes-Calos multi-call program (like busybox).

  pontes menu  [args]      the RetroMenu home screen   (also: symlink named retromenu)
  pontes login [args]      the RetroLogin screen       (also: symlink named retrologin)
  pontes --version

During development run the .py files directly; releases freeze this file
into a single program that doesn't need Python installed.
"""
import os
import sys

COMMANDS = {"menu": "retromenu", "login": "retrologin"}
ALIASES = {"retromenu": "menu", "retrologin": "login"}


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
        print(f"pontes-calos {retromenu.VERSION}")
        return 0
    if cmd in ("-h", "--help", "help") or cmd not in COMMANDS:
        print(__doc__.strip())
        return 0 if cmd in ("-h", "--help", "help") else 2
    sys.argv = [f"pontes-{cmd}"] + args
    if cmd == "menu":
        import retromenu
        return retromenu.entry()
    import retrologin
    return retrologin.entry()


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    sys.exit(main())
