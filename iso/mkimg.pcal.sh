# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
#
# PCal ISO profiles for Alpine's mkimage.sh. iso/build-iso.sh copies this file
# into aports/scripts/ and runs mkimage.sh with one of them:
#   --profile pcal       Alpine "standard": real PCs (all firmware, big build)
#   --profile pcalvirt   Alpine "virt": virtual machines only (small and fast)

_pcal_common() {
	arch="x86_64"
	image_ext="iso"
	output_format="iso"
	kernel_cmdline="$kernel_cmdline quiet"
	apks="$apks pcal"
	apkovl="genapkovl-pcal.sh"
	hostname="pcal"
}

profile_pcal() {
	profile_standard
	_pcal_common
	title="PCal"
	desc="PCal (Pontes-Calos): Alpine Linux with a retro green-CRT home menu.
		Boots into the PCal menu; choose Install PCal to put it on disk."
	profile_abbrev="pcal"
}

profile_pcalvirt() {
	profile_virt
	_pcal_common
	title="PCal (virtual machines)"
	desc="PCal for virtual machines: small kernel, no hardware firmware."
	profile_abbrev="pcalvirt"
}
