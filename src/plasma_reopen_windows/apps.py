"""plasma-reopen-windows: closing and starting apps."""

import json
import os
import pwd
import re
import shlex
import signal
import subprocess
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from gi.repository import Gio, GLib
from .browsers import CHROME_DIRS
from .config import CFG, STATE_DIR, log, progress
from .kwin import JS_ACTIVATE, JS_CLOSE


# Apps that restore all their own windows when quit as a whole: quit with SIGTERM
QUIT_BY_SIGNAL = {
    "google-chrome", "com.google.Chrome", "chromium-browser", "chromium", "org.chromium.Chromium",
    "brave-browser", "com.brave.Browser", "microsoft-edge", "com.microsoft.Edge", "vivaldi-stable",
    "code", "com.microsoft.VSCode", "code-oss", "codium", "com.vscodium.codium",
    "org.mozilla.firefox", "firefox", "firefox-esr", "org.mozilla.firefox_esr",
}


FIREFOX = {"org.mozilla.firefox", "firefox", "firefox-esr", "org.mozilla.firefox_esr"}


# Apps whose open file is found from the window title + the recent-files list
DOC_APPS = {"org.kde.gwenview", "org.kde.okular", "org.kde.kwrite", "org.kde.kate"}


# Apps that take longer to close (seconds)
CLOSE_TIMEOUT = {"resolve": 120, "com.blackmagicdesign.resolve": 120}


# Heavy apps started last, after the others have settled (seconds to wait)
LATE_START = {"resolve": 15, "com.blackmagicdesign.resolve": 15}


# Never touched: desktop shell, helpers, tray tools
IGNORE = {
    "plasmashell", "org.kde.plasmashell", "org.kde.krunner", "krunner", "kwin", "org.kde.kwin",
    "xwaylandvideobridge", "org.kde.xwaylandvideobridge", "org.kde.polkit-kde-authentication-agent-1",
    "org.kde.kwalletd6", "org.kde.kwalletd5", "org.kde.ksmserver", "org.kde.plasma-systemmonitor-applet",
    "org.kde.spectacle", "kdialog", "org.kde.kdialog",
    "plasma-reopen-windows-settings",
}


# Quit order: everything else first (most likely to ask about unsaved work),
# then the apps that restore themselves.
ORDER = {"generic": 0, "konsole": 1, "dolphin": 2, "signal": 3}


# ---- apps --------------------------------------------------------------------
def app_kind(app):
    if app in QUIT_BY_SIGNAL:
        return "signal"
    if app == "org.kde.konsole":
        return "konsole"
    if app == "org.kde.dolphin":
        return "dolphin"
    return "generic"


def ignored(app):
    return app in IGNORE or app in CFG.get("exclude", "").split()


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def proc_name(pid):
    try:
        with open("/proc/%d/comm" % pid) as f:
            return f.read().strip()
    except OSError:
        return "?"


def desktop_info(app):
    try:
        return Gio.DesktopAppInfo.new(app + ".desktop")
    except TypeError:            # PyGObject: "constructor returned NULL"
        return None


def desktop_exists(app):
    return desktop_info(app) is not None


def app_name(app):
    info = desktop_info(app)
    return info.get_name() if info else app


def konsole_tabs(kw, pid):
    """[[cwd, ...] per window], and busy tabs [(cwd, program)]."""
    service = "org.kde.konsole-%d" % pid
    windows, busy = [], []
    try:
        xml = kw.call(service, "/Windows", "org.freedesktop.DBus.Introspectable", "Introspect", None, "(s)")[0]
    except GLib.Error:
        return None, []
    node = Gio.DBusNodeInfo.new_for_xml(xml)
    for child in sorted((n.path for n in node.nodes), key=lambda p: int(p) if p.isdigit() else 0):
        try:
            sessions = kw.call(service, "/Windows/" + child, "org.kde.konsole.Window", "sessionList", None, "(as)")[0]
        except GLib.Error:
            continue
        tabs = []
        for s in sessions:
            path = "/Sessions/" + s
            try:
                shell = kw.call(service, path, "org.kde.konsole.Session", "processId", None, "(i)")[0]
                fg = kw.call(service, path, "org.kde.konsole.Session", "foregroundProcessId", None, "(i)")[0]
                cwd = os.readlink("/proc/%d/cwd" % (fg if fg > 0 else shell))
            except (GLib.Error, OSError):
                continue
            tabs.append(cwd)
            if fg > 0 and fg != shell:
                busy.append((cwd, proc_name(fg)))
        if tabs:
            windows.append(tabs)
    return windows, busy


