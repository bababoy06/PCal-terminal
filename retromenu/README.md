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
| Htop        | Runs `htop` by default. Name and command can be changed in Settings. |
| Plugins     | One entry per JSON file in `~/.config/retromenu/plugins/` (see `plugins/network.json`). Shows "Placeholder" when there are none. |
| Settings    | Theme (green/amber/white/cyan), monitor, shell, editor, pager, hidden files, splash, reboot command. Saved to `~/.config/retromenu/config.json`. |
| Reboot      | Asks for confirmation, then uses `doas`/`sudo` if needed (password prompt). |

## Plugins

```json
{ "name": "Network", "command": "nmtui",
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
apk add python3 htop less
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
