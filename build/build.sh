#!/bin/sh
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
# Build the Pontes-Calos release programs with PyInstaller.
#
#   python3 -m pip install pyinstaller      (once, ideally in a venv)
#   sh build/build.sh
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

PY=${PYTHON:-python3}
"$PY" -m PyInstaller --version >/dev/null 2>&1 || {
    echo "PyInstaller is missing: $PY -m pip install pyinstaller" >&2
    exit 1
}

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
    --paths retromenu --hidden-import retromenu --hidden-import retrologin \
    --workpath build/work --specpath build/work $EXCLUDES

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
