# Pontes-Calos on Alpine Linux

> For a step-by-step installation, including the ISO and the release package, see the
> [installation guide](../docs/installation.md). This page documents `install.sh` (`pcal-setup`) itself.

Turns a fresh Alpine install into a machine that boots into the RetroLogin screen and then RetroMenu.
Alpine is tiny (about 130 MB installed with Python), boots in a few seconds and uses OpenRC + BusyBox, so there's very little to slow it down.

## 1. Install Alpine (VM or old laptop)

1. Download the **Virtual** x86_64 ISO from https://alpinelinux.org/downloads/ (or **Standard** for real hardware).
2. Boot it in VirtualBox or QEMU with 512 MB RAM and an 8 GB disk. Log in as `root` (no password).
3. Run `setup-alpine`. Use the defaults, **create a user** when it asks (e.g. `jules`), and pick disk mode **sys**.
4. Remove the ISO and reboot.

## 2. Install Pontes-Calos

Log in as root, then run either:

```sh
apk add git
git clone https://github.com/bababoy06/PCal-terminal
sh PCal-terminal/distro/install.sh --fast-boot
```

or, if the repository is public, without git:

```sh
wget -qO- https://raw.githubusercontent.com/bababoy06/PCal-terminal/main/distro/install.sh | sh -s -- --fast-boot
```

The installer downloads the latest **release build**, which has Python built in, so `python3` isn't installed at all. If there is no release yet (or the repository is private), it installs the Python source instead. You can choose explicitly with `--binary pontes-calos-linux-x86_64-musl.tar.gz` (a file you copied over) or `--from-source`.

Reboot. tty1 now shows the green login screen. Log in and the menu opens; **Log out** brings you back to the login screen.

Your user needs a password (`setup-alpine` asks for one; otherwise run `passwd jules`).

## What the installer changes

| What | Where | Why |
|---|---|---|
| Packages | `ncurses-terminfo-base doas less file terminus-font` (+ `python3` for a source install) | everything the menu needs |
| Programs | `/usr/lib/pontes-calos/`, linked as `/usr/local/bin/retromenu`, `retrologin`, `pontes` | the menu and the login screen |
| Login screen | `/etc/inittab`, tty1 line (backup: `inittab.retromenu.bak`) | RetroLogin on tty1 instead of the plain prompt |
| Quiet login | `~/.hushlogin` | no "Welcome to Alpine" text between login and menu |
| Autostart | block in `~/.profile`, between `retromenu autostart` markers | starts the menu on tty1 only |
| Reboot rights | `/etc/doas.d/retromenu.conf` | Reboot works from the menu without a password |
| Console | `/etc/rc.conf` `unicode="YES"`, Terminus font, `LANG=C.UTF-8` | box-drawing characters render properly |
| `--fast-boot` | `/etc/update-extlinux.conf` (backup kept) | 1 s boot menu, quiet kernel |

## Safety net

- Only **tty1** uses the login screen. **Alt+F2** to **Alt+F6** are normal password logins, so you can always get in and fix things.
- tty1 runs in the **graphical screen** (`pontes screen --tty1`) unless Settings › Display is TEXT; it falls back to text by itself when there are no graphics. The menu's Desk modules always run as the user who logged in.
- If the menu crashes, tty1 drops to a normal shell instead of looping.
- **Log out** in the menu returns to the login screen. Use **Terminal** for a shell.
- If the login screen itself ever fails, init simply starts it again, and it never leaves a shell behind.
- `sh install.sh --uninstall` puts tty1, the boot settings and `.profile` back as they were.

## Options

```
install.sh [--plain-login | --autologin] [--binary FILE | --from-source] [--fast-boot] [--uninstall] [USER]
```

`USER` defaults to the first account in `/home`.

## The login screen

- It checks your password against `/etc/shadow`, the same as the normal login, and then hands over to `login -f`, which starts the session.
- Locked accounts and accounts **without a password** are always refused.
- Every wrong attempt waits 2 seconds, and after 5 wrong attempts the screen locks for 30 seconds.
- An unknown user name gets the same answer and takes the same time as a wrong password, so the screen doesn't reveal which accounts exist.
- It uses the theme you picked in the menu, so login and menu match.

## Login modes

| Option | tty1 shows | Use it for |
|---|---|---|
| *(default)* | RetroLogin screen, then the menu | normal use |
| `--plain-login` | the normal text login, then the menu | if you prefer the classic prompt |
| `--autologin` | the menu straight away, **no password** | a VM or a kiosk only |
