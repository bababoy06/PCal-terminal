# PCal as an Alpine package

> Released packages are on the [releases page](https://github.com/bababoy06/PCal-terminal/releases);
> see the [installation guide](../../docs/installation.md#5-install-on-an-existing-alpine-system).
> Building is covered in the [development guide](../../docs/development.md#4-build-the-release-program-and-the-package).

This turns the PCal release build into an ordinary Alpine package, `pcal-VERSION-r0.apk`.
It's step 1 towards the PCal ISO: Alpine's ISO builder (`mkimage`) puts packages on the image,
so PCal has to be one. Bonus: once PCal is a package, updating is just `apk upgrade`.

## What the package contains

| Path | What |
|---|---|
| `/usr/lib/pcal/pontes/` | the release build (PCal with its own Python inside) |
| `/usr/bin/pontes`, `retromenu`, `retrologin` | links to it |
| `/usr/share/retromenu/plugins/` | example plugins |
| `/etc/profile.d/pcal.sh` | UTF-8 locale for the console |
| `/usr/sbin/pcal-setup` | the installer (`distro/install.sh`): login screen, menu autostart, doas rule, console font |
| `/usr/sbin/pcal-install` | the PCal installer on the live ISO (`pcal-install --dry-run` to just look) |
| `/usr/sbin/pcal-live` | tty1 of the live ISO (falls back to a normal login once installed) |
| `/usr/share/licenses/pcal/LICENSE` | GPL-3.0 |

Depends on: `ncurses-terminfo-base doas less file terminus-font`. No `python3` is needed.

Installing the package doesn't change your login yet: run `pcal-setup` once (as root) for that,
because it is per-machine and per-user. It sees the installed package and only does the system setup.

## Build it (on Alpine)

One-time setup:

```sh
# as root
apk add alpine-sdk
addgroup jules abuild          # your normal user; log out and back in afterwards
# as jules
abuild-keygen -a -i -n         # creates your package signing key
```

Then, as your normal user, in the repository:

```sh
sh build/build.sh                    # the release build (if you don't have it yet)
sh packaging/alpine/build-apk.sh     # the package
```

Result: `dist/apk/pcal/x86_64/pcal-VERSION-rNUMBER.apk`, where NUMBER is the build time (e.g. `r2611221432`), so each build is newer than the last, plus a signed `APKINDEX.tar.gz` next to it.
`dist/apk/pcal` is a complete little apk repository.

Install it on the same machine:

```sh
doas apk add --allow-untrusted dist/apk/pcal/x86_64/pcal-VERSION-rNUMBER.apk   # the file build-apk.sh printed;
                                                                         # installs, or upgrades an older build
pontes --version
```

You can also package a downloaded release instead: `sh packaging/alpine/build-apk.sh pontes-calos-linux-x86_64-musl.tar.gz`.
