# Copyright (C) 2026 bababoy06
# SPDX-License-Identifier: GPL-3.0-or-later
#
# PCal ISO profile for Alpine's mkimage.sh. iso/build-iso.sh copies this file
# into aports/scripts/ and runs: mkimage.sh --profile pcal
profile_pcal() {
	profile_standard
	title="PCal"
	desc="PCal (Pontes-Calos): Alpine Linux with a retro green-CRT home menu.
		Boots into the PCal menu; choose Install PCal to put it on disk."
	profile_abbrev="pcal"
	arch="x86_64"
	image_ext="iso"
	output_format="iso"
	kernel_cmdline="$kernel_cmdline quiet"
	apks="$apks pcal"
	apkovl="genapkovl-pcal.sh"
	hostname="pcal"
}