def quit_app(kw, app, kind, wins):
    """Ask an app to quit as a whole. Returns the pids to wait for."""
    pids = sorted({w["pid"] for w in wins if w["pid"] > 0})
    if kind == "signal" or kind == "konsole":
        for p in pids:
            os.kill(p, signal.SIGTERM)
    elif kind == "dolphin":
        for p in pids:
            try:
                kw.call("org.kde.dolphin-%d" % p, "/dolphin/Dolphin_1/actions/file_quit",
                        "org.qtproject.Qt.QAction", "trigger")
            except GLib.Error:
                kw.run(JS_CLOSE % json.dumps([w["id"] for w in wins if w["pid"] == p]))
    else:
        kw.run(JS_CLOSE % json.dumps([w["id"] for w in wins]))
    return pids


def wait_closed(kw, app, kind, wins, pids, timeout):
    """Generic apps may stay in the tray: for them, gone windows are enough."""
    ids = {w["id"] for w in wins}
    end = time.time() + timeout
    while time.time() < end:
        if kind == "generic":
            if not ids & {w["id"] for w in kw.windows()["windows"]}:
                return True
        elif not any(pid_alive(p) for p in pids):
            return True
        time.sleep(0.5)
    return False


# Closed before a shut down or restart, but never started again: a started
# Spectacle takes a new screenshot, and the one open cannot come back. Left
# running (often in the background, without a window), Plasma notes it at the
# log-out and starts it at the next login (measured 2026-11-25).
CLOSE_ONLY = {"org.kde.spectacle": "spectacle"}


def user_pids(exe):
    """Processes of this user running this program."""
    out = []
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        try:
            with open("/proc/%s/comm" % d) as f:
                if f.read().strip() != exe:
                    continue
            if os.stat("/proc/" + d).st_uid == os.getuid():
                out.append(int(d))
        except OSError:
            continue
    return out


def close_only_apps(kw, timeout):
    """Close the CLOSE_ONLY apps: their windows like the ✕ button (an
    unsaved screenshot is asked about), then what still runs without a window
    (nothing on screen to lose). Returns the app that stayed open, or None."""
    wins = [w for w in kw.windows()["windows"] if w["app"] in CLOSE_ONLY]
    for app in sorted({w["app"] for w in wins}):
        ws = [w for w in wins if w["app"] == app]
        kw.run(JS_CLOSE % json.dumps([w["id"] for w in ws]))
        if not wait_closed(kw, app, "generic", ws, [], timeout):
            return app
        log("closed %s (not started again)" % app)
    for app, exe in CLOSE_ONLY.items():
        for pid in user_pids(exe):
            try:
                os.kill(pid, signal.SIGTERM)
                log("ended %s running in the background (not started again)" % app)
            except OSError:
                pass
    return None


def launch_spec(app, kind, wins, konsole, files):
    if kind == "konsole":
        tabs = [t for pid in sorted({w["pid"] for w in wins}) for t in konsole.get(pid, [])]
        return {"app": app, "konsole": tabs or [[os.path.expanduser("~")]]}
    desktop = find_desktop(app, wins[0])
    if not desktop:
        return None
    spec = {"app": app, "desktop": desktop}
    pids = {w["pid"] for w in wins}
    found = [f for pid in pids for f in files.get(pid, [])]
    if app in DOC_APPS:
        found += [f for w in wins for f in [recent_file(app, w["caption"])] if f]
    if found:
        spec["files"] = list(dict.fromkeys(found))
    if app in FIREFOX:
        spec["firefox_profiles"] = sorted({d for pid in pids for d in firefox_profiles(pid)})
    return spec


