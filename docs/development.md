# Development guide

How to work on PCal, and how to build the release program, the Alpine package and the ISO yourself,
for example to try changes from `main` before they are released.

- [1. Overview](#1-overview)
- [2. Set up a build machine](#2-set-up-a-build-machine)
- [3. Run from source](#3-run-from-source)
- [4. Build the release program and the package](#4-build-the-release-program-and-the-package)
- [5. Build the ISO](#5-build-the-iso)
- [6. Test your build](#6-test-your-build)
- [7. Publish a release](#7-publish-a-release)
- [8. Contributing](#8-contributing)

## 1. Overview

PCal is written in Python. The menu, login screen and installer use only the standard library; the
graphical screen also uses numpy. A release turns this source into three layers, each built from the
one before:

| Step | Command | Result |
|---|---|---|
| Release program | `sh build/build.sh` | `dist/release/`: a self-contained program with its own Python (PyInstaller) |
| Alpine package | `sh packaging/alpine/build-apk.sh` | `dist/apk/pcal/x86_64/pcal-VERSION-rNUMBER.apk` + a signed `APKINDEX.tar.gz` |
| ISO | `sh iso/build-iso.sh --virt` | `dist/iso/pcal-virt-VERSION-x86_64.iso` + `.sha256` |

| Folder | Contents |
|---|---|
| [`retromenu/`](../retromenu/) | all programs: `retromenu.py` (menu), `retrologin.py` (login screen), `retroinstall.py` (installer), `screen.py` + `crt.py` + `vt.py` + `psf.py` (graphical screen), `deck.py` (the Desk), `title.py` (the big title), `pontes.py` (one program that runs them all) |
| [`build/`](../build/) | `build.sh` and the list of standard modules the release leaves out |
| [`packaging/alpine/`](../packaging/alpine/) | the package recipe (`APKBUILD`), `pcal-live` and the build script |
| [`iso/`](../iso/) | the `mkimage` profile, the overlay with the live system's settings, and the build script |
| [`distro/`](../distro/) | `install.sh`, installed as `pcal-setup` |
| [`tests/`](../tests/) | `smoke_test.py` (the programs in a pseudo-terminal) and `crt_test.py` (graphical screen) |

## 2. Set up a build machine

Build on **Alpine Linux x86_64**, the same system PCal runs on. A virtual machine works well. Give it at
least **2 GB of memory** and a **20 GB disk**: the full ISO needs about 6 GB of free space while it is built.

A practical setup uses two VMs:

| VM | Purpose | Login on tty1 |
|---|---|---|
| **Build VM** | clone, edit, build | the standard text login, so a broken login screen can't lock you out |
| **Test VM** | install your ISO or package and try it | PCal, as a user would have it |

### Packages

As root on the build VM (enable the *community* repository in `/etc/apk/repositories` first):

```sh
apk add git python3 py3-pip py3-numpy binutils build-base zlib-dev python3-dev \
        font-terminus alpine-sdk alpine-conf syslinux xorriso squashfs-tools \
        grub grub-efi mtools dosfstools doas
```

### Your user

Build as a normal user, not as root:

```sh
# as root
adduser -D jules && passwd jules
addgroup jules wheel        # doas
addgroup jules abuild       # package building
addgroup jules video        # the graphical screen (/dev/fb0)
echo 'permit persist :wheel' > /etc/doas.d/wheel.conf
```

Log out and back in as that user, then create the key that signs your packages (once):

```sh
abuild-keygen -a -i -n
```

### Get the source

```sh
git clone https://github.com/bababoy06/PCal-terminal
cd PCal-terminal
```

To use the newest code at any time: `git pull`.

## 3. Run from source

No build is needed to try changes:

| Command | What it runs |
|---|---|
| `python3 retromenu/retromenu.py` | the menu |
| `python3 retromenu/retrologin.py --login-cmd /bin/true` | the login screen, without logging anyone in |
| `python3 retromenu/pontes.py install --dry-run` | the installer, showing what it would run without changing anything |
| `python3 retromenu/pontes.py screen` | the menu in the graphical screen (run it on the VM's own console, not over SSH) |
| `python3 retromenu/pontes.py screen --bench` | the graphical screen's drawing speed, no display needed |
| `python3 retromenu/crt.py -o menu.png -- python3 retromenu/retromenu.py` | a picture of the graphical screen, saved as PNG |

### Tests

Run these before every pull request:

```sh
python3 tests/smoke_test.py python3 retromenu/pontes.py
python3 tests/smoke_test.py --block build/excludes.txt python3 retromenu/pontes.py
python3 tests/crt_test.py --menu
```

The second line checks that the code doesn't depend on a standard module the release program leaves
out. `--menu` also renders the real menu and runs the graphical screen into a file.

## 4. Build the release program and the package

```sh
sh build/build.sh
sh packaging/alpine/build-apk.sh
```

- `build.sh` creates `build/.venv` with PyInstaller the first time (this needs internet once). It
  includes numpy when `py3-numpy` is installed and warns when it isn't; without numpy, the graphical
  screen of that build falls back to text mode.
- `build-apk.sh` takes the version from `VERSION` in `retromenu/retromenu.py`. The package release
  number is the build time, so every build is newer than the one before.

Install the package on the machine you built it on:

```sh
doas apk add --allow-untrusted dist/apk/pcal/x86_64/pcal-*.apk
```

## 5. Build the ISO

The ISO needs the package from the previous step.

```sh
sh iso/build-iso.sh --virt     # virtual machines: dist/iso/pcal-virt-VERSION-x86_64.iso (about 1.5 GB free space)
sh iso/build-iso.sh            # real computers, with firmware: dist/iso/pcal-VERSION-x86_64.iso (about 6 GB)
```

The ISO uses the same Alpine release as the build machine, and downloads Alpine's packages, so it needs
internet. The script checks the free space first and removes its work files afterwards.

If the disk is too small, power off the VM, enlarge its disk in your VM software, then grow the partition:

```sh
doas apk add cloud-utils-growpart e2fsprogs-extra
doas growpart /dev/sda 3 && doas resize2fs /dev/sda3
```

## 6. Test your build

- **ISO**: copy `dist/iso/*.iso` to the host and boot it in the test VM, then follow the
  [installation guide](installation.md#3-install-in-a-virtual-machine).
- **Package only**: copy the `.apk` to the test VM (for example with `scp`) and install it there with
  `doas apk add --allow-untrusted pcal-*.apk`. Log out and back in to use it.

## 7. Publish a release

1. Set the new version in `retromenu/retromenu.py` (`VERSION = "0.8"`), in a pull request of its own.
2. After it is merged, build everything from a clean `main`:
   ```sh
   git checkout main && git pull
   sh build/build.sh
   sh packaging/alpine/build-apk.sh
   sh iso/build-iso.sh --virt
   ```
3. Test the ISO in the test VM (install, first start, login, graphical screen).
4. On GitHub, create a release with the tag `vVERSION` (for example `v0.8`) and attach:

   | File | From |
   |---|---|
   | `pcal-virt-VERSION-x86_64.iso` and `.sha256` | `dist/iso/` |
   | `pcal-VERSION-rNUMBER.apk` and `APKINDEX.tar.gz` | `dist/apk/pcal/x86_64/` |
   | `pontes-calos-linux-x86_64-musl.tar.gz` and `.sha256` | `dist/release/` |
   | `pontes-linux-x86_64-musl` and `.sha256` | `dist/release/` |

   Keep these file names: `distro/install.sh` downloads `pontes-calos-linux-x86_64-musl.tar.gz` from
   the latest release by name.

A GitHub Actions workflow (`.github/workflows/build.yml`) can build and smoke-test the program on every
push and attach it to tagged releases, when Actions is available for the repository.

## 8. Contributing

- **One change per pull request**, each on its own branch, so every change can be reviewed and merged on its own.
- Run the [tests](#tests) before opening it, and describe what you tested.
- The menu, login screen and installer must keep working with the Python standard library alone.
  Only the graphical screen may use numpy, and it must fall back to text mode without it.
- Every source file starts with the GPL header: `Copyright (C) 2026 bababoy06` and
  `SPDX-License-Identifier: GPL-3.0-or-later`.
