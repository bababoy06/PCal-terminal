#!/bin/sh
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
# Pontes-Calos installer: turns a fresh Alpine Linux install into a RetroMenu machine.
#
#   doas sh distro/install.sh [options] [USER]
#   wget -qO- https://raw.githubusercontent.com/bababoy06/Pontes-Calos/main/distro/install.sh | sh
#
# What it does (all reversible with --uninstall):
#   - installs the latest release build of Pontes-Calos (no Python needed) to
#     /usr/lib/pontes-calos, with retromenu / retrologin / pontes in /usr/local/bin
#     (falls back to the Python source when there is no release to download)
#   - shows the retro login screen on tty1; after login the menu starts
#     (tty2..tty6 stay normal password logins: your safety net, Alt+F2)
#   - lets USER reboot / power off from the menu without a password (doas)
#   - UTF-8 console with the Terminus font, so the boxes draw properly
set -eu

REPO_RAW="https://raw.githubusercontent.com/bababoy06/Pontes-Calos/main"
RELEASE_URL="https://github.com/bababoy06/Pontes-Calos/releases/latest/download"
ROOT="${ROOT:-}"  # only for testing in a fake root
BIN="$ROOT/usr/local/bin/retromenu"
LOGIN_BIN="$ROOT/usr/local/bin/retrologin"
PONTES_BIN="$ROOT/usr/local/bin/pontes"
LIBDIR="$ROOT/usr/lib/pontes-calos"
SHARE="$ROOT/usr/share/retromenu"
INITTAB="$ROOT/etc/inittab"
DOAS_RULE="$ROOT/etc/doas.d/retromenu.conf"
EXTLINUX="$ROOT/etc/update-extlinux.conf"
MARK_BEGIN="# >>> retromenu autostart >>>"
MARK_END="# <<< retromenu autostart <<<"
FONT="ter-v16n.psf.gz"

say() { printf '\033[32m[pcal]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[pcal]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[pcal] %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: install.sh [options] [USER]

  USER            account that gets the menu on tty1 (default: first user in /home)
  --plain-login   keep the normal text login on tty1 (the menu still starts after it)
  --autologin     no password at all on tty1 (only for a VM or a kiosk!)
  --binary FILE   install this release archive (pontes-calos-linux-*-musl.tar.gz)
  --from-source   install the Python source instead of the release build
  --fast-boot     1 second boot menu and quiet kernel messages (extlinux/BIOS installs)
  --uninstall     undo autologin, autostart, doas rule and fast boot (keeps the program)
  -h, --help      this text
EOF
}

LOGIN_MODE=screen  # screen | plain | auto
FASTBOOT=0
UNINSTALL=0
FROM_SOURCE=0
BINARY_FILE=""
TARGET=""
while [ $# -gt 0 ]; do
    case "$1" in
        --plain-login | --no-autologin) LOGIN_MODE=plain ;;
        --autologin) LOGIN_MODE=auto ;;
        --fast-boot) FASTBOOT=1 ;;
        --uninstall) UNINSTALL=1 ;;
        --from-source) FROM_SOURCE=1 ;;
        --binary)
            [ $# -ge 2 ] || die "--binary needs a file"
            BINARY_FILE=$2
            shift
            ;;
        -h | --help) usage; exit 0 ;;
        -*) usage; die "unknown option: $1" ;;
        *) TARGET="$1" ;;
    esac
    shift
done
[ -z "$BINARY_FILE" ] || [ -f "$BINARY_FILE" ] || die "no such file: $BINARY_FILE"

[ "$(id -u)" -eq 0 ] || die "run this as root, e.g.: doas sh install.sh"
[ -f "$ROOT/etc/alpine-release" ] || die "this installer is for Alpine Linux (see retromenu/README.md for other systems)"

