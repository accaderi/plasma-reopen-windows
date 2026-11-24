"""plasma-reopen-windows: what a single window shows, so that this one window
can be opened again while its app keeps running (profiles: a saved window
that is missing).

  browsers        the pages (browsers.py, read from the browser's session file)
  Konsole         the folder of each tab (Konsole's D-Bus interface)
  Dolphin         the open folders, also split views (Dolphin answers "is this
                  folder open?" for the folders it recently showed)
  VS Code         the folder (VS Code's own list of open windows)
  document apps   the file (window title + the recent-files list, or the
                  file a media player reports)
"""

import json
import os
import re
import shlex
import sqlite3
from gi.repository import Gio, GLib
from .apps import DOC_APPS, FIREFOX, command_with_file, desktop_info, recent_file

DOLPHIN = {"org.kde.dolphin", "dolphin"}
KONSOLE = {"org.kde.konsole", "konsole"}
VSCODE = {
    "code": "~/.config/Code", "com.microsoft.VSCode": "~/.config/Code",
    "code-oss": "~/.config/Code - OSS", "codium": "~/.config/VSCodium", "com.vscodium.codium": "~/.config/VSCodium",
}
ACTIVITY_DB = os.path.expanduser("~/.local/share/kactivitymanagerd/resources/database")


def _call(bus, service, path, iface, method, args=None, sig=None):
    return bus.call_sync(service, path, iface, method, args, GLib.VariantType(sig) if sig else None,
                         0, 3000).unpack()


# ---- Konsole ---------------------------------------------------------------------
def konsole_windows(bus, pid):
    """[(title of the tab in front, [folder of each tab])] per Konsole window."""
    service = "org.kde.konsole-%d" % pid
    out = []
    try:
        xml = _call(bus, service, "/Windows", "org.freedesktop.DBus.Introspectable", "Introspect", None, "(s)")[0]
    except GLib.Error:
        return out
    for node in Gio.DBusNodeInfo.new_for_xml(xml).nodes:
        win = "/Windows/" + node.path
        try:
            sessions = _call(bus, service, win, "org.kde.konsole.Window", "sessionList", None, "(as)")[0]
            current = _call(bus, service, win, "org.kde.konsole.Window", "currentSession", None, "(i)")[0]
        except GLib.Error:
            continue
        tabs, title = [], ""
        for s in sessions:
            path = "/Sessions/" + s
            try:
                shell = _call(bus, service, path, "org.kde.konsole.Session", "processId", None, "(i)")[0]
                fg = _call(bus, service, path, "org.kde.konsole.Session", "foregroundProcessId", None, "(i)")[0]
                tabs.append(os.readlink("/proc/%d/cwd" % (fg if fg > 0 else shell)))
                if int(s) == current:
                    title = _call(bus, service, path, "org.kde.konsole.Session", "title", GLib.Variant("(i)", (1,)),
                                  "(s)")[0]
            except (GLib.Error, OSError, ValueError):
                continue
        if tabs:
            out.append((title, tabs))
    return out


# ---- Dolphin ---------------------------------------------------------------------
def recent_folders(agents=("org.kde.dolphin", "dolphin")):
    """Folders Plasma's activity log has seen opened by these apps."""
    try:
        db = sqlite3.connect("file:%s?mode=ro" % ACTIVITY_DB, uri=True, timeout=2)
        rows = db.execute("SELECT DISTINCT targettedResource FROM ResourceScoreCache WHERE initiatingAgent IN (%s)"
                          % ",".join("?" * len(agents)), agents).fetchall()
        db.close()
    except sqlite3.Error:
        rows = []
    out = [r[0] for r in rows if r[0].startswith("/") and os.path.isdir(r[0])]
    return out + [os.path.expanduser("~")]


def dolphin_windows(bus, pid, candidates):
    """[[folder, ...] open in each window] of a Dolphin process."""
    service = "org.kde.dolphin-%d" % pid
    out = []
    for n in range(1, 30):
        path = "/dolphin/Dolphin_%d" % n
        try:
            found = [c for c in candidates if _call(bus, service, path, "org.kde.dolphin.MainWindow", "isUrlOpen",
                                                    GLib.Variant("(s)", (Gio.File.new_for_path(c).get_uri(),)),
                                                    "(b)")[0]]
        except GLib.Error:
            if n > 3:
                break
            continue                 # numbers of closed windows are not reused
        if found:
            out.append(found)
    return out


# ---- VS Code ---------------------------------------------------------------------
def vscode_folders(app):
    """Folders (or workspace files) of the open VS Code windows."""
    path = os.path.expanduser(os.path.join(VSCODE[app], "User/globalStorage/storage.json"))
    try:
        with open(path) as f:
            state = json.load(f).get("windowsState", {})
    except (OSError, ValueError):
        return []
    out = []
    for w in [state.get("lastActiveWindow") or {}] + state.get("openedWindows", []):
        uri = w.get("folder") or (w.get("workspace") or {}).get("configPath")
        if uri and uri.startswith("file://"):
            p = Gio.File.new_for_uri(uri).get_path()
            if p and p not in out:
                out.append(p)
    return out


