#!/bin/sh
# Pontes-Calos installer: turns a fresh Alpine Linux install into a RetroMenu machine.
#
#   doas sh distro/install.sh [options] [USER]
#   wget -qO- https://raw.githubusercontent.com/bababoy06/Pontes-Calos/main/distro/install.sh | sh
#
# What it does (all reversible with --uninstall):
#   - installs python3 + the few packages the menu uses
#   - installs RetroMenu to /usr/local/bin/retromenu
#   - logs USER in automatically on tty1 and starts the menu there
#     (tty2..tty6 stay normal password logins: your safety net, Alt+F2)
#   - lets USER reboot / power off from the menu without a password (doas)
#   - UTF-8 console with the Terminus font, so the boxes draw properly
set -eu

REPO_RAW="https://raw.githubusercontent.com/bababoy06/Pontes-Calos/main"
ROOT="${ROOT:-}"  # only for testing in a fake root
BIN="$ROOT/usr/local/bin/retromenu"
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
  --no-autologin  install the menu, but log in with a password on tty1 as usual
  --fast-boot     1 second boot menu and quiet kernel messages (extlinux/BIOS installs)
  --uninstall     undo autologin, autostart, doas rule and fast boot (keeps the program)
  -h, --help      this text
EOF
}

AUTOLOGIN=1
FASTBOOT=0
UNINSTALL=0
TARGET=""
for arg in "$@"; do
    case "$arg" in
        --no-autologin) AUTOLOGIN=0 ;;
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
    cp "$SRC"/retromenu/plugins/*.json "$TMP/" 2>/dev/null || true
else
    say "downloading RetroMenu"
    wget -q -O "$TMP/retromenu.py" "$REPO_RAW/retromenu/retromenu.py" \
        || die "download failed. Is the repository public? You can also git clone it and run distro/install.sh"
    wget -q -O "$TMP/network.json" "$REPO_RAW/retromenu/plugins/network.json" 2>/dev/null || true
fi
python3 -c 'import ast, sys; ast.parse(open(sys.argv[1]).read())' "$TMP/retromenu.py" \
    || die "retromenu.py is damaged, not installing it"
mkdir -p "$(dirname "$BIN")" "$SHARE/plugins"
install -m 755 "$TMP/retromenu.py" "$BIN"
for f in "$TMP"/*.json; do
    [ -f "$f" ] && cp "$f" "$SHARE/plugins/"
done
say "installed /usr/local/bin/retromenu (example plugins in /usr/share/retromenu/plugins)"

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
chown "$TARGET" "$PROFILE" 2>/dev/null || true
say "menu starts when $TARGET logs in on tty1"

# ---- autologin on tty1 (busybox init)
if [ "$AUTOLOGIN" = 1 ]; then
    [ -f "$INITTAB.retromenu.bak" ] || cp "$INITTAB" "$INITTAB.retromenu.bak"
    if grep -q '^tty1:' "$INITTAB"; then
        sed -i "s|^tty1:.*|tty1::respawn:/bin/login -f $TARGET|" "$INITTAB"
    else
        echo "tty1::respawn:/bin/login -f $TARGET" >>"$INITTAB"
    fi
    say "tty1 logs in $TARGET automatically (backup: /etc/inittab.retromenu.bak)"
fi

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
  tty1      : RetroMenu ($TARGET)
  Alt+F2    : normal login, if you ever need to fix things
  undo      : sh install.sh --uninstall $TARGET

EOF