# ---- which user?
if [ -z "$TARGET" ]; then
    for d in "$ROOT"/home/*; do
        if [ -d "$d" ]; then
            TARGET=$(basename "$d")
            break
        fi
    done
fi
[ -n "$TARGET" ] || die "no user found. Create one first: adduser NAME   then run this again"
HOME_DIR=$(awk -F: -v u="$TARGET" '$1 == u { print $6 }' "$ROOT/etc/passwd")
[ -n "$HOME_DIR" ] || die "user '$TARGET' does not exist"
[ "$TARGET" != root ] || warn "using root for the menu works, but a normal user is safer"
PROFILE="$ROOT$HOME_DIR/.profile"

remove_profile_block() {
    [ -f "$PROFILE" ] && sed -i "/$MARK_BEGIN/,/$MARK_END/d" "$PROFILE"
    return 0
}

# ---- uninstall
if [ "$UNINSTALL" = 1 ]; then
    if [ -f "$INITTAB.retromenu.bak" ]; then
        mv "$INITTAB.retromenu.bak" "$INITTAB"
        say "restored /etc/inittab (normal login on tty1)"
    fi
    if [ -f "$EXTLINUX.retromenu.bak" ]; then
        mv "$EXTLINUX.retromenu.bak" "$EXTLINUX"
        update-extlinux >/dev/null 2>&1 || true
        say "restored boot loader settings"
    fi
    remove_profile_block
    rm -f "$DOAS_RULE"
    say "autostart removed for $TARGET. Reboot to apply. The program stays at /usr/local/bin/retromenu."
    exit 0
fi

# ---- get the program: release build (default) or Python source
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
ARCH=$(uname -m)
ARCHIVE="pontes-calos-linux-$ARCH-musl.tar.gz"
MODE=source
if [ -n "$BINARY_FILE" ]; then
    cp "$BINARY_FILE" "$TMP/pontes.tar.gz"
    MODE=binary
elif [ "$FROM_SOURCE" = 0 ]; then
    say "downloading the latest release ($ARCHIVE)"
    if wget -q -O "$TMP/pontes.tar.gz" "$RELEASE_URL/$ARCHIVE" 2>/dev/null; then
        if wget -q -O "$TMP/pontes.sha256" "$RELEASE_URL/$ARCHIVE.sha256" 2>/dev/null; then
            want=$(cut -d' ' -f1 "$TMP/pontes.sha256")
            have=$(sha256sum "$TMP/pontes.tar.gz" | cut -d' ' -f1)
            [ "$want" = "$have" ] || die "checksum mismatch on $ARCHIVE, not installing it"
            say "checksum OK"
        fi
        MODE=binary
    else
        warn "no release to download (no release yet, private repository or no network): installing the Python source instead"
    fi
fi

# ---- packages (the release build has Python inside, so it doesn't need python3)
PACKAGES="ncurses-terminfo-base doas less file terminus-font"
[ "$MODE" = binary ] || PACKAGES="python3 $PACKAGES"
say "installing packages ($PACKAGES)"
# shellcheck disable=SC2086
apk add --no-cache $PACKAGES >/dev/null || die "apk failed: check your network and /etc/apk/repositories"

if [ "$MODE" = binary ]; then
    mkdir -p "$TMP/x"
    tar -xzf "$TMP/pontes.tar.gz" -C "$TMP/x" || die "the release archive is damaged"
    [ -x "$TMP/x/pontes/pontes" ] || die "the release archive does not contain the pontes program"
    if [ -z "$ROOT" ]; then
        VERSION=$("$TMP/x/pontes/pontes" --version 2>/dev/null) \
            || die "the release program does not run here (wrong CPU type or C library?). Try --from-source"
    else
        VERSION="PCal (test root)"
    fi
    rm -rf "$LIBDIR.new"
    mkdir -p "$LIBDIR.new"
    mv "$TMP/x/pontes" "$LIBDIR.new/pontes"
    rm -rf "$LIBDIR"
    mv "$LIBDIR.new" "$LIBDIR"
    TARGET_BIN=/usr/lib/pontes-calos/pontes/pontes
    MENU_LINK=$TARGET_BIN LOGIN_LINK=$TARGET_BIN PONTES_LINK=$TARGET_BIN
    PLUGIN_SRC="$LIBDIR/pontes/plugins"
else
    SRC=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)
    mkdir -p "$TMP/src"
    if [ -n "$SRC" ] && [ -f "$SRC/retromenu/pontes.py" ]; then
        say "using the Python source from $SRC"
        cp "$SRC"/retromenu/*.py "$TMP/src/"
        mkdir -p "$TMP/src/plugins"
        cp "$SRC"/retromenu/plugins/*.json "$TMP/src/plugins/" 2>/dev/null || true
    else
        say "downloading the Python source"
        for f in pontes retromenu retrologin; do
            wget -q -O "$TMP/src/$f.py" "$REPO_RAW/retromenu/$f.py" \
                || die "download failed. Is the repository public? You can also git clone it and run distro/install.sh"
        done
        mkdir -p "$TMP/src/plugins"
        wget -q -O "$TMP/src/plugins/network.json" "$REPO_RAW/retromenu/plugins/network.json" 2>/dev/null || true
    fi
    for f in pontes retromenu retrologin; do
        python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$TMP/src/$f.py" \
            || die "$f.py is damaged, not installing it"
    done
    chmod 755 "$TMP"/src/*.py
    rm -rf "$LIBDIR"
    mkdir -p "$LIBDIR"
    mv "$TMP/src" "$LIBDIR/src"
    VERSION="PCal $(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$LIBDIR/src/retromenu.py") (Python source)"
    MENU_LINK=/usr/lib/pontes-calos/src/retromenu.py
    LOGIN_LINK=/usr/lib/pontes-calos/src/retrologin.py
    PONTES_LINK=/usr/lib/pontes-calos/src/pontes.py
    PLUGIN_SRC="$LIBDIR/src/plugins"
fi
mkdir -p "$(dirname "$BIN")" "$SHARE/plugins"
rm -f "$BIN" "$LOGIN_BIN" "$PONTES_BIN"
ln -s "$MENU_LINK" "$BIN"
ln -s "$LOGIN_LINK" "$LOGIN_BIN"
ln -s "$PONTES_LINK" "$PONTES_BIN"
for f in "$PLUGIN_SRC"/*.json; do
    [ -f "$f" ] && cp "$f" "$SHARE/plugins/"
done
say "installed $VERSION -> /usr/local/bin/retromenu, retrologin, pontes"

# ---- reboot / power off from the menu without a password
mkdir -p "$(dirname "$DOAS_RULE")"
cat >"$DOAS_RULE" <<EOF
# RetroMenu: let $TARGET reboot and power off from the menu without a password
permit nopass $TARGET as root cmd reboot
permit nopass $TARGET as root cmd poweroff
EOF
chmod 600 "$DOAS_RULE"

# ---- console: UTF-8 + a font that has the box-drawing characters
mkdir -p "$ROOT/etc/profile.d" "$ROOT/etc/conf.d"
cat >"$ROOT/etc/profile.d/retromenu.sh" <<'EOF'
# RetroMenu: UTF-8, so the box-drawing characters work on the console
[ -n "${LANG:-}" ] || export LANG=C.UTF-8
EOF
if grep -q '^#*[[:space:]]*unicode=' "$ROOT/etc/rc.conf" 2>/dev/null; then
    sed -i 's/^#*[[:space:]]*unicode=.*/unicode="YES"/' "$ROOT/etc/rc.conf"
