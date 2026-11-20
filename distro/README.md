# Pontes-Calos on Alpine Linux

Turns a fresh Alpine install into a machine that boots straight into RetroMenu.
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
git clone https://github.com/bababoy06/Pontes-Calos
sh Pontes-Calos/distro/install.sh --fast-boot
```

or, if the repository is public, without git:

```sh
wget -qO- https://raw.githubusercontent.com/bababoy06/Pontes-Calos/main/distro/install.sh | sh -s -- --fast-boot
```

Reboot. tty1 now logs your user in and opens the menu.

## What the installer changes

| What | Where | Why |
|---|---|---|
| Packages | `python3 ncurses-terminfo-base doas less file terminus-font` | everything the menu needs |
| Program | `/usr/local/bin/retromenu` | the menu itself |
| Autologin | `/etc/inittab`, tty1 line (backup: `inittab.retromenu.bak`) | boot straight into the menu |
| Autostart | block in `~/.profile`, between `retromenu autostart` markers | starts the menu on tty1 only |
| Reboot rights | `/etc/doas.d/retromenu.conf` | Reboot works from the menu without a password |
| Console | `/etc/rc.conf` `unicode="YES"`, Terminus font, `LANG=C.UTF-8` | box-drawing characters render properly |
| `--fast-boot` | `/etc/update-extlinux.conf` (backup kept) | 1 s boot menu, quiet kernel |

## Safety net

- Only **tty1** is automatic. **Alt+F2** to **Alt+F6** are normal password logins, so you can always get in and fix things.
- If the menu crashes, tty1 drops to a normal shell instead of looping.
- **Exit** in the menu logs out, and the menu starts again a moment later. Use **Terminal** for a shell.
- `sh install.sh --uninstall` puts tty1, the boot settings and `.profile` back as they were.

## Options

```
install.sh [--no-autologin] [--fast-boot] [--uninstall] [USER]
```

`USER` defaults to the first account in `/home`.

## Security note

Autologin means anyone with the keyboard gets your user account on tty1. That account can't do anything as root except reboot or power off. For a shared or portable machine, use `--no-autologin`: you type your password once and the menu still starts.
