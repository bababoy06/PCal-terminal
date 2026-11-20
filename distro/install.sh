#!/bin/sh
# Pontes-Calos installer: turns a fresh Alpine Linux install into a RetroMenu machine.
#
#   doas sh distro/install.sh [options] [USER]
#   wget -qO- https://raw.githubusercontent.com/bababoy06/Pontes-Calos/main/distro/install.sh | sh
#
# What it does (all reversible with --uninstall):
#   - installs python3 + the few packages the menu uses
#   - installs RetroMenu to /usr/local/bin/retromenu and RetroLogin to /usr/local/bin/retrologin
#   - shows the retro login screen on tty1; after login the menu starts
#     (tty2..tty6 stay normal password logins: your safety net, Alt+F2)
#   - lets USER reboot / power off from the menu without a password (doas)
#   - UTF-8 console with the Terminus font, so the boxes draw properly
set -eu

REPO_RAW="https://raw.githubusercontent.com/bababoy06/Pontes-Calos/main"
ROOT="${ROOT:-}"  # only for testing in a fake root
BIN="$ROOT/usr/local/bin/retromenu"
LOGIN_BIN="$ROOT/usr/local/bin/retrologin"
SHARE="$ROOT/usr/share/retromenu"
INITTAB="$ROOT/etc/inittab"
DOAS_RULE="$ROOT/etc/doas.d/retromenu.conf"
EXTLINUX="$ROOT/etc/update-extlinux.conf"
MARK_BEGIN="# >>> retromenu autostart >>>"
MARK_END="# <<< retromenu autostart <<<"
FONT="ter-v16n.psf.gz"

say() { printf '\033[32m[pontes-calos]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[pontes-calos]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[pontes-calos] %s\033[0m\n' "$*" >&2; exit 1; }

usage() {
    cat <<EOF
Usage: install.sh [options] [USER]

  USER            account that gets the menu on tty1 (default: first user in /home)
  --plain-login   keep the normal text login on tty1 (the menu still starts after it)
  --autologin     no password at all on tty1 (only for a VM or a kiosk!)
  --fast-boot     1 second boot menu and quiet kernel messages (extlinux/BIOS installs)
  --uninstall     undo autologin, autostart, doas rule and fast boot (keeps the program)
  -h, --help      this text
EOF
}

LOGIN_MODE=screen  # screen | plain | auto
FASTBOOT=0
UNINSTALL=0
TARGET=""
for arg in "$@"; do
    case "$arg" in
        --plain-login | --no-autologin) LOGIN_MODE=plain ;;
        --autologin) LOGIN_MODE=auto ;;
        --fast-boot) FASTBOOT=1 ;;
        --uninstall) UNINSTALL=1 ;;
        -h | --help) usage; exit 0 ;;
        -*) usage; die "unknown option: $arg" ;;
        *) TARGET="$arg" ;;
    esac
done

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

# ---- packages
say "installing packages (python3, terminfo, doas, less, file, terminus font)"
apk add --no-cache python3 ncurses-terminfo-base doas less file terminus-font >/dev/null \
    || die "apk failed: check your network and /etc/apk/repositories"

# ---- the program: from this checkout if we're in one, else from GitHub
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
SRC=$(cd "$(dirname "$0")/.." 2>/dev/null && pwd || true)
if [ -n "$SRC" ] && [ -f "$SRC/retromenu/retromenu.py" ]; then
    say "using RetroMenu from $SRC"
    cp "$SRC/retromenu/retromenu.py" "$TMP/retromenu.py"
    cp "$SRC/retromenu/retrologin.py" "$TMP/retrologin.py"
    cp "$SRC"/retromenu/plugins/*.json "$TMP/" 2>/dev/null || true
else
    say "downloading RetroMenu"
    wget -q -O "$TMP/retromenu.py" "$REPO_RAW/retromenu/retromenu.py" \
        || die "download failed. Is the repository public? You can also git clone it and run distro/install.sh"
    wget -q -O "$TMP/retrologin.py" "$REPO_RAW/retromenu/retrologin.py" || die "download of retrologin failed"
    wget -q -O "$TMP/network.json" "$REPO_RAW/retromenu/plugins/network.json" 2>/dev/null || true
fi
for f in retromenu retrologin; do
    python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$TMP/$f.py" \
        || die "$f.py is damaged, not installing it"
done
mkdir -p "$(dirname "$BIN")" "$SHARE/plugins"
install -m 755 "$TMP/retromenu.py" "$BIN"
install -m 755 "$TMP/retrologin.py" "$LOGIN_BIN"
for f in "$TMP"/*.json; do
    [ -f "$f" ] && cp "$f" "$SHARE/plugins/"
done
say "installed /usr/local/bin/retromenu and /usr/local/bin/retrologin"

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
  ║          PONTES-CALOS INSTALLED -- reboot now        ║
  ╚══════════════════════════════════════════════════════╝
  tty1      : $( [ "$LOGIN_MODE" = auto ] && echo "RetroMenu, no password" || echo "login, then RetroMenu" ) ($TARGET)
  Alt+F2    : normal login, if you ever need to fix things
  undo      : sh install.sh --uninstall $TARGET

EOF