else
    echo 'unicode="YES"' >>"$ROOT/etc/rc.conf"
fi
if grep -q '^consolefont=' "$ROOT/etc/conf.d/consolefont" 2>/dev/null; then
    sed -i "s/^consolefont=.*/consolefont=\"$FONT\"/" "$ROOT/etc/conf.d/consolefont"
else
    echo "consolefont=\"$FONT\"" >>"$ROOT/etc/conf.d/consolefont"
fi
rc-update add consolefont boot >/dev/null 2>&1 || warn "could not enable the console font service"
say "console set to UTF-8 with the Terminus font"

# ---- start the menu when USER logs in on tty1
touch "$PROFILE"
remove_profile_block
cat >>"$PROFILE" <<EOF
$MARK_BEGIN
# Pontes-Calos home menu on tty1. Other ttys (Alt+F2...) stay a normal login.
if [ "\$(tty)" = /dev/tty1 ] && [ -z "\${RETROMENU:-}" ]; then
    if retromenu; then exit 0; fi
    echo "RetroMenu stopped with an error. This is a normal shell: type 'exit' to restart the menu."
fi
$MARK_END
EOF
touch "$ROOT$HOME_DIR/.hushlogin"  # no "Welcome to Alpine" text between login and menu
chown "$TARGET" "$PROFILE" "$ROOT$HOME_DIR/.hushlogin" 2>/dev/null || true
say "menu starts when $TARGET logs in on tty1"

