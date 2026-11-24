#!/bin/bash
# Installs plasma-reopen-windows for the current user (no root needed).
# Run from this folder:  bash install.sh
set -eu
cd "$(dirname "$0")"
[ "$(id -u)" != 0 ] || { echo "Run as your normal user, not with sudo."; exit 1; }

BIN=$HOME/.local/bin/plasma-reopen-windows
LIB=$HOME/.local/lib/plasma-reopen-windows
APPS=$HOME/.local/share/applications
AUTO=$HOME/.config/autostart

missing=""
for c in python3 kstart kdialog notify-send; do command -v $c >/dev/null || missing="$missing $c"; done
python3 -c 'import gi; gi.require_version("Gio", "2.0")' 2>/dev/null || missing="$missing python3-gobject"
[ -z "$missing" ] || { echo "Missing:$missing  (Fedora: sudo dnf install python3-gobject kdialog libnotify)"; exit 1; }

# run the tests first: a broken version is never installed
python3 -m unittest discover -s tests -q || { echo "Tests failed, nothing installed."; exit 1; }
find . -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

rm -rf "$LIB/plasma_reopen_windows"
mkdir -p "$LIB"
cp -r src/plasma_reopen_windows "$LIB/"
install -D -m 755 src/plasma-reopen-windows "$BIN"

# the settings app, only where PySide6 is available
if python3 -c 'import PySide6.QtWidgets' 2>/dev/null; then
    install -D -m 755 src/plasma-reopen-windows-settings "$HOME/.local/bin/plasma-reopen-windows-settings"
    install -D -m 644 desktop/plasma-reopen-windows-settings.desktop "$APPS/plasma-reopen-windows-settings.desktop"
    sed -i "s|@SETTINGS@|$HOME/.local/bin/plasma-reopen-windows-settings|" "$APPS/plasma-reopen-windows-settings.desktop"
    UI=yes
else
    UI=no
fi
for f in shutdown reboot; do
    install -D -m 644 desktop/plasma-reopen-windows-$f.desktop "$APPS/plasma-reopen-windows-$f.desktop"
    sed -i "s|@BIN@|$BIN|" "$APPS/plasma-reopen-windows-$f.desktop"
done
install -D -m 644 desktop/plasma-reopen-windows-restore.desktop "$AUTO/plasma-reopen-windows-restore.desktop"
sed -i "s|@BIN@|$BIN|" "$AUTO/plasma-reopen-windows-restore.desktop"
# settings file: current explanations, your values kept (old file: .old)
"$BIN" config upgrade
update-desktop-database "$APPS" 2>/dev/null || true
kbuildsycoca6 2>/dev/null || true

echo "Installed. In the app menu: \"Save & Shut Down\" and \"Save & Restart\"."
if [ "$UI" = yes ]; then
    echo "Settings app: \"Reopen Windows\" in the app menu."
else
    echo "Settings app not installed: it needs PySide6 (Fedora: sudo dnf install python3-pyside6,"
    echo "Arch: sudo pacman -S pyside6, Debian/Ubuntu: sudo apt install python3-pyside6.qtwidgets); then run this again."
fi
echo "Try it without powering off:  $BIN save   (then: $BIN restore)"
