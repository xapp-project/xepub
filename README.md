# Xepub

<img width="1446" height="979" alt="xepub" src="https://github.com/user-attachments/assets/5c6ef2c5-5152-4359-8a7c-4d00bcf209f7" />

EPUB reader for Linux desktops.

Xepub is an XApp, so it works in any desktop and any distro.

## Building from source

### For Mint with mint-dev-tools

```bash
# Install mint-dev-tools
apt install mint-dev-tools
# Remove any previous versions
apt remove xepub
# Build and install from github
mint-build -i -g https://github.com/xapp-project/xepub.git
```

### For Debian distributions (Mint, Ubuntu, etc.) with dpkg-buildpackage

```bash
# Get the source code..
git clone https://github.com/xapp-project/xepub.git
# Go in..
cd xepub
# Install the build dependencies..
sudo apt build-dep --mark-auto .
# Remove any previously built packages
rm -f ../xepub*.deb
# Build
dpkg-buildpackage
# Install
sudo apt install ../xepub*.deb
```

### For other distributions with meson

```bash
# Get the source code..
git clone https://github.com/xapp-project/xepub.git
# Go in..
cd xepub
```

Install the build and runtime dependencies for your distribution. For example:

```bash
# Fedora: sudo dnf install meson ninja-build python3 gettext
# Arch: sudo pacman -S meson ninja python gettext
# openSUSE: sudo zypper install meson ninja python3 gettext-tools
```

The dependencies are listed below (using Debian package names, names may be different in your distribution). Install all of them.

#### Dependencies for building and runtime

```text
python3
```

#### Dependencies for building

```text
gettext
gtk-update-icon-cache
libglib2.0-dev or libgio-2.0-dev
meson
pkg-config
```

#### Dependencies for runtime

```text
gir1.2-gtk-3.0
gir1.2-webkit2-4.1
python3-gi
python3-setproctitle
python3-xapp
xapp-symbolic-icons
```

#### Build and install

```bash
meson setup build --prefix=/usr/local
meson compile -C build
meson test -C build
sudo meson install -C build
```

#### Uninstall

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
