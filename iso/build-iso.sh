#!/bin/sh
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Build the PCal live/install ISO with Alpine's mkimage.
#
#   sh build/build.sh                    # 1. release build
#   sh packaging/alpine/build-apk.sh     # 2. pcal package (dist/apk/pcal)
#   sh iso/build-iso.sh                  # 3. ISO -> dist/iso/pcal-VERSION-ARCH.iso
#   sh iso/build-iso.sh --virt           #    or the small ISO for virtual machines:
#                                        #    dist/iso/pcal-virt-VERSION-ARCH.iso
#
# The normal ISO carries firmware for all kinds of real hardware: building it
# needs about 6 GB of free disk space. The --virt one needs about 1.5 GB.
#
# Run as your normal user (in the abuild group, with an abuild signing key).
# One-time, as root:
#   apk add alpine-sdk alpine-conf syslinux xorriso squashfs-tools grub grub-efi mtools dosfstools git
# Needs internet: the Alpine packages for the image come from the Alpine mirror.
set -eu

die() {
    echo "build-iso.sh: $*" >&2
    exit 1
}

HERE=$(cd "$(dirname "$0")" && pwd)
TOP=$(cd "$HERE/.." && pwd)
ARCH=$(uname -m)
VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$TOP/retromenu/retromenu.py")
REPO=$TOP/dist/apk/pcal
WORK=$TOP/dist/iso-work
OUT=$TOP/dist/iso
MIRROR=${MIRROR:-https://dl-cdn.alpinelinux.org/alpine}
PROFILE=pcal NAME=pcal NEED_MB=6000
case "${1:-}" in
    --virt) PROFILE=pcalvirt NAME=pcal-virt NEED_MB=1500 ;;
    "") ;;
    -h | --help) sed -n '2,/^set -eu/p' "$0" | sed '$d'; exit 0 ;;
    *) echo "usage: build-iso.sh [--virt]" >&2; exit 1 ;;
esac
TOOLS="alpine-sdk alpine-conf syslinux xorriso squashfs-tools grub grub-efi mtools dosfstools git"

[ -f /etc/alpine-release ] || die "build this on Alpine Linux"
[ "$(id -u)" -ne 0 ] || die "run this as your normal user, not root"
id -nG | grep -qw abuild || die "$(id -un) is not in the abuild group. As root: addgroup $(id -un) abuild  (then log out and in)"
ls "$HOME"/.abuild/*.rsa.pub >/dev/null 2>&1 || die "no abuild signing key. Run once: abuild-keygen -a -i -n"
for t in abuild xorriso mksquashfs mformat mkfs.vfat grub-mkimage git; do
    command -v "$t" >/dev/null 2>&1 || die "$t is missing. As root: apk add $TOOLS"
done
[ -f /usr/share/syslinux/isohdpfx.bin ] || die "syslinux is missing. As root: apk add $TOOLS"

# Which Alpine release this machine runs: the ISO uses the same one (v3.22, ... or edge).
# (Only read the number: sourcing /etc/os-release would overwrite NAME and VERSION.)
ALPINE_VERSION=$(cat /etc/alpine-release)
case "$ALPINE_VERSION" in
    *_alpha* | *_beta* | *_rc*) REL=edge BRANCH=master ;;
    *) REL="v${ALPINE_VERSION%.*}" BRANCH="${ALPINE_VERSION%.*}-stable" ;;
esac

APK=$(ls "$REPO/$ARCH"/pcal-"$VERSION"-r*.apk 2>/dev/null | tail -1)
[ -n "$APK" ] && [ -f "$REPO/$ARCH/APKINDEX.tar.gz" ] \
    || die "no pcal $VERSION package in $REPO/$ARCH. Run first: sh packaging/alpine/build-apk.sh"
tar -tzf "$APK" 2>/dev/null | grep -q '^usr/sbin/pcal-live$' \
    || die "$APK is from an older recipe (no pcal-live). Rebuild it: sh packaging/alpine/build-apk.sh"

mkdir -p "$WORK" "$OUT"
if [ ! -f "$WORK/aports/scripts/mkimage.sh" ]; then
    echo "getting Alpine's image scripts (aports $BRANCH)"
    rm -rf "$WORK/aports"
    git clone --quiet --depth=1 --branch "$BRANCH" https://gitlab.alpinelinux.org/alpine/aports.git "$WORK/aports" \
        || die "could not download aports from gitlab.alpinelinux.org"
fi
cp "$HERE/mkimg.pcal.sh" "$HERE/genapkovl-pcal.sh" "$WORK/aports/scripts/"
chmod +x "$WORK/aports/scripts/genapkovl-pcal.sh"

PCAL_PUBKEYS=$(ls "$HOME"/.abuild/*.rsa.pub)
export PCAL_PUBKEYS

rm -rf "$WORK/tmp" "$WORK/scratch"  # leftovers from an earlier (failed) build
FREE_MB=$(df -Pk "$WORK" | awk 'NR == 2 { print int($4 / 1024) }')
if [ "$FREE_MB" -lt "$NEED_MB" ]; then
    hint="make the VM's disk bigger (see iso/README.md)"
    [ "$PROFILE" = pcal ] && hint="$hint, or build the small VM-only ISO: sh iso/build-iso.sh --virt"
    die "only $FREE_MB MB free, this ISO needs about $NEED_MB MB while building. $hint"
fi

echo "building the $NAME $VERSION ISO for $ARCH on Alpine $REL ($FREE_MB MB free; this takes a few minutes)"
mkdir -p "$WORK/tmp" "$WORK/scratch"
touch "$WORK/started"
(
    cd "$WORK/aports/scripts"
    # The kernel step unpacks all firmware into $TMPDIR (default /tmp, which can be a
    # small RAM disk). Keep it on the disk next to the other work files instead.
    export TMPDIR="$WORK/scratch"
    sh mkimage.sh \
        --tag "$VERSION" \
        --arch "$ARCH" \
        --profile "$PROFILE" \
        --outdir "$OUT" \
        --workdir "$WORK/tmp" \
        --hostkeys \
        --repository "$MIRROR/$REL/main" \
        --repository "$MIRROR/$REL/community" \
        --repository "$REPO"
) || {
    rm -rf "$WORK/tmp" "$WORK/scratch"
    die "mkimage failed (see the messages above)"
}
rm -rf "$WORK/tmp" "$WORK/scratch"  # the unpacked build files take gigabytes

ISO=$(find "$OUT" -maxdepth 1 -name '*.iso' -newer "$WORK/started" | head -1)
[ -n "$ISO" ] || die "mkimage finished but no new ISO is in $OUT"
FINAL="$OUT/$NAME-$VERSION-$ARCH.iso"
[ "$ISO" = "$FINAL" ] || mv "$ISO" "$FINAL"
(cd "$OUT" && sha256sum "$(basename "$FINAL")" >"$(basename "$FINAL").sha256")
echo
echo "done: $FINAL ($(du -h "$FINAL" | cut -f1))"
echo "try it in a VM: boot it, and the PCal menu starts on its own."