# ---- what runs on tty1 (busybox init)
set_tty1() {
    [ -f "$INITTAB.retromenu.bak" ] || cp "$INITTAB" "$INITTAB.retromenu.bak"
    if grep -q '^tty1:' "$INITTAB"; then
        sed -i "s|^tty1:.*|tty1::respawn:$1|" "$INITTAB"
    else
        echo "tty1::respawn:$1" >>"$INITTAB"
    fi
}
case "$LOGIN_MODE" in
    screen)
        set_tty1 "/usr/local/bin/retrologin --user $TARGET"
        say "tty1 shows the RetroLogin screen (backup: /etc/inittab.retromenu.bak)"
        HASH=$(awk -F: -v u="$TARGET" '$1 == u { print $2 }' "$ROOT/etc/shadow" 2>/dev/null || true)
        case "$HASH" in
            "" | "!"* | "*"*) warn "$TARGET has no password yet, so the login screen can't let them in. Run: passwd $TARGET" ;;
        esac
        ;;
    auto)
        set_tty1 "/bin/login -f $TARGET"
        warn "tty1 logs in $TARGET WITHOUT a password. Anyone at the keyboard gets in."
        ;;
    plain)
        say "tty1 keeps the normal login prompt"
        ;;
esac

# ---- optional: faster boot
if [ "$FASTBOOT" = 1 ]; then
    if [ -f "$EXTLINUX" ]; then
        [ -f "$EXTLINUX.retromenu.bak" ] || cp "$EXTLINUX" "$EXTLINUX.retromenu.bak"
        sed -i 's/^timeout=.*/timeout=1/' "$EXTLINUX"
        grep -q '^default_kernel_opts=.*quiet' "$EXTLINUX" \
            || sed -i 's/^default_kernel_opts="/default_kernel_opts="quiet /' "$EXTLINUX"
        if update-extlinux >/dev/null 2>&1; then
            say "fast boot: 1s boot menu, quiet kernel"
        else
            mv "$EXTLINUX.retromenu.bak" "$EXTLINUX"
            warn "update-extlinux failed, boot settings left unchanged"
        fi
    else
        warn "no extlinux config (UEFI/GRUB install?), skipping --fast-boot"
    fi
fi

cat <<EOF

  ╔══════════════════════════════════════════════════════╗
  ║              PCAL INSTALLED -- reboot now            ║
  ╚══════════════════════════════════════════════════════╝
  tty1      : $( [ "$LOGIN_MODE" = auto ] && echo "RetroMenu, no password" || echo "login, then RetroMenu" ) ($TARGET)
  Alt+F2    : normal login, if you ever need to fix things
  undo      : sh install.sh --uninstall $TARGET

EOF
