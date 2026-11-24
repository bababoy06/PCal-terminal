#!/bin/sh
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the Pontes-Calos release programs with PyInstaller.
#
#   sh build/build.sh
#
# The first run creates build/.venv with PyInstaller in it (needs internet once),
# so the system Python stays untouched (Alpine refuses a plain `pip install`).
# To use your own Python with PyInstaller instead: PYTHON=/path/to/python sh build/build.sh
#
# Output in dist/release/ (named for this machine's CPU and C library):
#   pontes-calos-linux-ARCH-LIBC.tar.gz   folder build: fastest start, used by the installer
#   pontes-linux-ARCH-LIBC                single file: copy anywhere and run
#   *.sha256                              checksums
#
# A binary only runs on the same C library it was built on: build on Alpine
# for musl (Pontes-Calos), on Debian/Ubuntu for glibc. CI does both.
set -eu
cd "$(dirname "$0")/.."

die() {
    echo "build.sh: $*" >&2
    exit 1
}

if [ -f /etc/alpine-release ]; then
    TOOLS_HINT="apk add python3 py3-pip py3-numpy binutils build-base zlib-dev python3-dev"
else
    TOOLS_HINT="sudo apt install python3-venv binutils   (Debian/Ubuntu)"
fi
command -v python3 >/dev/null 2>&1 || die "python3 is missing. Install the build tools: $TOOLS_HINT"
command -v strip >/dev/null 2>&1 || die "strip is missing. Install the build tools: $TOOLS_HINT"

VENV=build/.venv
if [ -n "${PYTHON:-}" ]; then
    PY=$PYTHON
    "$PY" -m PyInstaller --version >/dev/null 2>&1 || die "PyInstaller is not installed for $PY"
elif python3 -m PyInstaller --version >/dev/null 2>&1; then
    PY=python3
else
    PY=$VENV/bin/python
    if ! "$PY" -m PyInstaller --version >/dev/null 2>&1; then
        echo "first build: setting up $VENV with PyInstaller (one time, needs internet)"
        rm -rf "$VENV"
        # --system-site-packages: the venv also sees the system's numpy (apk add py3-numpy),
        # which the graphical screen needs and PyInstaller then puts in the release
        python3 -m venv --system-site-packages "$VENV" || die "could not create $VENV. Install the build tools: $TOOLS_HINT"
        "$VENV/bin/pip" install --quiet --disable-pip-version-check pyinstaller \
            || die "pip could not install PyInstaller (no internet? missing compiler? $TOOLS_HINT)"
    fi
fi

# A venv made by an older build.sh can't see the system's numpy yet: let it.
if [ "$PY" = "$VENV/bin/python" ] && [ -f "$VENV/pyvenv.cfg" ] && ! "$PY" -c 'import numpy' 2>/dev/null \
    && python3 -c 'import numpy' 2>/dev/null; then
    sed -i 's/^include-system-site-packages = false/include-system-site-packages = true/' "$VENV/pyvenv.cfg"
fi
if "$PY" -c 'import numpy' 2>/dev/null; then
    NUMPY="--hidden-import numpy"
    echo "numpy found: the release includes the graphical screen"
else
    NUMPY=""
    echo "WARNING: numpy not found, so the release's graphical screen falls back to text mode."
    [ -f /etc/alpine-release ] && echo "         Install it and build again:  doas apk add py3-numpy"
fi

VERSION=$("$PY" -c 'import re; print(re.search(r"VERSION = \"([^\"]+)\"", open("retromenu/retromenu.py").read()).group(1))')
ARCH=$(uname -m)
if ldd --version 2>&1 | grep -qi musl; then LIBC=musl; else LIBC=glibc; fi
TARGET="linux-$ARCH-$LIBC"
echo "building pontes-calos $VERSION for $TARGET"

# Leave out the parts of the standard library the programs never use (build/excludes.txt).
EXCLUDES=""
for m in $(sed 's/#.*//' build/excludes.txt); do
    EXCLUDES="$EXCLUDES --exclude-module $m"
done

rm -rf build/work dist
# shellcheck disable=SC2086
set -- --noconfirm --clean --log-level WARN --name pontes --strip \
    --paths retromenu --hidden-import retromenu --hidden-import retrologin --hidden-import retroinstall \
    --hidden-import screen --hidden-import crt --hidden-import vt --hidden-import psf --hidden-import deck --hidden-import title \
    $NUMPY --workpath build/work --specpath build/work $EXCLUDES

"$PY" -m PyInstaller "$@" --onedir --distpath dist/onedir retromenu/pontes.py
"$PY" -m PyInstaller "$@" --onefile --distpath dist/onefile retromenu/pontes.py

mkdir -p dist/release dist/onedir/pontes/plugins
cp retromenu/plugins/*.json dist/onedir/pontes/plugins/ 2>/dev/null || true
tar -C dist/onedir -czf "dist/release/pontes-calos-$TARGET.tar.gz" pontes
cp dist/onefile/pontes "dist/release/pontes-$TARGET"
(
    cd dist/release
    for f in *; do
        case "$f" in *.sha256) continue ;; esac
        sha256sum "$f" >"$f.sha256"
    done
)
ls -lh dist/release
du -sh dist/onedir/pontes
