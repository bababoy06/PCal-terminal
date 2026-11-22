# RetroMenu

Green-phosphor, arrow-key home screen for a lightweight Linux. One Python file, standard library only.

## Run it

```sh
python3 retromenu.py
```

Keys: `UP/DOWN` move, `RIGHT/ENTER` open, `LEFT/ESC/q` back, `PGUP/PGDN` scroll. Vim keys `hjkl` also work.

## Main menu

| Entry       | What it does |
|-------------|--------------|
| Files       | File manager starting in `~`. RIGHT enters a folder, LEFT goes up, ENTER opens the action drop-down (Open with >, Open terminal here, Details, New >, Rename, Delete >, Refresh). `.` toggles hidden files. |
| Terminal    | Your shell. Type `exit` to come back. |
| Top         | Runs `top` by default (available on every system). Change it to `htop`, `btop` or anything else in Settings. |
| Plugins     | One entry per JSON file in `~/.config/retromenu/plugins/` (see `plugins/network.json`). Shows "Placeholder" when there are none. |
| Settings    | Theme (green/amber/white/cyan), monitor, shell, editor, pager, hidden files, splash, reboot command. Saved to `~/.config/retromenu/config.json`. |
| Log out     | Leave the menu. On Pontes-Calos this returns to the login screen. |
| Reboot      | Asks for confirmation, then uses `doas`/`sudo` if needed (password prompt). |
| Shut down   | Asks for confirmation, then powers off (`poweroff`, through `doas`/`sudo` when needed). |

## Command line under the menu

The `user@host:~$` line under the main menu is a real prompt.

- Press **t** anywhere in the main menu or the file manager to open the full terminal (in the current folder). Type `exit` to come back.
- Press **i** (like insert mode in vim) or **TAB** to jump into it. **ESC** goes back to the menu. In the menu itself, `h j k l` still navigate.
- **ENTER** runs the command, and its output appears right under the prompt. **PGUP/PGDN** scrolls the output.
- **ENTER on an empty line** opens the full terminal.
- **↑/↓** browses your history, which is saved in `~/.config/retromenu/history`. ↑ past the oldest entry returns to the menu.
- `cd` changes the prompt's folder, and `clear` (or Ctrl-L) empties the output.
- Programs that need the whole screen or the keyboard (`vim`, `nano`, `top`, `less`, `ssh`, `sudo`, `python`...) automatically run in the full terminal. Start any command with `!` to force that, e.g. `!ping 1.1.1.1`.
- Quick commands are stopped after 5 seconds, including anything they started, so a runaway command can't freeze the menu.

## Look

- Double-line boxed tables (`╔═╦═╗`) for every menu, popup and dialog, with an ASCII fallback (`+=|`) for non-UTF-8 consoles.
- Big ASCII banner, a live readout of memory, load and uptime, a blinking prompt cursor, and a boot screen with a block progress bar.
- `ls -l` style file view with permissions, size, modified date and name. It switches to size + name on narrow screens.
- Themes: green, amber, white, cyan. Uses 256 colours when the terminal supports them.
- On the real Linux console (`TERM=linux`), the menu repaints the console's own palette into phosphor green or amber, and restores it on exit. This can be turned off in Settings > Console palette.
- **CRT effects** (Settings > Effects: `OFF` / `SUBTLE` / `FULL`, default subtle). The login screen follows the same setting.
  - *Power on/off*: at start the picture opens from a bright line, like an old monitor warming up. On Log out and Reboot it collapses to a line, then a dot, then black.
  - *Phosphor afterglow*: the row you move away from fades out over about 0.2 s instead of switching off at once.
  - *Full* adds the dot-to-line warm-up, typewriter text on the boot screen and login messages, and on the real Linux console a rare one-frame flicker.
  - Every effect lasts a fraction of a second and never blocks your keys for longer. Choose `OFF` for the fastest possible screens.
- For CRT glow, scanlines and curvature on a desktop, run it inside [cool-retro-term](https://github.com/Swordfish90/cool-retro-term).

## Plugins

```json
{ "name": "Network", "description": "Wifi and IP tools", "command": "nmtui",
  "options": [ { "label": "Show IP", "command": "ip a", "pause": true },
               { "label": "More", "options": [ ... ] } ] }
```

A program can also list its own options with `"options_cmd": "myprog --retromenu-options"`. That command must print a JSON list in the same format.

## Speed notes

- Folders are read with `os.scandir` (no stat per file). Only the rows visible on screen get a size lookup.
- Listings are cached per folder and reused until the folder's mtime changes, so going back is instant.
- No dependencies beyond `python3`. Starts in about 50 ms.

## Make it the home screen on Alpine

```sh
apk add python3 less
install -m755 retromenu.py /usr/local/bin/retromenu
mkdir -p ~/.config/retromenu/plugins && cp plugins/*.json ~/.config/retromenu/plugins/
```

Autologin on tty1. In `/etc/inittab`, replace the tty1 line with:

```
tty1::respawn:/bin/login -f YOURUSER
```

Start the menu on tty1 only. Add this to `~/.profile`:

```sh
[ "$(tty)" = /dev/tty1 ] && exec retromenu
```

Other ttys (Ctrl+Alt+F2) still give a normal login, which is your safety net.
For a nicer console font: `apk add terminus-font` then `setfont ter-v18n`.

## Graphical screen (in progress)

`pontes screen` shows the menu (or any program) as an old CRT monitor, drawn straight onto
the display through the framebuffer: phosphor glow, scanlines, a curved tube, afterglow and the
power on/off effect. No X or Wayland. Not started automatically yet.

- `screen.py`: the live screen: runs the program in a pseudo-terminal, passes your keys on,
  draws only what changed, and gives the console back when you switch away (Alt+F1..F6).
  Without numpy, a font or a framebuffer it just runs the program in text mode.
- `vt.py`: a small terminal emulator (xterm-256color subset) that turns program output into cells.
- `psf.py`: reads Linux console fonts (Terminus); box lines, blocks and arrows are drawn by code.
- `crt.py`: renders cells as a CRT picture, redrawing only the rows that changed. Needs numpy.

Try it on Alpine, on the VM's own screen (a console such as tty2, not over SSH):

```sh
doas apk add py3-numpy font-terminus
doas addgroup $USER video                 # access to /dev/fb0; log out and in again
python3 retromenu/pontes.py screen        # leave the menu (Log out) to come back
python3 retromenu/pontes.py screen --theme amber --effects full -- htop
python3 retromenu/pontes.py screen --bench   # drawing speed, no display needed
```

Without a display, `crt.py` saves pictures instead:

```sh
python3 retromenu/crt.py -o menu.png --size 1920x1080 --font-size 32 -- python3 retromenu/retromenu.py
python3 tests/crt_test.py --menu
```
