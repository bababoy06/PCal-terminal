# Installation guide

This guide installs a stable PCal release. To try changes that are not released yet, build your own
ISO with the [development guide](development.md) and then follow this guide with that ISO.

- [1. Requirements](#1-requirements)
- [2. Download and verify](#2-download-and-verify)
- [3. Install in a virtual machine](#3-install-in-a-virtual-machine)
- [4. Install on a real computer](#4-install-on-a-real-computer)
- [5. Install on an existing Alpine system](#5-install-on-an-existing-alpine-system)
- [6. First start](#6-first-start)
- [7. Updating](#7-updating)
- [8. Uninstalling](#8-uninstalling)
- [9. Troubleshooting](#9-troubleshooting)

## 1. Requirements

| | Minimum | Recommended |
|---|---|---|
| Processor | x86_64 (64-bit Intel or AMD) | 2 cores |
| Memory | 512 MB | 1 GB or more (the live system runs from memory) |
| Disk | 2 GB | 8 GB |
| Display | any; PCal falls back to text mode | a framebuffer with a Linux driver, for the graphical screen |
| Network | wired, with DHCP, during installation | |

PCal does not support ARM (such as the Raspberry Pi) or Wi-Fi during installation yet.

## 2. Download and verify

Open the [latest release](https://github.com/bababoy06/PCal-terminal/releases/latest) and download:

- `pcal-virt-VERSION-x86_64.iso`: the live and install image for virtual machines
- `pcal-virt-VERSION-x86_64.iso.sha256`: its checksum

Verify the download before using it. The command must print `OK`:

| System | Command |
|---|---|
| Linux | `sha256sum -c pcal-virt-VERSION-x86_64.iso.sha256` |
| macOS | `shasum -a 256 -c pcal-virt-VERSION-x86_64.iso.sha256` |
| Windows (PowerShell) | `Get-FileHash .\pcal-virt-VERSION-x86_64.iso` and compare the hash with the one in the `.sha256` file |

## 3. Install in a virtual machine

### VirtualBox

1. **Create the VM**: *Machine › New*.
   - Type **Linux**, version **Other Linux (64-bit)**
   - Memory **1024 MB**, processors **2**
   - A new virtual disk of **8 GB** (VDI, dynamically allocated)
2. **Display settings**: *Settings › Display › Screen*.
   - Graphics Controller **VMSVGA**: this gives PCal's graphical screen a proper driver
   - Video Memory **64 MB** or more
   - Leave **3D acceleration off**
3. **Attach the ISO**: *Settings › Storage*, select the empty optical drive and choose the downloaded ISO.
4. **Network**: the default **NAT** adapter is fine.
5. **Start the VM.** After a few seconds the PCal boot screen appears, followed by the main menu.
   This is the live system: it runs from memory and changes nothing on the disk.

Continue with [Running the installer](#running-the-installer).

### QEMU / KVM

```sh
qemu-img create -f qcow2 pcal.qcow2 8G
qemu-system-x86_64 -enable-kvm -m 1024 -smp 2 \
    -cdrom pcal-virt-VERSION-x86_64.iso \
    -drive file=pcal.qcow2,if=virtio \
    -device virtio-vga -nic user,model=virtio-net-pci
```

In virt-manager, choose **Virtio** as the video model. After installing, start the same command without
the `-cdrom` line.

### Running the installer

1. In the live menu, choose **Install PCal**.
2. Answer each screen:

   | Step | What to enter |
   |---|---|
   | Keyboard | your layout (it takes effect immediately) |
   | Computer name | for example `pcal` |
   | Time zone | for example `Europe/Brussels` |
   | Network | the wired interface, with DHCP |
   | SSH server | yes, if you want to log in remotely |
   | Your user | a user name and password: this is the account you will use |
   | Root password | the administrator password |
   | Disk | the disk to install to; type `YES` to confirm that it will be **erased** |

3. Check the **summary** and choose **INSTALL PCAL**. The installer runs Alpine's own setup tools
   on screen. If Alpine asks an extra question, answer it there. A full log is kept in
   `/tmp/pcal-install.log` until you reboot.
4. When it says the installation is complete, **remove the ISO** (VirtualBox: *Devices › Optical
   Drives › Remove disk from virtual drive*) and press Enter to reboot.

Continue with [First start](#6-first-start).

## 4. Install on a real computer

The ISO in the releases is the **virt** image: it is small and meant for virtual machines, and it does
not include firmware for most physical network cards, Wi-Fi or graphics chips. For a real computer,
build the full image with `sh iso/build-iso.sh` as described in the [development guide](development.md#5-build-the-iso).

1. **Write the ISO to a USB stick** (everything on the stick is erased):
   - Linux: `doas dd if=pcal-VERSION-x86_64.iso of=/dev/sdX bs=4M conv=fsync status=progress`, where
     `/dev/sdX` is the stick (check with `lsblk`; choosing the wrong device erases it)
   - Windows or macOS: [balenaEtcher](https://etcher.balena.io), or [Rufus](https://rufus.ie) in **DD mode**
2. **Boot from the stick**: use your computer's boot menu (often F12, F11, F8 or Esc right after
   power-on). If the stick doesn't appear, switch off **Secure Boot** in the firmware settings.
3. Follow [Running the installer](#running-the-installer), and remove the stick before rebooting.

## 5. Install on an existing Alpine system

Use this if you already run Alpine Linux and want to add PCal to it.

### From the release package (recommended)

Download the `.apk` file from the [latest release](https://github.com/bababoy06/PCal-terminal/releases/latest),
then, as root:

```sh
apk add --allow-untrusted pcal-VERSION-rNUMBER.apk
pcal-setup YOUR_USER
reboot
```

`--allow-untrusted` is needed because the package is signed with the key of the person who built it,
not with Alpine's. `pcal-setup` turns on the login screen and the menu for `YOUR_USER`; the
[`distro/` README](../distro/README.md) lists every change it makes and its options.

### With the install script

As root, with network access:

```sh
wget -qO- https://raw.githubusercontent.com/bababoy06/PCal-terminal/main/distro/install.sh | sh -s -- --fast-boot
reboot
```

The script downloads the release program, installs the packages it needs and configures the system
the same way as `pcal-setup`. `--fast-boot` shortens the boot menu; leave it out to keep yours as it is.

## 6. First start

1. On the first boot after installing from the ISO, PCal finishes its own setup. You see a short
   `PCAL -- FIRST START` message while it configures the login screen for your user.
2. The **login screen** appears. Log in with the user you created.
3. The **main menu** opens. Good places to start:
   - **Settings**: theme, effects, title, font size, and the graphical screen
   - **Desk**: the command deck (in the graphical screen)
   - **Log out**: back to the login screen

### Graphical or text mode

PCal starts in the **graphical screen** when the computer has a usable framebuffer, and in **text mode**
otherwise. To choose yourself, open *Settings › Display* and pick **GRAPHICAL** or **TEXT**. The change
applies at the next login on the first console.

### Other consoles

The PCal login screen runs on the first console only. **Alt+F2** to **Alt+F6** are standard text
logins. They always work, even if something is wrong with PCal, so you can log in there to fix things.

## 7. Updating

Download the newer `.apk` from the [releases page](https://github.com/bababoy06/PCal-terminal/releases), then as root:

```sh
apk add --allow-untrusted pcal-VERSION-rNUMBER.apk
```

`apk` replaces the older version and keeps your settings. Log out and back in to start the new version.

## 8. Uninstalling

As root:

```sh
pcal-setup --uninstall YOUR_USER   # restores the standard login and removes the menu autostart
apk del pcal                       # removes the programs
reboot
```

Your settings in `~/.config/retromenu/` and the system title in `/etc/pcal/` are kept; delete them by
hand if you don't need them.

## 9. Troubleshooting

**PCal starts in text mode instead of the graphical screen**
- In VirtualBox, set the Graphics Controller to **VMSVGA** (VM powered off). In QEMU, use `virtio-vga`.
- Check that a display driver is loaded: `ls /dev/dri` should list `card0`.
- Check *Settings › Display*: it should say **GRAPHICAL**.
- Run `pontes screen --bench` from a console. If it reports that numpy is missing, your release was
  built without it; update to a newer release or [build your own](development.md).

**The screen is stuck or black**
Press **Alt+F2**, log in, and run `doas pkill -f "pontes screen"`. The first console restarts on its own.
To stay in text mode, set `"display": "text"` in `~/.config/retromenu/config.json` of the user on the
first console, or choose *Settings › Display › TEXT* once you're back in.

**The login screen does not accept my password**
Log in on **Alt+F2** to check that the password itself is right. If your password was set by a tool
that uses a format PCal cannot check, the login screen hands over to the standard login, which asks
for the password again. Setting the password once more with `passwd` fixes this for good.

**The keyboard layout is wrong**
Log in as root on Alt+F2 and run `setup-keymap`.

**The installer stops with an error**
The full output is in `/tmp/pcal-install.log` on the live system. The most common causes are no network
connection (PCal needs a wired connection with DHCP during installation) and a disk smaller than 2 GB.

**Something else**
[Open an issue](https://github.com/bababoy06/PCal-terminal/issues) with what you did, what you expected,
and a photo or the text of the screen.
