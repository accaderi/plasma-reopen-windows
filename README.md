# Reopen windows when logging back in, for KDE Plasma

**Shut down or restart, and get your apps and windows back.** Every Chrome
window with its tabs, every VS Code window, Konsole tabs in their folders,
Dolphin, each on its screen, in its place, in the same order on top of each
other. Like macOS's *"Reopen windows when logging back in"*, which KDE Plasma
on Wayland does not do. The restart-proof alternative to hibernation.

Adds two entries to the app menu: **Save & Shut Down** and **Save & Restart**.

---

## Contents

1. [Why](#why)
2. [Hibernation or Save & Shut Down?](#hibernation-or-save--shut-down)
3. [What comes back](#what-comes-back)
4. [Tested on](#tested-on)
5. [Install](#install)
6. [Use](#use)
7. [Settings](#settings)
8. [How it works](#how-it-works)
9. [Troubleshooting](#troubleshooting)
10. [Uninstall](#uninstall)
11. [License](#license)

---

## Why

On a normal log-out, Plasma closes the windows of every app **one by one**.
Apps that restore their own windows (Chrome, VS Code, Dolphin) then only
remember the window that was closed last, so after a restart you get one
Chrome window instead of three. On Wayland, apps also cannot place their own
windows, so whatever comes back lands stacked in the middle of one screen,
and Plasma's own session restore does not bring back most apps at all.

This tool does it the other way round: it records every window, quits each
app **as a whole** (as Ctrl+Shift+Q does in Chrome), so it saves all its
windows, then shuts down. At the next login it starts the apps again and
puts every window back.

## Hibernation or Save & Shut Down?

| | Hibernation | Save & Shut Down |
|---|---|---|
| What comes back | everything, exactly (running programs, terminal output, unsaved work) | apps and windows; the content as far as each app restores it |
| Kernel or system updates | not applied | applied (it is a real restart) |
| Setup | swap, kernel and driver settings; hardware dependent | none, per user |
| Speed | about 1–2 minutes | apps take their normal start time |

Use hibernation for "continue exactly here", Save & Shut Down / Restart for
updates and whenever hibernation is not available. For hibernation on an
MSI Bravo 15 C7VF (AMD + NVIDIA) with Fedora, see
[msi-bravo15-c7vf-fedora-hibernation-fix](https://github.com/accaderi/msi-bravo15-c7vf-fedora-hibernation-fix).

## What comes back

| | |
|---|---|
| **Chrome** (also Chromium, Brave, Edge, Vivaldi) | all windows with their tabs |
| **VS Code** (also VSCodium) | all windows; unsaved changes through VS Code's own backup |
| **Konsole** | every window with its tabs, each tab in its folder |
| **Dolphin** | its folder, and tabs through Dolphin's own "restore last session" |
| **Any other app** with a menu entry | started again; what it shows depends on the app |
| **Window placement** | screen, position, size, virtual desktop, minimized/maximized/full screen |
| **Window order** | which window lies on top of which, on every screen |
| **Videos** (YouTube and most sites) | the visible tab's video at the same position, paused if it was paused; the sound stays off until then |
| **Sound output** | the same device again, found by name (HDMI/DisplayPort monitors swap numbers between boots) |

Not possible: programs running inside a terminal, terminal scrollback, and
the inside state of apps that do not save it themselves. For that, use
hibernation.

**Safety:** nothing is ever killed. If an app does not close by itself (for
example because it asks to save a document), or a program is still running
in a Konsole tab, the shutdown is cancelled with a notification, and the apps
closed so far are opened again.

## Tested on

Fedora 44, KDE Plasma 6.7 on Wayland, three screens (laptop + 2 monitors),
2026-11-21. Tested apps: Chrome (3 windows, one profile), VS Code (2 windows),
Konsole (2 windows, several tabs), Dolphin (one window), YouTube in Chrome,
and with the KWin script "Remember Window Positions" both on and off.

**Not tested yet:** several Chrome profiles open at once, apps that ask to
save a document (the cancel path), Firefox (not handled specially yet, so it may come back
with only one window), Electron apps such as Obsidian, Slack or Discord,
X11 sessions, other distributions. It should work on any Plasma 6 desktop;
reports are welcome.

## Install

Needs Python 3 with PyGObject, `kdialog` and `notify-send` (on Fedora KDE
all present by default; otherwise `sudo dnf install python3-gobject kdialog
libnotify`).

```bash
git clone https://github.com/accaderi/plasma-reopen-windows.git
cd plasma-reopen-windows
bash install.sh
```

Installs for your user only, no root needed:

- `~/.local/bin/plasma-reopen-windows`: the tool
- "Save & Shut Down" and "Save & Restart" in the app menu
- `~/.config/autostart/plasma-reopen-windows-restore.desktop`: restores at login
- `~/.config/plasma-reopen-windows.conf`: settings, all commented out

Try it once without powering off:

```bash
plasma-reopen-windows save      # closes the apps and saves the session
plasma-reopen-windows restore   # opens them again
```

## Use

- App menu → **Save & Shut Down** or **Save & Restart** (search "Save").
  A dialog lists the open apps; confirm, and they close within a few seconds,
  then the computer shuts down or restarts.
- After the next login, wait 10–30 s: the apps start one after another and
  the windows move into place.
- Want a key for it? System Settings → Keyboard → Shortcuts → Add
  Application → Save & Shut Down. KDE warns if the keys are taken. There is
  no shortcut by default.
- The normal **Shut Down** in the menu is unchanged and does not save
  anything: Plasma starts closing windows before any tool can step in.

### Commands

```bash
plasma-reopen-windows status    # what is saved, changes nothing
plasma-reopen-windows windows   # open windows as the tool sees them (app ids for "exclude")
plasma-reopen-windows save chrome konsole   # save and close only these (for testing)
cat ~/.local/state/plasma-reopen-windows/log
```

## Settings

`~/.config/plasma-reopen-windows.conf`:

| Setting | Default | Meaning |
|---|---|---|
| `confirm` | yes | ask before closing everything |
| `place_windows` | auto | put windows back in place: yes / no / auto (= leave it to "Remember Window Positions" when that KWin script is on, so the two do not fight) |
| `restore_stacking` | yes | put windows back in the same order on top of each other |
| `close_timeout` | 30 | seconds an app may take to close before the shutdown is cancelled |
| `restore_timeout` | 120 | seconds to wait for windows after login |
| `max_age_days` | 7 | older saved sessions are not restored |
| `exclude` | empty | app ids to leave alone, space separated (see `plasma-reopen-windows windows`) |

## How it works

1. **Save:** a small KWin script lists every window (app, screen, geometry,
   desktop, state, stacking order). Konsole reports each tab's folder over
   D-Bus. Playing or paused videos are read over MPRIS, the default sound
   output from PipeWire.
2. **Close:** each app is quit as a whole: browsers and VS Code with SIGTERM
   (their clean quit), Dolphin with its Quit action, Konsole after its tabs
   are recorded, other apps by closing their windows like the ✕ button.
3. **Shut down / restart** through Plasma (`org.kde.Shutdown`), so Plasma
   saves nothing of its own on top.
4. **Restore at login:** starts the apps (Konsole with its tabs), matches the
   new windows to the saved ones by app and title, moves them back, restores
   the stacking order, seeks and pauses the videos, and selects the sound
   output again.

## Troubleshooting

| What you see | What to do |
|---|---|
| "Shutdown cancelled: X did not close" | X probably asks to save something: answer it or close X, then try again |
| "Programs are still running in Konsole" | finish or stop them, then try again |
| Apps came back, windows not in place | is "Remember Window Positions" on? Then it places the windows; set `place_windows = yes` and turn that script off to use this tool's placement |
| Nothing came back | `plasma-reopen-windows status` and the log; the autostart entry must exist in `~/.config/autostart/` |
| An app should not be touched | add its id to `exclude` |

## Uninstall

```bash
bash uninstall.sh
```

Removes the tool, the menu entries and the autostart entry, and asks before
deleting the settings and the saved session.

## License

MIT, see [LICENSE](LICENSE).
