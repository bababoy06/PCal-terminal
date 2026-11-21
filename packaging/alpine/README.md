# PCal as an Alpine package

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
| `/usr/share/licenses/pcal/LICENSE` | GPL-3.0 |

Depends on: `ncurses-terminfo-base doas less file terminus-font`. No `python3` is needed.

The package doesn't set up the login screen or autologin: that stays the job of
`distro/install.sh` (and later the ISO installer), because it is per-machine and per-user.

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

Result: `dist/apk/pcal/x86_64/pcal-0.6-r0.apk`, plus a signed `APKINDEX.tar.gz` next to it.
`dist/apk/pcal` is a complete little apk repository.

Install it on the same machine:

```sh
doas apk add --allow-untrusted dist/apk/pcal/x86_64/pcal-0.6-r0.apk
pontes --version
```

You can also package a downloaded release instead: `sh packaging/alpine/build-apk.sh pontes-calos-linux-x86_64-musl.tar.gz`.
