# Xepub

<img width="1446" height="979" alt="xepub" src="https://github.com/user-attachments/assets/5c6ef2c5-5152-4359-8a7c-4d00bcf209f7" />

EPUB reader for Linux desktops.

Xepub is an XApp, so it works in any desktop and any distro.

## Build and run

### Install build dependencies (Debian/Ubuntu)

```sh
sudo apt install meson gettext debhelper python3 python3-gi \
gir1.2-gtk-3.0 gir1.2-webkit2-4.1 python3-xapp python3-setproctitle
```

### Build and run from source

```sh
meson setup build
meson test -C build
meson install -C build
xepub my-book.epub
```

To install to `~/bin` instead of the system prefix:

```sh
rm -rf build
meson setup build --prefix=$HOME/.local --bindir=$HOME/bin
meson install -C build
```

### Build a Debian package

The resulting `.deb` is written to the parent directory.

```sh
dpkg-buildpackage -us -uc -b
```

## Controls

- Left/Right, Page Up/Page Down, Space/Backspace: turn pages
- Ctrl+Up/Ctrl+Down: change chapters
- Ctrl+O: open; Ctrl+F: find; Ctrl+B: bookmark
- F11: distraction-free fullscreen; Ctrl+Q: quit

## Security

Xepub is built with security in mind.

The security design and measures are documented in [SECURITY.md](SECURITY.md).
