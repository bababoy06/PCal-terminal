# Pontes-Calos

A lightweight, speed-optimized Linux (Alpine-based) with a retro green-CRT, arrow-key home menu.

## Components

- [`distro/`](distro/): installer that turns a fresh Alpine install into Pontes-Calos (autologin on tty1 into the menu).
- [`retromenu/`](retromenu/): the home screen (file manager, terminal, system monitor shortcut (top), plugins, settings, reboot). Pure Python stdlib.

## Roadmap

1. RetroMenu as a standalone app (this PR)
2. Alpine VM: autologin on tty1 straight into RetroMenu (`distro/install.sh`)
3. Custom ISO built with `mkimage`
4. Boot splash, console font and palette
