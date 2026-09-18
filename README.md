# Xepub

<img width="1446" height="979" alt="xepub" src="https://github.com/user-attachments/assets/5c6ef2c5-5152-4359-8a7c-4d00bcf209f7" />

EPUB reader for Linux desktops.

Xepub is an XApp, so it works in any desktop and any distro.

## Dependencies

### Runtime Dependencies

```text
gir1.2-gtk-3.0
gir1.2-webkit2-4.1
python3
python3-gi
python3-setproctitle
python3-xapp
xapp-symbolic-icons
```

### Build Dependencies

```text
gettext
gtk-update-icon-cache
libglib2.0-dev | libgio-2.0-dev
meson
pkg-config
python3
```

## Building from source

### For Debian distributions (Mint, Ubuntu, etc.)

```bash
sudo apt build-dep --mark-auto .
dpkg-buildpackage
```

This creates Xepub packages in the parent directory. After installing them, run
`xepub my-book.epub` or open Xepub from the application menu.

### For other distributions

Xepub uses the Meson build system. Install the equivalent build and runtime
dependencies for your distribution. For example:

```bash
# Fedora: sudo dnf install meson ninja-build python3 gettext
# Arch: sudo pacman -S meson ninja python gettext
# openSUSE: sudo zypper install meson ninja python3 gettext-tools
```

### Build and install

```sh
meson setup build --prefix=/usr/local
meson compile -C build
meson test -C build
sudo meson install -C build
```

### Uninstall

To remove a Meson installation while retaining the build directory:

```bash
sudo ninja -C build uninstall
```

## Controls

- Left/Right, Page Up/Page Down, Space/Backspace: turn pages
- Ctrl+Up/Ctrl+Down: change chapters
- Ctrl+O: open; Ctrl+F: find; Ctrl+B: bookmark
- F11: distraction-free fullscreen; Ctrl+Q: quit

## Security

Xepub is built with security in mind.

The security design and measures are documented in [SECURITY.md](SECURITY.md).
