#!/usr/bin/env python3
# Copyright (C) 2026 bababoy06 and Pontes-Calos contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Smoke test: start Pontes-Calos in a pseudo-terminal, press keys, check the screen.
Works for the source and for a built binary:

  python3 tests/smoke_test.py python3 retromenu/pontes.py
  python3 tests/smoke_test.py dist/onedir/pontes/pontes
  python3 tests/smoke_test.py dist/release/pontes-linux-x86_64-musl
"""
import os
import pty
import select
import shutil
import struct
import sys
import tempfile
import time

try:
    import fcntl
    import termios
except ImportError:  # not a Unix system
    sys.exit("needs Linux")

UP, ENTER, ESC = "\x1bOA", "\r", "\x1b"


class Session:
    def __init__(self, cmd, home):
        env = dict(os.environ, HOME=home, XDG_CONFIG_HOME=os.path.join(home, ".config"),
                   TERM="xterm-256color", LANG="C.UTF-8", LC_ALL="C.UTF-8", SHELL="/bin/sh")
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.chdir(home)
            os.execvpe(cmd[0], cmd, env)
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 90, 0, 0))
        self.out = b""

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            r, _, _ = select.select([self.fd], [], [], 0.05)
            if r:
                try:
                    data = os.read(self.fd, 65536)
                except OSError:
                    return
                if not data:
                    return
                self.out += data

    def send(self, keys, wait=0.4):
        os.write(self.fd, keys.encode())
        self.pump(wait)

    def expect(self, text, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            if text.encode() in self.out:
                return True
            self.pump(0.2)
        return False

    def exited(self, timeout=5):
        end = time.time() + timeout
        while time.time() < end:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
            if pid:
                return os.waitstatus_to_exitcode(status)
            self.pump(0.1)
        return None

    def kill(self):
        try:
            os.kill(self.pid, 9)
            os.waitpid(self.pid, 0)
        except OSError:
            pass


def main():
    # make file arguments absolute: the test runs the program from a temporary home folder
    base = [os.path.abspath(a) if os.path.exists(a) else a for a in sys.argv[1:]]
    if not base:
        sys.exit(__doc__)
    results = []

    def check(name, ok):
        results.append(ok)
        print(("PASS " if ok else "FAIL ") + name, flush=True)

    home = tempfile.mkdtemp(prefix="pontes-smoke-")
    os.makedirs(os.path.join(home, "docs"))
    try:
        # --- version
        s = Session(base + ["--version"], home)
        check("--version prints the version", s.expect("pontes-calos ") and s.exited() == 0)

        # --- busybox-style links: a link named retrologin must start the login screen
        if len(base) == 1:
            link = os.path.join(home, "retrologin")
            os.symlink(base[0], link)
            s = Session([link, "--help"], home)
            check("link named retrologin runs the login program", s.expect("RetroLogin") and s.exited() == 0)

        # --- menu
        s = Session(base + ["menu"], home)
        check("menu: main menu appears", s.expect("MAIN MENU", 8))
        s.send(ENTER, 0.8)  # Files is the first item
        check("menu: file manager opens", s.expect("FILES ::"))
        check("menu: lists the home folder", s.expect("docs/"))
        s.send("q", 0.6)
        s.send("i")
        s.send("echo SMOKE-$((6*7))" + ENTER, 1.5)
        check("menu: command line runs a command", s.expect("SMOKE-42"))
        s.send(ESC, 0.5)
        s.send(UP, 0.4)  # wraps to the last item: Log out
        s.send(ENTER, 0.5)
        check("menu: Log out exits cleanly", s.exited() == 0)
        s.kill()

        # --- login screen (no real login: --login-cmd points at a harmless program)
        s = Session(base + ["login", "--user", "nosuchuser", "--login-cmd", "/bin/true"], home)
        check("login: login screen appears", s.expect("SYSTEM LOGIN", 8))
        s.send("wrong-password" + ENTER, 3.5)
        check("login: wrong password is refused", s.expect("ACCESS DENIED", 4))
        s.kill()
    finally:
        shutil.rmtree(home, ignore_errors=True)

    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
