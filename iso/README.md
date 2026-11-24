# The PCal ISO

> To install PCal, see the [installation guide](../docs/installation.md). To build the ISO, see the
> [development guide](../docs/development.md#5-build-the-iso). This page explains what is on the ISO.

A bootable image that starts straight into the PCal menu (a *live* system running
from memory) and can install PCal onto a computer's disk.

## Build it (on your Alpine VM, as your normal user)

One-time setup, as root:

```sh
apk add alpine-sdk alpine-conf syslinux xorriso squashfs-tools grub grub-efi mtools dosfstools git
```

(plus the package setup from [`packaging/alpine/README.md`](../packaging/alpine/README.md): the `abuild` group and a signing key)

Then, in the repository:

```sh
sh build/build.sh                    # 1. the release build
sh packaging/alpine/build-apk.sh     # 2. the pcal package
sh iso/build-iso.sh                  # 3. the ISO
```

Result: `dist/iso/pcal-VERSION-x86_64.iso` (+ `.sha256`).

### Two kinds of ISO

| Command | ISO | For | Free space while building |
|---|---|---|---|
| `sh iso/build-iso.sh` | `pcal-VERSION-x86_64.iso` | real PCs and laptops (Alpine *standard*: firmware for Wi-Fi, graphics, ...) | about **6 GB** |
| `sh iso/build-iso.sh --virt` | `pcal-virt-VERSION-x86_64.iso` | virtual machines only (Alpine *virt*: small VM kernel, no firmware) | about **1.5 GB** |

For testing in VirtualBox/Hyper-V/QEMU, `--virt` is quicker and much smaller. The script checks
the free space before it starts, and removes its multi-GB work files when done or when it fails.

If your VM's disk is too small: shut the VM down, make its disk bigger in your VM program, then
grow the partition inside Alpine (with the community repository enabled):

```sh
doas apk add cloud-utils-growpart e2fsprogs-extra
doas growpart /dev/sda 3 && doas resize2fs /dev/sda3
```
 It needs internet, because the Alpine packages
come from the Alpine mirror, and takes a few minutes. The ISO uses the same Alpine release as the VM you build on.

## What's on it

| Part | What it does |
|---|---|
| `iso/mkimg.pcal.sh` | the image recipe: Alpine's *standard* image (works on real hardware) plus the `pcal` package |
| `iso/genapkovl-pcal.sh` | the settings layered on top: hostname `pcal`, DHCP network, UTF-8 console with the Terminus font, the key that signed `pcal`, and tty1 running `pcal-live` |
| `/usr/sbin/pcal-live` (in the package) | starts the PCal menu as root on tty1, **only when running live** (root filesystem in RAM). On an installed system it gives the normal login prompt instead. |
| `/usr/sbin/pcal-setup` (in the package) | `distro/install.sh`: turns on the login screen, menu autostart, doas rule, console font |

## Using it

1. Boot the ISO in a VM (or from a USB stick). The boot screen and PCal menu appear on their own, in the
   graphical screen (in VirtualBox set Display › Graphics Controller to VMSVGA). Without usable graphics it
   simply starts in text mode; Settings › Display switches between the two.
2. Choose **Install PCal**. The PCal installer asks, step by step:
   keyboard layout (switched on right away) · computer name · time zone · network (wired, DHCP) ·
   SSH server yes/no · your user name and password · root password · the disk (type `YES` to erase it).
3. A summary shows all choices. **INSTALL PCAL** then runs Alpine's own tools, visibly, in this order:

   | Step | Command |
   |---|---|
   | Alpine setup | `setup-alpine -e -f /tmp/pcal-answers` (keyboard, name, network, DNS, time zone, mirrors, NTP, SSH) |
   | User account | `adduser` + `addgroup` (wheel, audio, video, netdev, input) + doas for `wheel`, like `setup-user -a` |
   | Passwords | `chpasswd -e`, in the classic `$6$` format that the console, SSH and RetroLogin all check |
   | Note for the first start | `/etc/pcal/firstboot` with the user name (copied to the disk with the rest of `/etc`) |
   | Disk | `setup-disk -m sys /dev/DISK`, what `setup-alpine` runs last |

   If Alpine asks something extra, you answer it right there. Everything is logged to `/tmp/pcal-install.log`.
4. Remove the ISO, press Enter to reboot. On its **first start**, the new system sees the note, runs
   `pcal-setup` by itself (you see it on screen), and then shows the PCal login screen.

`pcal-install --dry-run` goes through every screen and prints what it would run, without changing anything.
It works anywhere (no root needed), so it's handy while developing.

## Safety notes

- The live menu runs as root **without a password**. That's normal for a live/install medium (Alpine's own ISO does the same), but it only happens when booted from the ISO: `pcal-live` checks that the root filesystem is in RAM.
- Alpine's installer copies the live system's `/etc/inittab` to the disk. Because of the check above, the installed system shows the normal login prompt on tty1 until you run `pcal-setup`. It never starts a root menu.
- The *Install PCal* entry lives in `/root/.config` on the live system only. It isn't copied to the disk.