def exec_name(info):
    """Program a desktop entry starts, without "env VAR=..." in front."""
    try:
        args = shlex.split(info.get_commandline() or "")
    except ValueError:
        return ""
    while args and (os.path.basename(args[0]) == "env" or "=" in args[0]):
        args.pop(0)
    return os.path.basename(args[0]) if args else ""


def find_desktop(app, w):
    """Menu entry of an app: by its id, else by window class or program name."""
    if desktop_exists(app):
        return app
    try:
        prog = os.path.basename(os.readlink("/proc/%d/exe" % w["pid"]))
    except OSError:
        prog = ""
    cls = (w.get("cls") or "").lower()
    for info in Gio.DesktopAppInfo.get_all():
        wm = (info.get_startup_wm_class() or "").lower()
        if (cls and wm == cls) or (prog and exec_name(info) == prog):
            return info.get_id()[:-len(".desktop")]
    return None


def recent_file(app, caption):
    """Full path of the file named in a window title, from the recent-files list."""
    name = caption.rsplit(" — ", 1)[0] if " — " in caption else caption.rsplit(" - ", 1)[0]
    name = name.strip().lstrip("*").strip()
    if not name or name.lower() == app.split(".")[-1].lower():
        return None
    try:
        root = ET.parse(os.path.expanduser("~/.local/share/recently-used.xbel")).getroot()
    except (OSError, ET.ParseError):
        return None
    best = None
    for b in root.iter("bookmark"):
        href = b.get("href", "")
        if not href.startswith("file://"):
            continue
        path = urllib.parse.unquote(href[7:])
        if os.path.basename(path) != name or not os.path.exists(path):
            continue
        apps = [a.get("name", "") for a in b.iter() if a.tag.endswith("application")]
        if not any(app.split(".")[-1].lower() in a.lower() for a in apps):
            continue
        if best is None or b.get("modified", "") > best[0]:
            best = (b.get("modified", ""), path)
    return best[1] if best else None


def real_path(path):
    """A KDE kio-fuse path (only valid while logged in) back to the real file."""
    m = re.match(r"/run/user/\d+/kio-fuse-[^/]+/([^/]+)/(.*)", path)
    if not m:
        return path
    scheme, rest = m.groups()
    return "/" + rest if scheme == "file" else "%s://%s" % (scheme, rest)


def command_with_file(desktop, path):
    """The menu entry's command with a file in place of %f/%F/%u/%U (started
    directly: through KDE, a local file can end up as a kio-fuse path)."""
    info = desktop_info(desktop)
    if not info or not info.get_commandline():
        return None
    try:
        args = shlex.split(info.get_commandline())
    except ValueError:
        return None
    out, placed = [], False
    for a in args:
        if a in ("%f", "%F", "%u", "%U"):
            out.append(path)
            placed = True
        elif not re.fullmatch(r"%[a-zA-Z]", a):
            out.append(a)
    return out if placed else out + [path]


def firefox_profiles(pid):
    """Firefox profile folders a running Firefox has open."""
    out = set()
    try:
        for fd in os.listdir("/proc/%d/fd" % pid):
            m = re.match(r"(.*/firefox/[^/]+)/", os.readlink("/proc/%d/fd/%s" % (pid, fd)))
            if m and os.path.exists(os.path.join(m.group(1), "prefs.js")):
                out.add(m.group(1))
    except OSError:
        pass
    return out


def firefox_resume_once(profile):
    """Make Firefox restore its last session at the next start, once (Firefox
    resets this itself); its normal startup setting stays as it is."""
    with open(os.path.join(profile, "prefs.js"), "a") as f:
        f.write('user_pref("browser.sessionstore.resume_session_once", true);\n')


def login_shell():
    return os.environ.get("SHELL") or pwd.getpwuid(os.getuid()).pw_shell or "/bin/bash"


def new_window(kw, app, before, timeout):
    """Id of a window of this app that is not in before, once it appears (or None)."""
    end = time.time() + timeout
    while time.time() < end:
        for w in kw.windows()["windows"]:
            if w["app"] == app and w["id"] not in before:
                return w["id"]
        time.sleep(0.4)
    return None


