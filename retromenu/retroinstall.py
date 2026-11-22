#!/usr/bin/env python3
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
"""
retroinstall - the PCal installer  (pcal-install, or: pontes install)

Runs on the PCal live ISO as root. It asks the questions of Alpine's own
setup-alpine in PCal's style, then installs with Alpine's own tools, in the
same order setup-alpine uses:

  1. setup-alpine -e -f ANSWERS    keyboard, hostname, network, DNS, timezone,
                                   proxy, NTP, package mirrors, SSH
                                   (user and disk are done in the next steps)
  2. adduser / addgroup / chpasswd the user account and passwords (as setup-user)
  3. setup-disk -m sys DISK        what setup-alpine runs last
  4. chroot NEW-SYSTEM pcal-setup  PCal login screen and menu on the new system

Every command runs visibly in the terminal and is logged to /tmp/pcal-install.log.
If Alpine asks something extra, you can answer it right there.

  pcal-install            install (live ISO, as root)
  pcal-install --dry-run  go through every screen, then only print what would run
                          (works anywhere, without root; nothing is changed)
"""
import curses
import locale
import os
import re
import secrets
import shlex
import string
import subprocess
import sys
import time

import retromenu as rm
import retrologin

LOG = "/tmp/pcal-install.log"
ANSWERS = "/tmp/pcal-answers"
TARGET = "/mnt/pcal-target"
BKEYMAPS = "/usr/share/bkeymaps"

# Shown first; everything Alpine has is under "Other layout..."
KEYMAPS = [
    ("be", "be", "Belgian (AZERTY)"),
    ("us", "us", "US English (QWERTY)"),
    ("gb", "gb", "UK English (QWERTY)"),
    ("fr", "fr", "French (AZERTY)"),
    ("nl", "nl", "Dutch"),
    ("de", "de", "German (QWERTZ)"),
    ("es", "es", "Spanish"),
    ("it", "it", "Italian"),
]
TIMEZONES = [
    "Europe/Brussels", "Europe/Amsterdam", "Europe/Paris", "Europe/Luxembourg", "Europe/Berlin",
    "Europe/London", "UTC", "America/New_York", "America/Los_Angeles", "Asia/Tokyo",
]
USERNAME_RE = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?$")
RESERVED_USERS = {"root", "daemon", "bin", "sys", "adm", "nobody", "wheel", "users"}


# --------------------------------------------------------------- system facts
def read(path, default=""):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return default


def is_live():
    """The live ISO runs from RAM: its root filesystem is tmpfs."""
    root = ""
    for line in read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) > 2 and parts[1] == "/":
            root = parts[2]
    return root == "tmpfs"


def parent_disk(dev):
    """'sdb1' -> 'sdb', 'nvme0n1p2' -> 'nvme0n1'."""
    path = os.path.realpath(f"/sys/class/block/{dev}")
    if os.path.exists(os.path.join(path, "partition")):
        return os.path.basename(os.path.dirname(path))
    return dev


def boot_disks():
    """Disks the live system is running from (the USB stick / CD): never offer those."""
    used = set()
    for line in read("/proc/mounts").splitlines():
        parts = line.split()
        if len(parts) > 1 and parts[0].startswith("/dev/") and (
                parts[1].startswith("/media/") or parts[1] in ("/", "/boot")):
            used.add(parent_disk(os.path.basename(parts[0])))
    return used


def list_disks():
    """[(name, bytes, description)] of disks PCal can be installed on."""
    skip = re.compile(r"^(loop|ram|zram|sr|fd|dm-|md|nbd)")
    boot = boot_disks()
    disks = []
    try:
        names = sorted(os.listdir("/sys/block"))
    except OSError:
        return disks
    for name in names:
        if skip.match(name) or name in boot:
            continue
        try:
            size = int(read(f"/sys/block/{name}/size", "0")) * 512
        except ValueError:
            continue
        if size < 2 * 1024 ** 3:  # too small for PCal
            continue
        model = " ".join(filter(None, (read(f"/sys/block/{name}/device/vendor"),
                                       read(f"/sys/block/{name}/device/model"))))
        if read(f"/sys/block/{name}/removable") == "1":
            model = (model + " (removable)").strip()
        disks.append((name, size, model or "disk"))
    return disks


