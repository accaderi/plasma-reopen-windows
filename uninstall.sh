#!/bin/bash
# Removes plasma-reopen-windows for the current user.  Run:  bash uninstall.sh
rm -fv "$HOME/.local/bin/plasma-reopen-windows" \
       "$HOME/.local/share/applications/plasma-reopen-windows-shutdown.desktop" \
       "$HOME/.local/share/applications/plasma-reopen-windows-reboot.desktop" \
       "$HOME/.config/autostart/plasma-reopen-windows-restore.desktop"
kbuildsycoca6 2>/dev/null || true
read -r -p "Also delete settings and the saved session (~/.config/plasma-reopen-windows.conf, ~/.local/state/plasma-reopen-windows)? [y/N] " a
[ "$a" = y ] || [ "$a" = Y ] && rm -rfv "$HOME/.config/plasma-reopen-windows.conf" "$HOME/.local/state/plasma-reopen-windows"
exit 0