def open_in_steps(kw, spec):
    """A browser window whose front tab is not its first page (see
    reopen.browser_window): new window, make it active, add the rest."""
    run = ["kstart", "--desktopfile", spec["desktop"], "--"]
    before = {w["id"] for w in kw.windows()["windows"]}
    subprocess.Popen(run + spec["cmd"])
    wid = new_window(kw, spec["app"], before, 30)
    if wid and kw.run(JS_ACTIVATE % json.dumps(wid)):
        time.sleep(0.8)                   # let the browser notice which window is active
        subprocess.Popen(run + spec["then"])
    else:                                 # never into another of the user's windows
        log("%s: new window did not appear in time; the other pages open in a window of their own"
            % spec["app"])
        subprocess.Popen(run + spec["alone"])


def relaunch(launches, kw=None):
    late = [l for l in launches if l["app"] in LATE_START]
    launches = [l for l in launches if l["app"] not in LATE_START] + late
    # tab lists from earlier restores (Konsole reads them within seconds)
    for f in os.listdir(STATE_DIR) if os.path.isdir(STATE_DIR) else []:
        path = os.path.join(STATE_DIR, f)
        if f.startswith("konsole-tabs-") and time.time() - os.path.getmtime(path) > 3600:
            os.remove(path)
    for n, spec in enumerate(launches):
        what = "opening a window of" if "cmd" in spec else "starting"
        progress("%s %s" % (what, app_name(spec.get("desktop") or spec["app"])), n, len(launches))
        if spec in late and spec is late[0]:
            time.sleep(max(LATE_START[l["app"]] for l in late))
        if spec.get("delay"):
            time.sleep(spec["delay"])
        if "cmd" in spec:             # a ready command (one window with its pages, folders, file)
            if spec.get("then") and kw:
                open_in_steps(kw, spec)
            else:
                subprocess.Popen(["kstart", "--desktopfile", spec["desktop"], "--"] + spec["cmd"])
            log("opened %s %s" % (spec["app"], spec.get("what", "")))
            time.sleep(1)
            continue
        if "konsole" in spec:
            for tabs in spec["konsole"]:
                # Konsole always adds its default tab after the ones from the file,
                # so the last tab is that default one (--workdir), the others go
                # in the file.
                cmd = ["kstart", "--", "konsole", "--workdir", tabs[-1]]
                if len(tabs) > 1:
                    fd, path = tempfile.mkstemp(prefix="konsole-tabs-", suffix=".txt", dir=STATE_DIR)
                    with os.fdopen(fd, "w") as f:
                        # each line needs a command; "%d : %n" is Konsole's default
                        # tab title (folder : program), so titles keep updating
                        f.writelines("title: %%d : %%n;; workdir: %s;; command: %s\n" % (t, login_shell())
                                     for t in tabs[:-1])
                    cmd += ["--tabs-from-file", path]
                subprocess.Popen(cmd)
        else:
            for profile in spec.get("firefox_profiles", []):
                try:
                    firefox_resume_once(profile)
                except OSError as e:
                    log("firefox: %s" % e)
            files = [real_path(f) for f in spec.get("files", [])]
            if spec.get("restore_session") and spec["app"] in CHROME_DIRS:
                # Chrome-style browsers: restore the session put back for it,
                # whatever "On startup" says (Firefox: resume_session_once above)
                direct = command_with_file(spec["desktop"], "--restore-last-session")
                if direct:
                    subprocess.Popen(["kstart", "--desktopfile", spec["desktop"], "--"] + direct)
                    log("started %s with its saved session" % spec["app"])
                    time.sleep(1)
                    continue
            for f in files or [None]:
                direct = command_with_file(spec["desktop"], f) if f else None
                if direct:
                    subprocess.Popen(["kstart", "--desktopfile", spec["desktop"], "--"] + direct)
                    log("reopening %s" % f)
                else:
                    subprocess.Popen(["kstart", "--application", spec["desktop"]] + (["--url", f] if f else []))
        log("started " + spec["app"])
        time.sleep(1)


def power(action):
    method = {"shutdown": "logoutAndShutdown", "reboot": "logoutAndReboot"}[action]
    log("asking Plasma to " + action)
    Gio.bus_get_sync(Gio.BusType.SESSION).call_sync(
        "org.kde.Shutdown", "/Shutdown", "org.kde.Shutdown", method, None, None, 0, -1)