def list_interfaces():
    """[(name, kind)] of network interfaces, wired first."""
    out = []
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        return out
    for name in names:
        # only real network cards (or a VM's): virtual helpers like ifb0, veth, docker0 have no device
        if name == "lo" or not os.path.exists(f"/sys/class/net/{name}/device"):
            continue
        kind = "Wi-Fi" if os.path.isdir(f"/sys/class/net/{name}/wireless") else "wired"
        out.append((name, kind))
    return sorted(out, key=lambda i: i[1] != "wired")


def keymap_layouts():
    try:
        return sorted(d for d in os.listdir(BKEYMAPS) if os.path.isdir(os.path.join(BKEYMAPS, d)))
    except OSError:
        return []


def keymap_variants(layout):
    try:
        return sorted(f[: -len(".bmap.gz")] for f in os.listdir(os.path.join(BKEYMAPS, layout))
                      if f.endswith(".bmap.gz"))
    except OSError:
        return []


def sha512_hash(password):
    """The classic $6$ format: Alpine's console login, SSH and RetroLogin all check it."""
    salt = "".join(secrets.choice(string.ascii_letters + string.digits + "./") for _ in range(16))
    for crypt in retrologin.CRYPT_BACKENDS:
        out = crypt(password, "$6$" + salt)
        if out.startswith("$6$" + salt + "$"):
            return out
    return None


