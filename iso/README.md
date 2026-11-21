# The PCal ISO

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

Result: `dist/iso/pcal-0.6-x86_64.iso` (+ `.sha256`). It needs internet, because the Alpine packages
come from the Alpine mirror, and takes a few minutes. The ISO uses the same Alpine release as the VM you build on.

## What's on it

| Part | What it does |
|---|---|
| `iso/mkimg.pcal.sh` | the image recipe: Alpine's *standard* image (works on real hardware) plus the `pcal` package |
| `iso/genapkovl-pcal.sh` | the settings layered on top: hostname `pcal`, DHCP network, UTF-8 console with the Terminus font, the key that signed `pcal`, and tty1 running `pcal-live` |
| `/usr/sbin/pcal-live` (in the package) | starts the PCal menu as root on tty1, **only when running live** (root filesystem in RAM). On an installed system it gives the normal login prompt instead. |
| `/usr/sbin/pcal-setup` (in the package) | `distro/install.sh`: turns on the login screen, menu autostart, doas rule, console font |

## Using it

1. Boot the ISO in a VM (or write it to a USB stick). The boot screen and PCal menu appear on their own.
2. Choose **Install PCal**. This runs Alpine's `setup-alpine`: pick your keyboard, hostname, network and timezone, **create a user**, and at the disk question choose your disk and **`sys`**.
3. Remove the ISO, reboot, log in as **root** and run:
   ```sh
   pcal-setup
   ```
4. Reboot once more: the RetroLogin screen appears, and after logging in, the menu.

Steps 2–4 become one retro install screen in the next step (ISO step 3).

## Safety notes

- The live menu runs as root **without a password**. That's normal for a live/install medium (Alpine's own ISO does the same), but it only happens when booted from the ISO: `pcal-live` checks that the root filesystem is in RAM.
- Alpine's installer copies the live system's `/etc/inittab` to the disk. Because of the check above, the installed system shows the normal login prompt on tty1 until you run `pcal-setup`. It never starts a root menu.
- The *Install PCal* entry lives in `/root/.config` on the live system only. It isn't copied to the disk.