def _folder_in_caption(folder, caption):
    name = os.path.basename(folder.rstrip("/"))
    if name.endswith(".code-workspace"):
        name = name[:-len(".code-workspace")]
    return name and name in [part.strip() for part in re.split(r" [-—] ", caption)]


# ---- all windows -----------------------------------------------------------------
def details(bus, wins, files):
    """{window id: what it shows} for the windows that can be opened one by one.
    files: files media players report, per process."""
    out = {}
    by_app = {}
    for w in wins:
        by_app.setdefault(w["app"], []).append(w)
    for app, ws in by_app.items():
        if app in KONSOLE:
            for pid in {w["pid"] for w in ws}:
                left = [w for w in ws if w["pid"] == pid]
                for title, tabs in konsole_windows(bus, pid):
                    w = next((w for w in left if title and w["caption"].startswith(title)), None)
                    if w:
                        left.remove(w)
                        out[w["id"]] = {"tabs": tabs}
        elif app in DOLPHIN:
            cands = recent_folders()
            for pid in {w["pid"] for w in ws}:
                left = [w for w in ws if w["pid"] == pid]
                for folders in dolphin_windows(bus, pid, cands):
                    w = next((w for w in left if any(_folder_in_caption(f, w["caption"]) or
                                                     w["caption"].startswith(f) for f in folders)), None)
                    if w:
                        left.remove(w)
                        out[w["id"]] = {"folders": folders}
        elif app in VSCODE:
            folders = vscode_folders(app)
            for w in ws:
                f = next((f for f in folders if _folder_in_caption(f, w["caption"])), None)
                if f:
                    folders.remove(f)
                    out[w["id"]] = {"folder": f}
        else:
            for w in ws:
                f = recent_file(app, w["caption"]) if app in DOC_APPS else None
                if not f and len(ws) == 1 and len(files.get(w["pid"], [])) == 1:
                    f = files[w["pid"]][0]          # a player with one file open
                if f:
                    out[w["id"]] = {"file": f}
    return out


def can_reopen(w):
    return bool(w.get("urls") or w.get("tabs") or w.get("folders") or w.get("folder") or w.get("file"))


def exec_args(desktop):
    """The menu entry's command without its %-placeholders."""
    info = desktop_info(desktop) if desktop else None
    if not info or not info.get_commandline():
        return None
    try:
        return [a for a in shlex.split(info.get_commandline()) if not re.fullmatch(r"%[a-zA-Z]", a)]
    except ValueError:
        return None


def browser_window(w, desktop, base):
    """Open a browser window with its pages, in order, the saved front tab in front.

    Chrome (and Chromium-based browsers) put the first page given in front, and
    add pages given without --new-window to the window that is active, the first
    of them in front. So: a new window with the pages before the front tab, then
    (that window made active, see apps.relaunch) the front tab and the rest.
    Firefox splits "--new-window A B" over two windows; a plain "firefox A B"
    opens one new window with all of them, the first in front. Firefox cannot
    add pages to a chosen window, so there the first tab is in front."""
    urls = list(w["urls"])
    spec = {"app": w["app"], "desktop": desktop, "what": "with %d pages" % len(urls)}
    if w["app"] in FIREFOX:
        spec["cmd"] = base + (["--new-window"] + urls if len(urls) == 1 else urls)
        return spec
    k = w.get("active") or 0
    if 0 < k < len(urls):
        spec["cmd"] = base + ["--new-window"] + urls[:k]
        spec["then"] = base + urls[k:]        # into the new window, once it is active
        spec["alone"] = base + ["--new-window"] + urls[k:]   # if that window does not appear
    else:
        spec["cmd"] = base + ["--new-window"] + urls
    return spec


def command(w, desktop):
    """A start spec (see apps.relaunch) that opens this one window again, or None."""
    app = w["app"]
    if w.get("tabs"):
        return {"app": app, "konsole": [w["tabs"]], "what": "with %d tabs" % len(w["tabs"])}
    base = exec_args(desktop)
    if not base:
        return None
    if w.get("urls"):
        return browser_window(w, desktop, base)
    elif w.get("folders"):
        cmd = base + ["--new-window"] + [Gio.File.new_for_path(f).get_uri() for f in w["folders"]]
        what = "with %d folders" % len(w["folders"])
    elif w.get("folder"):
        cmd = base + ["--new-window", w["folder"]]
        what = w["folder"]
    elif w.get("file"):
        cmd = command_with_file(desktop, w["file"])
        what = w["file"]
    else:
        return None
    return {"app": app, "desktop": desktop, "cmd": cmd, "what": what} if cmd else None
