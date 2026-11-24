# Reopen windows when logging back in, for KDE Plasma

**Shut down or restart, and get your apps and windows back.** Every Chrome
window with its tabs, every VS Code window, Konsole tabs in their folders,
Dolphin, each on its screen, in its place, in the same order on top of each
other. Like macOS's *"Reopen windows when logging back in"*, which KDE Plasma
on Wayland does not do. The restart-proof alternative to hibernation.

Adds entries to the app menu: **Save & Shut Down**, **Save & Restart** and
**Save & Update & Restart** (installs the system updates during the restart).

---

## Contents

1. [Why](#why)
2. [Hibernation or Save & Shut Down?](#hibernation-or-save--shut-down)
3. [What comes back](#what-comes-back)
4. [Tested on](#tested-on)
5. [Install](#install)
6. [Use](#use)
7. [The settings app](#the-settings-app)
8. [Profiles](#profiles)
9. [Autosave](#autosave)
10. [Save & Update & Restart](#save--update--restart)
11. [At login](#at-login)
12. [Settings](#settings)
13. [How it works](#how-it-works)
14. [Troubleshooting](#troubleshooting)
15. [Uninstall](#uninstall)
16. [Development](#development)
17. [License](#license)

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

| | Hibernation | Save & Shut Down / Restart |
|---|---|---|
| What comes back | everything, exactly: running programs, terminal output, unsaved work, every tab and every video | apps and windows on their screens, in their order; the content as far as each app restores it (browser tabs, open files, terminal folders); one video per browser at its position |
| Unsaved work | kept as it is | the app asks before closing; the shutdown waits until you answer |
| Limit | the image must fit in about half of the RAM; what decides is the memory that cannot be swapped out, mostly the graphics drivers' (2–3 GB with three screens on a 16 GB laptop), so it can be refused at one try and work at the next | none |
| When it goes wrong | refused: nothing happens, you keep working. Rarely the machine hangs while writing the image: then the session is lost (once in about 40 attempts while testing) | an app that does not close stops the shutdown, and the apps closed so far open again |
| Kernel and system updates | not applied | applied: it is a real restart |
| Setup | swap partition at least as big as the RAM, kernel and driver settings, Secure Boot off; depends on the hardware | per user, no system changes |
| Time | about 1 minute to hibernate and 1 to wake | a normal shutdown; after login the apps take their normal start time |
| Power while off | none | none |

Use hibernation for "continue exactly here" after a short break. Use Save &
Shut Down / Restart for updates, longer breaks, and whenever hibernation is
refused or not available. With both installed: hibernate, and if it is
refused, Save & Shut Down.

For hibernation on an
MSI Bravo 15 C7VF (AMD + NVIDIA) with Fedora, see
[msi-bravo15-c7vf-fedora-hibernation-fix](https://github.com/accaderi/msi-bravo15-c7vf-fedora-hibernation-fix).

## What comes back

| | |
|---|---|
| **Chrome** (also Chromium, Brave, Edge, Vivaldi) | all windows with their tabs |
| **VS Code** (also VSCodium) | all windows; unsaved changes through VS Code's own backup |
| **Konsole** | every window with its tabs, each tab in its folder |
| **Dolphin** | its folder, and tabs through Dolphin's own "restore last session" |
| **Firefox** | all windows with their tabs (the tool makes Firefox restore its last session once, whatever its startup setting) |
| **VLC** and other media players that report their file | the file reopened, at the same position, paused if it was paused |
| **Gwenview, Okular, Kate, KWrite** | the open file, found from the window title and the recent-files list |
| **DaVinci Resolve** | started again (last, 15 s after the others); with its own "load last project" setting on, the project opens |
| **Any other app** with a menu entry | started again; what it shows depends on the app |
| **Window placement** | screen, position, size, virtual desktop, minimized/maximized/full screen |
| **Window order** | which window lies on top of which, on every screen |
| **Videos** (YouTube and most sites) | the visible tab's video at the same position, paused if it was paused; the sound stays off until then |
| **Sound output** | the same device again, found by name (HDMI/DisplayPort monitors swap numbers between boots) |

Not possible: programs running inside a terminal, terminal scrollback, and
the inside state of apps that do not save it themselves. For that, use
hibernation.

### Limits

There is no memory limit (it is a real shutdown, not a copy of RAM), so any
number of apps is fine. The practical limits:

- Every app gets 30 s to close (`close_timeout`; DaVinci Resolve 120 s). An
  app that asks "save changes?" stops the shutdown until you answer.
- A program still running in a Konsole tab stops the shutdown too, on purpose.
- After login the tool waits up to 2 minutes for windows (`restore_timeout`);
  a window that takes longer appears, but is not placed.
- Apps without a menu entry (some AppImages, scripts) are not started again.
- Never touched, so never saved, closed, moved or started again: the parts
  of Plasma itself (panels, KRunner, KWin, the password wallet, Spectacle,
  …) and the **Reopen Windows** settings app. It stays where you put it while
  its commands run, and it does not open by itself after a restart: open it
  from the app menu when you need it. Your own additions:
  `exclude` (see [Settings](#settings)).

**Safety:** nothing is ever killed. If an app does not close by itself (for
example because it asks to save a document), or a program is still running
in a Konsole tab, the shutdown is cancelled with a notification, and the apps
closed so far are opened again.

## Tested on

Fedora 44, KDE Plasma 6.7 on Wayland, three screens (laptop + 2 monitors),
2026-11-21 and 22. A real Save & Restart brought back 11 apps with 15 windows:
Chrome (3 windows, one profile), Firefox (2 windows), VS Code, Konsole
(2 windows), Dolphin, DaVinci Resolve, VLC (file reopened, paused), Gwenview,
qBittorrent, OBS Studio, System Settings, plus a paused YouTube video and a
Bluetooth speaker as sound output. Window placement tested with the KWin script
"Remember Window Positions" both on and off.

**Not tested yet:** several Chrome profiles open at once, apps that ask to
save a document (the cancel path), Okular/Kate/KWrite reopening a file,
Electron apps such as Obsidian, Slack or Discord (they may come back with only
one window), X11 sessions, other distributions. It should work on any Plasma 6
desktop; reports are welcome.

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

- `~/.local/bin/plasma-reopen-windows` and `~/.local/lib/plasma-reopen-windows/`: the tool
- "Save & Shut Down" and "Save & Restart" in the app menu, and "Save & Update &
  Restart" where PackageKit is installed (`pkcon`; Fedora, openSUSE, Ubuntu, ...)
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

## The settings app

If PySide6 is installed (Fedora: `sudo dnf install python3-pyside6`), the
installer adds **Reopen Windows** to the app menu. Everything in it can also be
done with the command line; the app only reads and writes the same files.

- **Overview**: the saved session, and the buttons Save & Shut Down, Save &
  Restart, Save & Update & Restart, Save without restarting, Restore now.
- **Profiles**: a drawing of your screens with the profile's windows (each
  app in its own color, numbers = layer on each screen; pointing at a row of
  the tables highlights its windows); switch apps on/off, start delays, the
  pages of each browser window; New from screen, Write over from screen,
  **Unite Screen and Existing**, **Reload Existing**, **Arrange Existing on
  Screen**, **Undo**, Save changes, Discard changes, Use at login, Rename, Import/Export,
  Delete. Unticking an app switches it off (kept, greyed out); the trash can
  removes it and its windows from the profile. Edits are saved together with
  **Save changes** (blue while something is unsaved), which then reloads the
  profile onto the screens (switched-off and removed apps close, ticked ones
  start, windows go to their places); **Discard changes** forgets unsaved
  edits. The undo button names what it takes back (**Undo Save changes**,
  **Undo Unite**, **Undo Reload**, **Undo Arrange**). Like in an editor, undo
  never saves: it puts the screens back, and if the profile had changed, its
  previous version is shown as unsaved edits, to keep with Save changes or
  drop with Discard changes (which then reloads the saved version onto the
  screens, so the window and the screens always show the same). Every button explains itself in a tooltip. After
  a command, the app comes back on top.
- **Settings**: every setting with its explanation; the file's comments are kept.
- **Where to show it**: per entry (Save & Shut Down, Save & Restart, Save &
  Update & Restart, each
  profile, the app itself) a favorite in the Application Launcher, a desktop
  icon, a panel button, and a keyboard shortcut (a combination used elsewhere is
  refused, with its owner). The fixed power row of the Application Launcher
  (Sleep, Restart, Shut Down) is built into Plasma and cannot be extended.
- **Log**, with "Copy for an issue report".

Without PySide6 nothing is missing except this window.

## Profiles

A profile is a named arrangement of apps and windows, like "Work" or "Video
editing". Saving one closes nothing; loading one does not need a restart.

```bash
plasma-reopen-windows profile save Work      # what is open now, with every window's place
                                             # (asks before replacing an existing "Work")
plasma-reopen-windows profile load Work      # unite or reload (setting profile_load)
plasma-reopen-windows profile list
```

| Command | What it does |
|---|---|
| `unite NAME` | **Unite Screen and Existing**: both ways. Starts the profile's apps that are not open, opens its missing windows and puts all its windows in place; and adds the windows that are open but not in the profile to it, where they are (an app switched off but open is switched on). Closes nothing |
| `reload NAME` | **Reload Existing**: closes the apps that are not in the profile, then unites |
| `arrange NAME` | **Arrange Existing on Screen**: only puts the profile's open windows back in place and order (also minimized ones); starts and closes nothing |
| `undo` | **Undo …**: undoes the last thing done, once, on the screens: an unite, reload or arrange (apps it started close, apps it closed start again, windows go back), or the last change saved in the settings app. The profile file is never written (in the settings app, the previous version is shown as unsaved edits) |
| `plan NAME` | shows, as JSON, which open window would be taken for which saved one; changes nothing |
| `show`, `edit`, `rename`, `delete`, `export`, `import` | look at, edit (opens the file in your text editor), share between computers |
| `clean` | deletes the automatic "Before NAME" profiles that older versions made |

- Every profile also appears in the app menu as **Load profile: NAME**, so it
  can be found by search (KRunner) and given a keyboard shortcut.
- Monitors are recognised by their identity (serial number or model), not by
  the connector, so windows land on the right monitor even if it is plugged
  into another port; a smaller monitor gets the windows scaled down.
- **Every window is saved with what it shows**, so a missing window can be
  opened again even while its app is running:

  | App | Saved per window |
  |---|---|
  | Chrome, Chromium, Brave, Edge, Vivaldi, Firefox | the open pages and the tab in front, read from the browser's own session file (read only; no extension needed). Reopened, the window gets its pages in order with the same tab in front (Firefox: the first tab, it cannot add pages to a chosen window) |
  | Konsole | the folder of every tab |
  | Dolphin | the open folders (tabs and split views) |
  | VS Code (also OSS, VSCodium) | the project folder |
  | Kate, KWrite, Okular, Gwenview, media players | the open file |

  Other apps are started as a whole when they are not running; a missing
  extra window of a running app of this kind cannot be opened again.
- **Windows are recognised by their title.** A browser window's title is its
  front tab, so after switching tabs it may not be recognised any more. When a
  saved window is not found but its app has other windows open, a window asks
  what to do, per saved window: open the saved one again, use one of the open
  windows (it is moved to the saved place), or leave it out. The setting
  `when_unsure` can answer this always the same way instead (`open`, `use`,
  `skip`). New windows that are not in the profile are never moved.
- Profiles are plain files in `~/.local/share/plasma-reopen-windows/profiles/`.
  Per app you can set `"enabled": false` (leave it out) or `"start_delay"`
  (seconds); per window the list of pages, `"urls": ["https://…"]`, tabs,
  folders or file can be edited by hand.
- Saving a profile again keeps these edits; the tabs are read again from the
  browser (a window whose tabs cannot be read keeps its earlier list).
- Videos (YouTube, VLC, …) are saved with their position and paused/playing
  state; when a profile starts their app or opens their window again, they go
  back there, with the sound off until they are. A video that is open already
  is not touched. (A browser only loads a video in the tab in front, which is
  why the front tab is saved too.)
- **Only one video per browser comes back at its place.** Chrome and Firefox
  each tell the desktop about one video only, the one started last (Firefox:
  only one that makes sound), so that is the one saved. Other videos that start
  by themselves after a restore or profile load are paused instead of playing
  out loud (settings `pause_unknown_videos`, `video_watch`); if several start at the same
  moment (several windows with a video in front), only one of them can be
  paused, as the browser shows just one.
- If an app started by a profile brings back fewer windows than it had (for
  example Chrome after a hard power-off), the missing ones are opened from
  what was saved for them.

## Autosave

Save & Shut Down only helps when it is used. Every 30 minutes (setting
`autosave_minutes`), a snapshot of the open apps and windows is taken, the
same as a profile: screens, places, order, browser pages with the tab in
front, Konsole and Dolphin folders, open files. Nothing is closed or moved,
and it is written only when something changed. It brings the windows back
after a hang or a long press, an empty battery, a crash, or the normal Shut
Down (which closes a browser window by window, so only its last window
would come back).

- **Save Windows Now** (app menu, the settings app, or a shortcut you give it
  in "Where to show it") takes one at once, also with autosave switched off.
- **Restore Last Autosave** brings the windows back like a profile's Unite:
  missing apps and windows start, nothing is closed, Undo works.
- In the settings app, both autosaves are also at the end of the profile
  list: the drawing and tables show what they would bring back, Unite,
  Reload and Arrange work on them, and **Keep as profile…** turns one into
  a normal profile with a name (autosaves are replaced as new ones come).
- The **last two** are kept: a snapshot taken while a normal shutdown was
  closing the apps is half empty, and the one before it is the good one.
  Switching autosave off keeps them.
- No snapshot is taken while a load or restore is running.
- At login, after a session that did not end with Save & Shut Down, one
  question offers them (setting `autosave_ask`; once per snapshot).
- An autosave holds the same as a profile, videos included (one per
  browser), and in addition a **copy of each browser's own session file**
  (Chrome and Chromium-based browsers: `Sessions/`, Firefox:
  `recovery.jsonlz4`), taken while the browser runs. When a restore starts a
  browser, the copy is put back first, so the browser brings back every
  window and tab itself, with each tab's back/forward history, as after Save
  & Restart. The files it replaces are moved to
  `~/.local/state/plasma-reopen-windows/browser-sessions/replaced/`, never
  deleted; a browser that is already running is left alone (its missing
  windows open from their page addresses).
- Not in an autosave: unsaved work and terminal output (only hibernation
  keeps those).

```bash
plasma-reopen-windows autosave now            # save now
plasma-reopen-windows autosave list           # 1 = newest, 2 = earlier
plasma-reopen-windows autosave restore 2      # bring back the earlier one
plasma-reopen-windows autosave delete
```

A user timer looks every 5 minutes whether one is due
(`systemctl --user status plasma-reopen-windows-autosave.timer`).

## Save & Update & Restart

Updates the system the way Discover's "Update and restart" does (PackageKit
offline updates), and keeps the windows:

1. The updates are downloaded while every app stays open (the settings app
   shows the progress, the app menu entry a notification). No updates, or the
   download fails: nothing is closed.
2. The apps close and are saved, as for Save & Restart (an app that does not
   close stops it; the downloaded updates stay for later).
3. The update is armed for the next boot and Plasma restarts. The computer
   boots once into the update, installs it, and restarts again.
4. At the login the windows come back.

No password: PackageKit lets the user at the screen download and install
updates. Flatpaks are not updated (Discover or `flatpak update` does that
while you work). After a kernel or NVIDIA driver update, the first login can
take a minute longer while the driver module is built.

```bash
plasma-reopen-windows update
pkcon offline-get-prepared      # what is downloaded and waiting
```

## At login

What comes back is the setting `on_login`:

- `last` (default): the session saved by Save & Shut Down / Restart; if
  there is none (the session ended otherwise), the last autosave is offered.
- `ask`: a notification with buttons: restore the last session, the last
  autosave, load a profile, or nothing. Without an answer, the last session
  comes back after `ask_timeout` seconds.
- `profile:NAME`: always that profile (for example `profile:Work`).
- `nothing`: start empty. A saved session is kept aside, not deleted.

```bash
plasma-reopen-windows config set on_login ask
```

## Settings

`~/.config/plasma-reopen-windows.conf`:

| Setting | Default | Meaning |
|---|---|---|
| `confirm` | yes | Ask before closing all apps. Show a dialog listing the open apps before Save & Shut Down / Restart closes them. |
| `close_timeout` | 30 | Seconds an app may take to close. If an app is still open after this (often because it asks to save something), the shutdown is cancelled and the apps closed so far are opened again. DaVinci Resolve always gets 120 s. |
| `exclude` | empty | Apps to leave alone. These apps are neither closed nor started again (for example a torrent client that should keep running in the tray). Give their app ids, space separated; an id looks like org.qbittorrent.qBittorrent or google-chrome. The first column of "plasma-reopen-windows windows" shows the id of every open app; the settings app has a picker. |
| `place_windows` | auto | Put windows back in place. Screen, position, size, desktop, minimized/maximized. auto = when the KWin script "Remember Window Positions" is on, leave the apps it handles to it and place the others (its blacklist, for example System Settings) here, so the two never move the same window. |
| `restore_stacking` | yes | Restore the window order. Put windows back in the same order on top of each other, on every screen. |
| `pause_unknown_videos` | yes | Pause videos that were not saved. A browser tells the desktop about one video only, so only that one comes back at its place. Other videos that start by themselves after a restore or profile load are paused instead of playing out loud. What was playing before is left alone. |
| `video_watch` | 10 | Seconds to watch for videos that start by themselves. After a load starts apps or opens windows, videos that were not saved and start by themselves are paused for this long (see the setting above); the sound stays off meanwhile (at most 12 s). 0 = do not watch. |
| `restore_timeout` | 120 | Seconds to wait for windows after login. Windows that appear later are not placed. |
| `max_age_days` | 7 | Oldest session to restore (days). A saved session older than this is ignored at login. |
| `on_login` | last | What comes back at login. last = the session saved by Save & Shut Down / Restart (if there is one); ask = a notification with the choices (last session, a profile, nothing); profile:NAME = always load that profile; nothing = start empty. |
| `ask_timeout` | 20 | Seconds to answer the login question. With on_login = ask: without an answer, the last session is restored (or nothing, if there is none). |
| `autosave` | yes | Save the open windows every while. A snapshot of the open apps and windows (nothing is closed or moved), for a hang, an empty battery, a crash, or the normal Shut Down. The last two are kept; switching this off keeps them, and "Save now" still works. |
| `autosave_minutes` | 30 | Minutes between autosaves. Written only when something changed since the last one. |
| `autosave_ask` | yes | Offer the autosave at login. After a session that ended without Save & Shut Down (with on_login = last or ask): one question to bring the windows of the last autosave back. |
| `profile_load` | unite | What loading a profile does outside the app. For the menu entries "Load profile: NAME" (app menu, or a shortcut, favorite or panel button from "Where to show it") and for profile:NAME at login. unite = like "Unite Screen and Existing": start the profile's apps and windows that are missing and arrange all of them, closing nothing; reload = like "Reload Existing": also close the apps that are not in the profile. Undo works for both. |
| `when_unsure` | ask | A saved window is not clearly found. When a saved window's title matches none of the open windows, but its app has other windows open (for example a browser window with another tab in front): ask = show a window to choose, per saved window; open = open the saved one again; use = move an open window of that app to its place; skip = leave it out. |

Change settings in the file, or with
`plasma-reopen-windows config set NAME VALUE` (keeps the comments; checks the
value). `plasma-reopen-windows config` shows them all.

## How it works

1. **Save:** a small KWin script lists every window (app, screen, geometry,
   desktop, state, stacking order). Konsole reports each tab's folder over
   D-Bus. Playing or paused videos are read over MPRIS, the default sound
   output from PipeWire.
2. **Close:** each app is quit as a whole: browsers and VS Code with SIGTERM
   (their clean quit), Dolphin with its Quit action, Konsole after its tabs
   are recorded, other apps by closing their windows like the ✕ button.
3. **Shut down / restart** through Plasma (`org.kde.Shutdown`), so Plasma
   saves nothing of its own on top. For Save & Update & Restart the updates
   are downloaded first (`pkcon update --only-download`) and armed after the
   apps closed (`pkcon offline-trigger`).
4. **Restore at login:** starts the apps (Konsole with its tabs), matches the
   new windows to the saved ones by app and title, moves them back, restores
   the stacking order, seeks and pauses the videos, and selects the sound
   output again.

## Troubleshooting

| What you see | What to do |
|---|---|
| "Shutdown cancelled: X did not close" | X probably asks to save something: answer it or close X, then try again |
| "Programs are still running in Konsole" | finish or stop them, then try again |
| Apps came back, windows not in place | with "Remember Window Positions" on, that script places the apps it handles and this tool the rest (the log says which); to let this tool place everything, set `place_windows = yes` and turn the script off |
| Nothing came back | `plasma-reopen-windows status` and the log; the autostart entry must exist in `~/.config/autostart/` |
| An app should not be touched | add its id to `exclude` |
| "Update cancelled: the updates could not be downloaded" | no network, or another update is running (Discover): wait and try again; the log has pkcon's last lines |
| Bluetooth speaker or headset silent after login, or sounds like a phone call | an app that records (OBS, DaVinci Resolve, video calls) is using the speaker's built-in microphone, which forces it into headset mode. Pick the laptop microphone as default input (speaker icon → microphone), then reconnect the speaker. To stop the automatic switch for good, see below |
| DaVinci Resolve hangs at "Loading Projects" | same cause: its audio input is the Bluetooth device's microphone. Fix as above; it is not caused by this tool |

**Keep Bluetooth speakers in music mode.** Create
`~/.config/wireplumber/wireplumber.conf.d/51-bluetooth-no-headset-autoswitch.conf`:

```
wireplumber.settings = {
  bluetooth.autoswitch-to-headset-profile = false
}
```

then `systemctl --user restart wireplumber`. Apps that want a microphone then
use the laptop's, and the speaker stays in music mode. Delete the file to undo
it.

## Uninstall

```bash
bash uninstall.sh
```

Removes the tool, the menu entries and the autostart entry, and asks before
deleting the settings and the saved session.

## Development

The code is a small Python package in `src/plasma_reopen_windows/` (`config`,
`kwin`, `apps`, `session`, `media`, `audio`, `cli`), started by
`src/plasma-reopen-windows`. Tests use only the standard library:

```bash
python3 -m unittest discover -s tests -v
```

`install.sh` runs them first and installs nothing if one fails.

## License

MIT, see [LICENSE](LICENSE).
