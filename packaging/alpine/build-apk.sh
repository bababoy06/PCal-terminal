#!/bin/sh
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build the PCal Alpine package (.apk) from a release build.
#
#   sh build/build.sh                       # first make the release build
#   sh packaging/alpine/build-apk.sh        # then package it
#   sh packaging/alpine/build-apk.sh FILE   # or package a downloaded release archive
#
# Run it as your normal user (abuild refuses root). One-time setup, as root:
#   apk add alpine-sdk && addgroup YOURUSER abuild
# and then as YOURUSER (log out and in after addgroup):
#   abuild-keygen -a -i -n
#
# Output: dist/apk/pcal/ARCH/pcal-VERSION-r0.apk plus a signed APKINDEX,
# so dist/apk/pcal can be used as an apk repository.
set -eu

die() {
    echo "build-apk.sh: $*" >&2
    exit 1
}

HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/../.." && pwd)
ARCH=$(uname -m)
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$TOP/retromenu/retromenu.py")
ARCHIVE=${1:-$TOP/dist/release/pontes-calos-linux-$ARCH-musl.tar.gz}
OUT=$TOP/dist/apk

[ -f /etc/alpine-release ] || die "build this on Alpine Linux"
[ "$(id -u)" -ne 0 ] || die "run this as your normal user, not root (abuild refuses root)"
command -v abuild >/dev/null 2>&1 || die "abuild is missing. As root: apk add alpine-sdk && addgroup $(id -un) abuild"
id -nG | grep -qw abuild || die "$(id -un) is not in the abuild group. As root: addgroup $(id -un) abuild  (then log out and in)"
ls "$HOME"/.abuild/*.rsa >/dev/null 2>&1 || die "no signing key yet. Run once: abuild-keygen -a -i -n"
[ -f "$ARCHIVE" ] || die "no release build at $ARCHIVE. Run sh build/build.sh first, or pass the archive file"
[ -n "$VERSION" ] || die "could not read VERSION from retromenu/retromenu.py"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# abuild names the repository after the folder above the recipe: WORK/pcal/pcal/APKBUILD -> repo "pcal"
REC=$WORK/pcal/pcal
mkdir -p "$REC" "$WORK/distfiles"
sed -e "s/^pkgver=.*/pkgver=$VERSION/" -e "s/^arch=.*/arch=\"$ARCH\"/" "$HERE/APKBUILD" >"$REC/APKBUILD"
cp "$HERE/pcal.profile.sh" "$REC/"
cp "$TOP/LICENSE" "$REC/"
# Put the archive where abuild looks for downloads, so nothing is fetched (works for a private repo too)
cp "$ARCHIVE" "$WORK/distfiles/pontes-calos-linux-$ARCH-musl-$VERSION.tar.gz"

echo "packaging PCal $VERSION for $ARCH"
cd "$REC"
export SRCDEST="$WORK/distfiles" REPODEST="$OUT"
abuild checksum
abuild -P "$OUT"

APK=$(ls "$OUT/pcal/$ARCH/pcal-$VERSION"-r*.apk 2>/dev/null | tail -1)
[ -n "$APK" ] || die "abuild finished but no package was found in $OUT/pcal/$ARCH"
echo
echo "done: $APK"
echo "install it here with:  doas apk add --allow-untrusted $APK"
