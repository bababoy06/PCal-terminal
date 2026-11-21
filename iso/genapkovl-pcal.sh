#!/bin/sh -e
# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Builds pcal.apkovl.tar.gz: the settings layered on top of the PCal live ISO.
# Called by mkimage.sh (under fakeroot) as: genapkovl-pcal.sh HOSTNAME
# PCAL_PUBKEYS (set by iso/build-iso.sh): public keys that signed the pcal package.

HOSTNAME="$1"
if [ -z "$HOSTNAME" ]; then
	echo "usage: $0 hostname"
	exit 1
fi

cleanup() {
	rm -rf "$tmp"
}

makefile() {
	OWNER="$1"
	PERMS="$2"
	FILENAME="$3"
	cat >"$FILENAME"
	chown "$OWNER" "$FILENAME"
	chmod "$PERMS" "$FILENAME"
}

rc_add() {
	mkdir -p "$tmp"/etc/runlevels/"$2"
	ln -sf /etc/init.d/"$1" "$tmp"/etc/runlevels/"$2"/"$1"
}

tmp="$(mktemp -d)"
trap cleanup EXIT

mkdir -p "$tmp"/etc/apk/keys "$tmp"/etc/network "$tmp"/etc/conf.d

makefile root:root 0644 "$tmp"/etc/hostname <<EOF
$HOSTNAME
EOF

makefile root:root 0644 "$tmp"/etc/network/interfaces <<EOF
auto lo
iface lo inet loopback

auto eth0
iface eth0 inet dhcp
EOF

# packages of the live system (and, after setup-alpine, of the installed one)
makefile root:root 0644 "$tmp"/etc/apk/world <<EOF
alpine-base
pcal
EOF

# trust the key that signed the pcal package
for key in ${PCAL_PUBKEYS:-}; do
	[ -f "$key" ] && install -m644 "$key" "$tmp"/etc/apk/keys/
done

# tty1: the PCal menu (only while live; see /usr/sbin/pcal-live). tty2-6: normal logins.
makefile root:root 0644 "$tmp"/etc/inittab <<EOF
# /etc/inittab (PCal)

::sysinit:/sbin/openrc sysinit
::sysinit:/sbin/openrc boot
::wait:/sbin/openrc default

tty1::respawn:/usr/sbin/pcal-live
tty2::respawn:/sbin/getty 38400 tty2
tty3::respawn:/sbin/getty 38400 tty3
tty4::respawn:/sbin/getty 38400 tty4
tty5::respawn:/sbin/getty 38400 tty5
tty6::respawn:/sbin/getty 38400 tty6

#ttyS0::respawn:/sbin/getty -L 115200 ttyS0 vt100

::ctrlaltdel:/sbin/reboot
::shutdown:/sbin/openrc shutdown
EOF

# UTF-8 console with a font that has the box-drawing characters
makefile root:root 0644 "$tmp"/etc/rc.conf <<EOF
# /etc/rc.conf (PCal): see /usr/share/openrc/support for all options
unicode="YES"
EOF
makefile root:root 0644 "$tmp"/etc/conf.d/consolefont <<EOF
consolefont="ter-v16n.psf.gz"
EOF

# the live menu: an "Install PCal" entry (lives in /root, which the installer doesn't copy)
mkdir -p "$tmp"/root/.config/retromenu/plugins
makefile root:root 0644 "$tmp"/root/.config/retromenu/plugins/00-install.json <<'EOF'
{
  "name": "Install PCal",
  "description": "Put PCal on this computer's disk",
  "command": "setup-alpine && printf '\\n  PCal is on the disk. Remove the ISO, reboot, log in as root and run:  pcal-setup\\n  (it turns on the PCal login screen and menu for your user)\\n'",
  "pause": true
}
EOF
makefile root:root 0644 "$tmp"/root/.config/retromenu/config.json <<EOF
{"effects": "full", "splash": true}
EOF

rc_add devfs sysinit
rc_add dmesg sysinit
rc_add mdev sysinit
rc_add hwdrivers sysinit
rc_add modloop sysinit

rc_add hwclock boot
rc_add modules boot
rc_add sysctl boot
rc_add hostname boot
rc_add bootmisc boot
rc_add syslog boot
rc_add networking boot
rc_add consolefont boot

rc_add mount-ro shutdown
rc_add killprocs shutdown
rc_add savecache shutdown

tar -c -C "$tmp" etc root | gzip -9n >"$HOSTNAME".apkovl.tar.gz