# ------------------------------------------------------------------ installer
class Installer:
    STEPS = ["KEYBOARD", "COMPUTER NAME", "TIME ZONE", "NETWORK", "SSH", "USER", "ROOT", "DISK", "SUMMARY"]

    def __init__(self, scr, dry_run):
        self.app = rm.App(scr)
        self.scr = scr
        self.dry = dry_run
        self.a = {  # the answers
            "keymap": ("be", "be", "Belgian (AZERTY)"),
            "hostname": "pcal",
            "timezone": "Europe/Brussels",
            "iface": None,
            "ssh": False,
            "user": "",
            "user_hash": None,
            "root_hash": None,
            "disk": None,
        }

    # ---- drawing
    def frame(self, step, title, n, row_at, sel, top, hints, headers=None, widths=(), note=None):
        """One installer screen: banner, step line, a boxed table, a note, the status bar."""
        app, scr = self.app, self.scr
        H, W = scr.getmaxyx()
        scr.erase()
        y = app.draw_header(H, W, info=False)
        idx = self.STEPS.index(step) + 1 if step in self.STEPS else 0
        line = f"PCAL INSTALLER  ::  STEP {idx} OF {len(self.STEPS)}  ::  {step}" if idx else "PCAL INSTALLER"
        if self.dry:
            line += "  ::  DRY RUN"
        rm.put(scr, y, max(0, (W - len(line)) // 2), line, app.bold)
        y += 2
        tw = min(W - 2, 72)
        tx = max(0, (W - tw) // 2)
        over = app.table_overhead(title, headers)
        th = max(over + 1, min(H - y - 4 - (2 if note else 0), n + over))
        body = th - over
        _, top = rm.clamp_scroll(sel, top, body, n)
        app.draw_table(scr, y, tx, th, tw, n, row_at, sel, top, title=title, headers=headers,
                       widths=widths, pointer=sel >= 0, glow_key="install:" + title)
        if note:
            for i, text in enumerate(note.splitlines()[:2]):
                rm.put(scr, y + th + 1 + i, tx + 1, text[: tw - 2], app.dim)
        app.status(H, W, hints)
        scr.noutrefresh()
        return top

    def choose(self, step, title, options, default=0, note=None, headers=None, widths=()):
        """A list to pick from. options: [(cells, value)]. Returns the value, or None for back."""
        app = self.app
        sel, top = max(0, min(default, len(options) - 1)), 0
        while True:
            def draw():
                nonlocal top
                top = self.frame(step, title, len(options), lambda i: (options[i][0], None), sel, top,
                                 "UP/DN move  ENTER/RIGHT choose  LEFT/ESC back", headers, widths, note)
            app.bg = draw
            draw()
            curses.doupdate()
            self.scr.timeout(app.anim_timeout(-1))
            k = self.scr.getch()
            self.scr.timeout(-1)
            if k in rm.K_UP:
                sel = (sel - 1) % len(options)
            elif k in rm.K_DOWN:
                sel = (sel + 1) % len(options)
            elif k == curses.KEY_PPAGE:
                sel = max(0, sel - 10)
            elif k == curses.KEY_NPAGE:
                sel = min(len(options) - 1, sel + 10)
            elif k in rm.K_ENTER or k in rm.K_FWD:
                return options[sel][1]
            elif k in rm.K_BACK:
                return None

    def message(self, step, title, lines, hints="ENTER continue  LEFT/ESC back"):
        """A text card. Returns True for ENTER/RIGHT, False for LEFT/ESC."""
        while True:
            def draw():
                self.frame(step, title, len(lines), lambda i: ((lines[i],), None), -1, 0, hints)
            self.app.bg = draw
            draw()
            curses.doupdate()
            k = self.scr.getch()
            if k in rm.K_ENTER or k in rm.K_FWD:
                return True
            if k in rm.K_BACK:
                return False

    def ask(self, step, label, initial="", secret=False, info=()):
        """Text input on top of the current step's screen, with a short explanation above it."""
        lines = list(info) or [""]

        def draw():
            self.frame(step, step, len(lines), lambda i: ((lines[i],), None), -1, 0, "ENTER ok  ESC back")
        self.app.bg = draw
        return self.app.prompt(label, initial, secret=secret)

    def error(self, step, text):
        self.message(step, "PLEASE CHECK", [text], hints="ENTER/ESC ok")

    # ---- the steps (each returns True = next, False = back)
    def step_keyboard(self):
        current = self.a["keymap"]
        options = [((f"{label}", f"{lay}/{var}"), (lay, var, label)) for lay, var, label in KEYMAPS]
        if keymap_layouts():
            options.append((("Other layout...", "all of Alpine's layouts"), "other"))
        default = next((i for i, o in enumerate(options) if o[1] == current), 0)
        pick = self.choose("KEYBOARD", "KEYBOARD LAYOUT", options, default, headers=("LAYOUT", "KEYMAP"),
                           widths=(30,), note="It is switched on right away, so you can check it while typing\n"
                                              "the passwords later on.")
        if pick is None:
            return False
        if pick == "other":
            layouts = keymap_layouts()
            lay = self.choose("KEYBOARD", "ALL LAYOUTS", [((l,), l) for l in layouts])
            if lay is None:
                return self.step_keyboard()
            variants = keymap_variants(lay) or [lay]
            var = self.choose("KEYBOARD", f"VARIANTS OF {lay.upper()}", [((v,), v) for v in variants])
            if var is None:
                return self.step_keyboard()
            pick = (lay, var, f"{lay} / {var}")
        self.a["keymap"] = pick
        if not self.dry:  # switch the console layout now (Alpine's own tool)
            subprocess.run(["setup-keymap", pick[0], pick[1]], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        return True

    def step_hostname(self):
        while True:
            name = self.ask("COMPUTER NAME", "Name of this computer", self.a["hostname"],
                            info=["The name this computer has on the network,",
                                  "and in the prompt: user@NAME. Letters, digits and '-'."])
            if name is None:
                return False
            name = name.strip()
            if HOSTNAME_RE.match(name):
                self.a["hostname"] = name
                return True
            self.error("COMPUTER NAME", "Use letters, digits and '-' only (not at the start or end).")

    def step_timezone(self):
        options = [((tz,), tz) for tz in TIMEZONES] + [(("Other...",), "other")]
        default = next((i for i, o in enumerate(options) if o[1] == self.a["timezone"]), 0)
        pick = self.choose("TIME ZONE", "TIME ZONE", options, default)
        if pick is None:
            return False
        if pick == "other":
            tz = self.ask("TIME ZONE", "Time zone", self.a["timezone"],
                          info=["Region/City, for example Europe/Madrid or America/Chicago."])
            if not tz or not re.match(r"^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)*$", tz.strip()):
                return self.step_timezone()
            pick = tz.strip()
        self.a["timezone"] = pick
        return True

    def step_network(self):
        ifaces = list_interfaces()
        options = []
        for name, kind in ifaces:
            if kind == "wired":
                options.append(((name, "wired, automatic address (DHCP)"), name))
        options.append((("no network", "set it up later"), ""))
        wifi = [n for n, k in ifaces if k != "wired"]
        note = f"Wi-Fi ({', '.join(wifi)}) isn't set up by the installer yet." if wifi else None
        default = next((i for i, o in enumerate(options) if o[1] == self.a["iface"]), 0)
        pick = self.choose("NETWORK", "NETWORK", options, default, headers=("INTERFACE", "HOW"),
                           widths=(16,), note=note)
        if pick is None:
            return False
        self.a["iface"] = pick
        return True

    def step_ssh(self):
        options = [(("No", "nobody can log in over the network"), False),
                   (("Yes", "OpenSSH: log in from another computer with ssh"), True)]
        pick = self.choose("SSH", "SSH SERVER", options, 1 if self.a["ssh"] else 0,
                           headers=("SSH", "MEANS"), widths=(10,))
        if pick is None:
            return False
        self.a["ssh"] = pick
        return True

    def ask_password(self, step, who):
        info = [f"The password for {who}. You type it twice; it isn't shown.",
                "Check the keyboard layout: it is the one you chose in step 1."]
        while True:
            pw = self.ask(step, f"Password for {who}", secret=True, info=info)
            if pw is None:
                return None
            if len(pw) < 4:
                self.error(step, "Use at least 4 characters.")
                continue
            again = self.ask(step, f"Password for {who} again", secret=True, info=info)
            if again is None:
                return None
            if again != pw:
                self.error(step, "The two passwords are not the same. Try again.")
                continue
            hashed = sha512_hash(pw)
            if not hashed:
                self.error(step, "This system can't make a $6$ password hash. Please report this.")
                return None
            return hashed

    def step_user(self):
        while True:
            name = self.ask("USER", "Your user name", self.a["user"],
                            info=["Your everyday account: it logs in on the PCal login screen",
                                  "and can do admin tasks with doas. Small letters, e.g. jules."])
            if name is None:
                return False
            name = name.strip()
            if not USERNAME_RE.match(name) or name in RESERVED_USERS:
                self.error("USER", "Small letters, digits, '-' and '_', starting with a letter. Not 'root'.")
                continue
            hashed = self.ask_password("USER", name)
            if hashed is None:
                continue
            self.a["user"], self.a["user_hash"] = name, hashed
            return True

    def step_root(self):
        options = [(("A separate password", "recommended"), "own"),
                   (("The same as " + self.a["user"], "easier to remember"), "same")]
        pick = self.choose("ROOT", "PASSWORD FOR ROOT (THE ADMINISTRATOR)", options,
                           note="Your user can also run admin commands with doas, using its own password.")
        if pick is None:
            return False
        if pick == "same":
            self.a["root_hash"] = self.a["user_hash"]
            return True
        hashed = self.ask_password("ROOT", "root")
        if hashed is None:
            return self.step_root()
        self.a["root_hash"] = hashed
        return True

    def step_disk(self):
        disks = list_disks()
        if not disks and self.dry:  # so the disk screens can be tried anywhere
            disks = [("sda", 16 * 1024 ** 3, "EXAMPLE DISK (dry run)")]
        if not disks:
            self.error("DISK", "No disk of at least 2 GB found to install on.")
            return False
        options = [((f"/dev/{n}", rm.human_size(size), model), n) for n, size, model in disks]
        default = next((i for i, o in enumerate(options) if o[1] == self.a["disk"]), 0)
        pick = self.choose("DISK", "INSTALL ON WHICH DISK?", options, default,
                           headers=("DISK", "SIZE", "WHAT"), widths=(16, 10),
                           note="PCal uses the WHOLE disk, like Alpine's 'sys' install.\n"
                                "Everything on it will be erased.")
        if pick is None:
            return False
        size, model = next((s, m) for n, s, m in disks if n == pick)
        answer = self.ask("DISK", "Type YES to erase this disk",
                          info=[f"/dev/{pick}   {rm.human_size(size)}   {model}",
                                "EVERYTHING ON THIS DISK WILL BE ERASED."])
        if answer is None or answer.strip() != "YES":
            return self.step_disk()
        self.a["disk"] = pick
        return True

    def step_summary(self):
        a = self.a
        rows = [
            ("Keyboard", f"{a['keymap'][2]}  ({a['keymap'][0]}/{a['keymap'][1]})"),
            ("Computer name", a["hostname"]),
            ("Time zone", a["timezone"]),
            ("Network", f"{a['iface']} (DHCP)" if a["iface"] else "none"),
            ("SSH server", "yes (OpenSSH)" if a["ssh"] else "no"),
            ("User", a["user"] + "  (admin via doas)"),
            ("Root password", "set"),
            ("Disk", f"/dev/{a['disk']}  -- WILL BE ERASED"),
        ]
        options = [(r, "info") for r in rows] + [(("", ""), "info"), (("INSTALL PCAL", "erase the disk and install"), "go")]
        while True:
            pick = self.choose("SUMMARY", "READY TO INSTALL", options, len(options) - 1,
                               headers=("SETTING", "CHOICE"), widths=(18,),
                               note="ENTER on INSTALL PCAL starts. LEFT goes back to change something.")
            if pick is None:
                return False
            if pick == "go":
                return True

    def run(self):
        """Walk through the steps (LEFT goes back). Returns the answers, or None if cancelled."""
        welcome = [
            "This installs PCal on this computer's disk.",
            "",
            "It asks the same questions as Alpine's setup-alpine, then uses",
            "Alpine's own tools to install, and switches on the PCal login",
            "screen and menu on the new system.",
            "",
            "Nothing is changed until the last screen.",
        ]
        if not self.message("", "WELCOME", welcome, hints="ENTER start  LEFT/ESC cancel"):
            return None
        steps = [self.step_keyboard, self.step_hostname, self.step_timezone, self.step_network,
                 self.step_ssh, self.step_user, self.step_root, self.step_disk, self.step_summary]
        i = 0
        while i < len(steps):
            if steps[i]():
                i += 1
            elif i == 0:
                return None
            else:
                i -= 1
        return self.a


# ------------------------------------------------------------- installing
def answer_file(a):
    """setup-alpine answer file. USEROPTS and DISKOPTS are none: those steps follow separately."""
    if a["iface"]:
        interfaces = f"auto lo\niface lo inet loopback\n\nauto {a['iface']}\niface {a['iface']} inet dhcp\n"
        repos = "-1 -c"  # first mirror, community enabled
    else:
        interfaces = "auto lo\niface lo inet loopback\n"
        repos = "none"
    return "\n".join([
        "# PCal installer answers for setup-alpine -f (user and disk are set up separately)",
        f'KEYMAPOPTS="{a["keymap"][0]} {a["keymap"][1]}"',
        f'HOSTNAMEOPTS="{a["hostname"]}"',
        'DEVDOPTS="mdev"',
        f'INTERFACESOPTS="{interfaces}"',
        'DNSOPTS="1.1.1.1 9.9.9.9"',
        f'TIMEZONEOPTS="{a["timezone"]}"',
        'PROXYOPTS="none"',
        f'APKREPOSOPTS="{repos}"',
        'USEROPTS="none"',
        f'SSHDOPTS="{"openssh" if a["ssh"] else "none"}"',
        'NTPOPTS="busybox"',
        'DISKOPTS="none"',
        'LBUOPTS="none"',
        'APKCACHEOPTS="none"',
        "",
    ])


def plan(a):
    """The install, as (title, command, env, stdin) steps. stdin carries the password hashes."""
    user, disk = a["user"], f"/dev/{a['disk']}"
    groups = "wheel audio video netdev input"
    user_sh = (
        f"adduser -D -s /bin/ash -h /home/{user} {user} && "
        f"for g in {groups}; do addgroup {user} $g 2>/dev/null || true; done && "
        "mkdir -p /etc/doas.d && "
        "{ grep -qs ':wheel' /etc/doas.d/*.conf || echo 'permit persist :wheel' > /etc/doas.d/20-wheel.conf; }"
    )
    target_sh = (
        # setup-disk leaves the new system unmounted: mount its root filesystem
        f"part=$(blkid | grep '^{disk}' | grep 'TYPE=\"ext4\"' | tail -1 | cut -d: -f1) && "
        f"[ -n \"$part\" ] || {{ echo 'no ext4 root partition found on {disk}'; exit 1; }} && "
        f"mkdir -p {TARGET} && mount \"$part\" {TARGET} && "
        f"mount --bind /dev {TARGET}/dev && mount --bind /proc {TARGET}/proc && mount --bind /sys {TARGET}/sys && "
        f"chroot {TARGET} pcal-setup {user}; rc=$?; "
        f"umount {TARGET}/sys {TARGET}/proc {TARGET}/dev {TARGET}; exit $rc"
    )
    return [
        ("Alpine setup: keyboard, name, network, time, mirrors, SSH",
         ["setup-alpine", "-e", "-f", ANSWERS], {}, None),
        (f"User account {user} (like setup-user)", ["sh", "-c", user_sh], {}, None),
        ("Passwords for root and " + user + " ($6$ format)",
         ["chpasswd", "-e"], {}, f"root:{a['root_hash']}\n{user}:{a['user_hash']}\n"),
        (f"Installing on {disk} (setup-disk -m sys)",
         ["setup-disk", "-m", "sys", disk], {"ERASE_DISKS": disk}, None),
        ("PCal login screen and menu on the new system (pcal-setup)", ["sh", "-c", target_sh], {}, None),
    ]


def say(text, colour="1;32"):
    print(f"\033[{colour}m{text}\033[0m", flush=True)


def install(a, dry):
    """Runs the plan in the plain terminal. Returns True when everything worked."""
    steps = plan(a)
    print()
    say("=" * 70)
    say("  PCAL INSTALLER" + ("  --  DRY RUN: nothing will be changed" if dry else ""))
    say("=" * 70)
    if dry:
        say(f"\nanswer file ({ANSWERS}):", "1")
        print(answer_file(a))
    else:
        with open(ANSWERS, "w") as f:
            f.write(answer_file(a))
        os.chmod(ANSWERS, 0o600)
    log = None if dry else open(LOG, "a")
    try:
        for n, (title, cmd, env, stdin) in enumerate(steps, 1):
            say(f"\n[{n}/{len(steps)}] {title}")
            shown = " ".join(f"{k}={v}" for k, v in env.items())
            shown = (shown + " " if shown else "") + " ".join(shlex.quote(c) for c in cmd)
            print(f"    $ {shown}" + ("   (password hashes on stdin)" if stdin else ""), flush=True)
            if dry:
                continue
            log.write(f"\n=== [{n}] {title}\n$ {shown}\n")
            log.flush()
            r = subprocess.run(cmd, env=dict(os.environ, **env),
                               input=stdin.encode() if stdin else None,
                               stdin=None if stdin else sys.stdin)
            log.write(f"exit code {r.returncode}\n")
            if r.returncode != 0:
                say(f"\nStep {n} failed (exit code {r.returncode}). Nothing after it was done.", "1;31")
                say(f"The log is in {LOG}.", "1;31")
                return False
        return True
    finally:
        if log:
            log.close()
        if not dry:
            try:
                os.remove(ANSWERS)
            except OSError:
                pass


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    if "-h" in args or "--help" in args:
        print(__doc__.strip())
        return 0
    if not dry:
        if os.geteuid() != 0:
            print("pcal-install must run as root, from the PCal ISO. To just look: pcal-install --dry-run")
            return 1
        if not is_live():
            print("pcal-install runs from the PCal ISO (live system), not on an installed one.\n"
                  "To look at the screens anyway: pcal-install --dry-run")
            return 1
    if not os.environ.get("LANG"):
        os.environ["LANG"] = "C.UTF-8"
    os.environ.setdefault("ESCDELAY", "25")
    try:
        locale.setlocale(locale.LC_ALL, "")
    except locale.Error:
        pass

    answers = curses.wrapper(lambda scr: Installer(scr, dry).run())
    if answers is None:
        print("Installation cancelled. Nothing was changed.")
        return 0
    ok = install(answers, dry)
    if dry:
        say("\nDry run finished: this is what would have been run.")
        return 0
    if not ok:
        input("\nPress ENTER to go back to the menu ")
        return 1
    say("\nPCal is installed.", "1;32")
    say("Remove the ISO (or USB stick), then press ENTER to reboot.", "1")
    try:
        input()
    except EOFError:
        pass
    subprocess.run(["reboot"])
    return 0


def entry():
    try:
        return main()
    except KeyboardInterrupt:
        print("\nInstallation cancelled.")
        return 1


if __name__ == "__main__":
    sys.exit(entry())
