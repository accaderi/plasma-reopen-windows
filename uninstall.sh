#!/bin/bash
# Removes plasma-reopen-windows for the current user.  Run:  bash uninstall.sh
systemctl --user disable --now plasma-reopen-windows-autosave.timer 2>/dev/null || true
rm -fv "$HOME/.config/systemd/user/plasma-reopen-windows-autosave.service" \
       "$HOME/.config/systemd/user/plasma-reopen-windows-autosave.timer"
systemctl --user daemon-reload 2>/dev/null || true
rm -rfv "$HOME/.local/lib/plasma-reopen-windows"
rm -fv "$HOME/.local/bin/plasma-reopen-windows-settings" "$HOME/.local/share/applications/plasma-reopen-windows-settings.desktop" \
       "$HOME/.local/share/applications"/plasma-reopen-windows-profile-*.desktop
rm -fv "$HOME/.local/bin/plasma-reopen-windows" \
       "$HOME/.local/share/applications/plasma-reopen-windows-shutdown.desktop" \
       "$HOME/.local/share/applications/plasma-reopen-windows-reboot.desktop" \
       "$HOME/.local/share/applications/plasma-reopen-windows-update.desktop" \
       "$HOME/.local/share/applications/plasma-reopen-windows-save-now.desktop" \
       "$HOME/.local/share/applications/plasma-reopen-windows-restore-autosave.desktop" \
       "$HOME/.config/autostart/plasma-reopen-windows-restore.desktop"
kbuildsycoca6 2>/dev/null || true
read -r -p "Also delete settings and the saved session (~/.config/plasma-reopen-windows.conf, ~/.local/state/plasma-reopen-windows)? [y/N] " a
[ "$a" = y ] || [ "$a" = Y ] && rm -rfv "$HOME/.config/plasma-reopen-windows.conf" "$HOME/.local/state/plasma-reopen-windows"
exit 0
