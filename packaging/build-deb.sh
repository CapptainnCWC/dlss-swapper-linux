#!/usr/bin/env bash
# Build a .deb package: ./packaging/build-deb.sh  ->  dist/dlss-swapper-linux_<ver>_all.deb
set -euo pipefail
cd "$(dirname "$0")/.."
VER=$(python3 -c 'import re;print(re.search(r"\"(.+?)\"", open("dlss_swapper/__init__.py").read()).group(1))')
APP=dlss-swapper-linux
ID=io.github.capptainncwc.DlssSwapperLinux
BUILD=$(mktemp -d)
trap 'rm -rf "$BUILD"' EXIT
R="$BUILD/root"

install -d "$R/DEBIAN" "$R/usr/bin" "$R/usr/lib/$APP/dlss_swapper" \
  "$R/usr/share/applications" "$R/usr/share/icons/hicolor/scalable/apps" "$R/usr/share/doc/$APP"
install -m644 dlss_swapper/*.py "$R/usr/lib/$APP/dlss_swapper/"
cat > "$R/usr/bin/$APP" <<'SH'
#!/usr/bin/python3
import sys
sys.path.insert(0, "/usr/lib/dlss-swapper-linux")
from dlss_swapper.cli import main
main()
SH
chmod 755 "$R/usr/bin/$APP"
install -m644 "packaging/$ID.desktop" "$R/usr/share/applications/"
install -m644 "packaging/$ID.svg" "$R/usr/share/icons/hicolor/scalable/apps/"
install -m644 README.md LICENSE "$R/usr/share/doc/$APP/"

find "$R" -type d -exec chmod 755 {} +
SIZE=$(du -sk "$R/usr" | cut -f1)
cat > "$R/DEBIAN/control" <<CTL
Package: $APP
Version: $VER
Section: games
Priority: optional
Architecture: all
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1 (>= 1.5)
Recommends: 7zip | p7zip-full
Suggests: steam
Installed-Size: $SIZE
Maintainer: CapptainnCWC <noreply@github.com>
Homepage: https://github.com/CapptainnCWC/dlss-swapper-linux
Description: DLSS Swapper for Linux / Steam Proton
 Swap DLSS, DLSS Frame Generation, Ray Reconstruction, FSR 3.1 and XeSS
 DLL versions in Steam (Proton) games and install OptiScaler with
 DLSS Frame Generation unlock for GPUs older than RTX 40.
CTL
cat > "$R/DEBIAN/postinst" <<'PI'
#!/bin/sh
set -e
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -f /usr/share/icons/hicolor || true
command -v update-desktop-database >/dev/null && update-desktop-database -q /usr/share/applications || true
PI
chmod 755 "$R/DEBIAN/postinst"
mkdir -p dist
OUT="dist/${APP}_${VER}_all.deb"
fakeroot dpkg-deb --build -Zxz "$R" "$OUT" >/dev/null
echo "$OUT"
