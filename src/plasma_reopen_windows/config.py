"""plasma-reopen-windows: paths, settings, log and notifications."""

import configparser
import json
import os
import re
import subprocess
import sys
import time



# ---- paths and settings ------------------------------------------------------
STATE_DIR = os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")),
                         "plasma-reopen-windows")


STATE = os.path.join(STATE_DIR, "session.json")


LOG = os.path.join(STATE_DIR, "log")


CONF = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
                    "plasma-reopen-windows.conf")


# One definition per setting. It drives the commented settings file
# ("config template"), validation, "config" output and the settings UI.
SETTINGS = [
    # Saving
    dict(key="confirm", default="yes", type="bool", section="Saving",
         label="Ask before closing all apps",
         help="Show a dialog listing the open apps before Save & Shut Down / Restart closes them."),
    dict(key="close_timeout", default="30", type="int", min=5, max=600, section="Saving",
         label="Seconds an app may take to close",
         help="If an app is still open after this (often because it asks to save something), "
              "the shutdown is cancelled and the apps closed so far are opened again. "
              "DaVinci Resolve always gets 120 s.",
         when="an app regularly needs longer to close."),
    dict(key="exclude", default="", type="list", section="Saving",
         label="Apps to leave alone",
         help="These apps are neither closed nor started again (for example a torrent client "
              "that should keep running in the tray). Give their app ids, space separated; an id "
              "looks like org.qbittorrent.qBittorrent or google-chrome. The first column of "
              "\"plasma-reopen-windows windows\" shows the id of every open app; the settings "
              "app has a picker.",
         example="org.qbittorrent.qBittorrent com.obsproject.Studio"),
    # Restoring
    dict(key="place_windows", default="auto", type="choice", choices=["auto", "yes", "no"],
         section="Restoring", label="Put windows back in place",
         help="Screen, position, size, desktop, minimized/maximized. auto = when the KWin script "
              "\"Remember Window Positions\" is on, leave the apps it handles to it and place "
              "the others (its blacklist, for example System Settings) here, so the two never "
              "move the same window.",
         when="you want this tool to place windows although that script is on (then turn the script off)."),
    dict(key="restore_stacking", default="yes", type="bool", section="Restoring",
         label="Restore the window order",
         help="Put windows back in the same order on top of each other, on every screen."),
    dict(key="pause_unknown_videos", default="yes", type="bool", section="Restoring",
         label="Pause videos that were not saved",
         help="A browser tells the desktop about one video only, so only that one comes back at "
              "its place. Other videos that start by themselves after a restore or profile load "
              "are paused instead of playing out loud. What was playing before is left alone."),
    dict(key="max_age_days", default="7", type="int", min=1, max=365, section="Restoring",
         label="Oldest session to restore (days)",
         help="A saved session older than this is ignored at login."),
    # Waiting: a restore or profile load ends as soon as nothing more is
    # coming; each of these is named in its progress text
    dict(key="restore_timeout", default="120", type="int", min=10, max=900, section="Waiting",
         label="Seconds to wait for windows at most",
         help="The upper limit for a restore or a profile load; it normally ends much sooner "
              "(see the settings below). Windows that appear later are not placed.",
         when="slow apps (large projects) take longer than 2 minutes to open."),
    dict(key="window_wait", default="20", type="int", min=3, max=300, section="Waiting",
         label="Seconds to wait for one more window",
         help="Apps that were started may still open windows: the restore ends when no new "
              "window appeared for this long (and every saved window that is still missing is "
              "opened again, where the app allows).",
         when="a slow app (a large project) opens its window later than this."),
    dict(key="browser_wait", default="8", type="int", min=2, max=120, section="Waiting",
         label="Seconds a browser gets to restore all its windows",
         help="Chrome and Firefox restore all their windows at once: the windows still missing "
              "this long after the browser's first one are not coming, and are opened again "
              "from their saved pages (their videos are put back then)."),
    dict(key="video_wait", default="20", type="int", min=0, max=300, section="Waiting",
         label="Seconds to wait for a saved video to appear",
         help="A saved video whose tab has not shown up this long after the last window "
              "appeared is given up (a browser loads a window's other tabs only when shown).",
         when="a video on a slow site is put back too rarely."),
    dict(key="mute_max", default="30", type="int", min=0, max=300, section="Waiting",
         label="Seconds the sound may stay off",
         help="The sound is off while videos are put back at their place (they start playing "
              "as soon as their tab loads); it comes back on when they are done, at the latest "
              "after this."),
    dict(key="sound_wait", default="20", type="int", min=0, max=300, section="Waiting",
         label="Seconds to wait for the sound output",
         help="A monitor's sound output appears only when the monitor has woken up: it is "
              "waited for this long after the windows and videos are done, then the sound "
              "stays where it is."),
    dict(key="video_watch", default="10", type="int", min=0, max=120, section="Waiting",
         label="Seconds to watch for videos that start by themselves",
         help="After a load starts apps or opens windows, videos that were not saved and "
              "start by themselves are paused for this long (with “Pause videos that were not saved” on, under Restoring); the "
              "sound stays off meanwhile (at most 12 s of it). 0 = do not watch.",
         when="a video in a slow-loading window still starts playing after the load."),
    # Login
    dict(key="on_login", default="last", type="login", section="Login",
         label="What comes back at login",
         help="last = the session saved by Save & Shut Down / Restart (if there is one); "
              "ask = a notification with the choices (last session, a profile, nothing); "
              "profile:NAME = always load that profile; nothing = start empty.",
         example="profile:Work"),
    dict(key="ask_timeout", default="20", type="int", min=5, max=300, section="Login",
         label="Seconds to answer the login question",
         help="With on_login = ask: without an answer, the last session is restored "
              "(or nothing, if there is none)."),
    # Profiles
    # Autosave
    dict(key="autosave", default="yes", type="bool", section="Autosave",
         label="Save the open windows every while",
         help="A snapshot of the open apps and windows (nothing is closed or moved), for a "
              "hang, an empty battery, a crash, or the normal Shut Down. The last two are "
              "kept; switching this off keeps them, and \"Save now\" still works."),
    dict(key="autosave_minutes", default="30", type="int", min=5, max=240, section="Autosave",
         label="Minutes between autosaves",
         help="Written only when something changed since the last one."),
    dict(key="autosave_ask", default="yes", type="bool", section="Autosave",
         label="Offer the autosave at login",
         help="After a session that ended without Save & Shut Down (with on_login = last "
              "or ask): one question to bring the windows of the last autosave back."),
    dict(key="profile_load", default="unite", type="choice", choices=["unite", "reload"],
         section="Profiles", label="What loading a profile does outside the app",
         help="For the menu entries \"Load profile: NAME\" (app menu, or a shortcut, favorite "
              "or panel button from \"Where to show it\") and for profile:NAME at login. "
              "unite = like \"Unite Screen and Existing\": start the profile's apps and windows "
              "that are missing and arrange all of them, closing nothing; reload = like "
              "\"Reload Existing\": also close the apps that are not in the profile. Undo works "
              "for both."),
    dict(key="when_unsure", default="ask", type="choice", choices=["ask", "open", "use", "skip"],
         section="Profiles", label="A saved window is not clearly found",
         help="When a saved window's title matches none of the open windows, but its app "
              "has other windows open (for example a browser window with another tab in "
              "front): ask = show a window to choose, per saved window; open = open the "
              "saved one again; use = move an open window of that app to its place; "
              "skip = leave it out."),
    dict(key="hibernate_minimize", default="no", type="bool", section="Hibernation",
         label="When hibernation would be refused for memory, minimize the windows first",
         help="Graphics memory of shown windows stays in RAM and goes into the hibernation "
              "image. Minimized, a playing video window gives back about 100 MiB after 20-30 s "
              "(measured on a Radeon). hibernate-prepare tries this only when the image would "
              "not fit otherwise; the windows come back after waking, in the same order.",
         when="hibernations are refused because of memory, and you can wait half a minute."),
    dict(key="hibernate_minimize_wait", default="30", type="int", min=15, max=60, section="Hibernation",
         label="Seconds to wait after minimizing",
         help="Longer if the memory is still shrinking (up to 10 s more). Chrome gave a minimized "
              "video's memory back only 20-26 s later (measured): below about 25 s it often "
              "frees nothing."),
]


DEFAULTS = {s["key"]: s["default"] for s in SETTINGS}


def label(key):
    """A setting's name as the settings app shows it, in quotes (progress
    texts name the setting that decides a wait, so it is easy to find)."""
    return "“%s”" % next(s["label"] for s in SETTINGS if s["key"] == key)


def setting(key, value=None):
    """“Setting name” = 20 s: the setting that decides a wait, with the value
    it has now (or the one used, when an app has its own)."""
    return "%s = %s s" % (label(key), CFG.get(key) if value is None else value)


def seconds(key):
    return int(CFG.get(key))


def setting_values(st):
    if st["type"] == "bool":
        return "yes / no"
    if st["type"] == "choice":
        return " / ".join(st["choices"])
    if st["type"] == "int":
        return "%d-%d" % (st["min"], st["max"])
    if st["type"] == "list":
        return "space-separated list"
    if st["type"] == "login":
        return "last / ask / nothing / profile:NAME"
    return "text"


def check_value(st, value):
    """Return the value normalised, or raise ValueError with a readable reason."""
    v = value.strip()
    if st["type"] == "bool":
        v = v.lower()
        if v in ("yes", "true", "on", "1"):
            return "yes"
        if v in ("no", "false", "off", "0"):
            return "no"
        raise ValueError("%s must be yes or no" % st["key"])
    if st["type"] == "choice":
        if v not in st["choices"]:
            raise ValueError("%s must be one of: %s" % (st["key"], ", ".join(st["choices"])))
        return v
    if st["type"] == "int":
        if not v.isdigit() or not st["min"] <= int(v) <= st["max"]:
            raise ValueError("%s must be a number from %d to %d" % (st["key"], st["min"], st["max"]))
        return str(int(v))
    if st["type"] == "list":
        return " ".join(v.split())
    if st["type"] == "login":
        if v in ("last", "ask", "nothing") or (v.startswith("profile:") and v[8:].strip()):
            return v if not v.startswith("profile:") else "profile:" + v[8:].strip()
        raise ValueError("%s must be last, ask, nothing or profile:NAME" % st["key"])
    return v


def find_setting(key):
    for st in SETTINGS:
        if st["key"] == key:
            return st
    raise ValueError("unknown setting %r (see: plasma-reopen-windows config)" % key)


def conf_template():
    """The settings file with every setting explained and commented out."""
    import textwrap
    out = ["# Settings for plasma-reopen-windows.",
           "#",
           "# Every line starting with \"#name =\" shows the default; remove the \"#\" and",
           "# change the value to override it, or use: plasma-reopen-windows config set NAME VALUE",
           "# Running the installer again keeps your values and refreshes these comments.",
           "", "[general]"]
    section = None
    for st in SETTINGS:
        if st["section"] != section:
            section = st["section"]
            out += ["", "# === %s %s" % (section, "=" * (74 - len(section)))]
        out.append("")
        text = "%s - %s. %s" % (st["key"], st["label"], st["help"])
        out += ["# " + l for l in textwrap.wrap(text, 76)]
        out.append("#   Values: %s.   Default: %s" % (setting_values(st), st["default"] or "(empty)"))
        if st.get("example"):
            out.append("#   Example: %s" % st["example"])
        if st.get("when"):
            out += ["#   " + l for l in textwrap.wrap("Change it when: " + st["when"], 74)]
        out.append("#%s = %s" % (st["key"], st["default"]))
    return "\n".join(out) + "\n"


def conf_set(path, key, value, reset=False):
    """Set (or reset to default) one setting, keeping every comment in the file."""
    st = find_setting(key)
    value = st["default"] if reset else check_value(st, value)
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        lines = conf_template().splitlines()
    new = ("#%s = %s" if reset else "%s = %s") % (key, value)
    pat = re.compile(r"^\s*#?\s*%s\s*=" % re.escape(key))
    hits = [i for i, l in enumerate(lines) if pat.match(l)]
    active = [i for i in hits if not lines[i].lstrip().startswith("#")]
    target = (active or hits or [None])[0]
    if target is None:
        if "[general]" not in [l.strip() for l in lines]:
            lines.append("[general]")
        lines.append(new)
    else:
        lines[target] = new
        for i in active[1:]:          # an older duplicate would win otherwise
            lines[i] = "#" + lines[i].lstrip()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(path + ".tmp", path)
    return value


def conf_upgrade(path):
    """Rewrite the file from the current template, keeping the values set in it."""
    cp = configparser.ConfigParser()
    try:
        cp.read(path)
    except configparser.Error:
        pass
    mine = dict(cp["general"]) if cp.has_section("general") else {}
    old = None
    if os.path.exists(path):
        with open(path) as f:
            old = f.read()
    tmp = path + ".new"
    with open(tmp, "w") as f:
        f.write(conf_template())
    for key, value in mine.items():
        try:
            conf_set(tmp, key, value)
        except ValueError as e:
            print("  kept out of the new file: %s (%s)" % (key, e))
    with open(tmp) as f:
        new = f.read()
    if old == new:
        os.remove(tmp)
        return False
    if old is not None:
        with open(path + ".old", "w") as f:
            f.write(old)
    os.replace(tmp, path)
    return True


def load_conf():
    cp = configparser.ConfigParser()
    cp["general"] = DEFAULTS
    try:
        cp.read(CONF)
    except configparser.Error as e:
        print("plasma-reopen-windows: settings file unreadable, using defaults: %s" % e, file=sys.stderr)
    cfg = dict(DEFAULTS)
    for st in SETTINGS:
        try:
            cfg[st["key"]] = check_value(st, cp["general"].get(st["key"], st["default"]))
        except ValueError as e:
            print("plasma-reopen-windows: %s; using %r" % (e, st["default"]), file=sys.stderr)
    return cfg


CFG = load_conf()


def log(msg):
    """Into the log file first; then also to the screen, if there is one (a
    closed output, for example of a caller that went away, must not stop the work)."""
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(time.strftime("%F %T ") + msg + "\n")
    try:
        if os.path.getsize(LOG) > 1048576:      # never more than about 1 MB: keep the newest part
            with open(LOG) as f:
                keep = f.readlines()[-5000:]
            with open(LOG + ".new", "w") as f:
                f.writelines(keep)
            os.replace(LOG + ".new", LOG)
    except OSError:
        pass
    try:
        print(msg, flush=True)
    except (OSError, ValueError):
        pass


PROGRESS_PREFIX = "@@"
_last_progress = None


class ScreenProgress:
    """Progress on the desktop when the settings app is not open (at login,
    from the app menu): one notification with a bar, updated in place, and
    replaced by the result at the end (notify)."""

    def __init__(self):
        self.id = 0
        self.step, self.since, self.shown = None, 0, (None, -1, 0)

    def _bus(self):
        from gi.repository import Gio
        return Gio.bus_get_sync(Gio.BusType.SESSION)

    def _send(self, summary, body, pct=None, timeout=-1, icon="preferences-system-windows"):
        if os.environ.get("PRW_NO_NOTIFICATIONS"):
            return False
        from gi.repository import GLib
        hints = {"value": GLib.Variant("i", pct)} if pct is not None else {}
        if pct is not None:
            hints["transient"] = GLib.Variant("b", True)     # not kept in the history
        try:
            self.id = self._bus().call_sync(
                "org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                "org.freedesktop.Notifications", "Notify",
                GLib.Variant("(susssasa{sv}i)", ("Reopen windows", self.id, icon, summary, body, [],
                                                 hints, timeout)),
                GLib.VariantType("(u)"), 0, 3000).unpack()[0]
            return True
        except Exception:                # no notification service (early at login): nothing shown
            return False

    def show(self, step, done=None, total=None, until=None):
        now = time.time()
        if step != self.step:
            self.step, self.since = step, now
        if total:
            pct = min(100, int(100 * (done or 0) / total))
        elif until and until > self.since:
            pct = min(100, int(100 * (now - self.since) / (until - self.since)))
        else:
            pct = 0
        # the same text as the settings app's bar (the bar shows the count)
        text = step[0].upper() + step[1:] if step else "Working"
        left = int(until - now) if until else 0
        if left > 0:
            text += "  –  up to %d s" % left
        # a new step at once; the same step at most every 2 s
        if (step, pct) == self.shown[:2] or (step == self.shown[0] and now - self.shown[2] < 2):
            return
        if self._send("Working…", text, pct, timeout=0):
            self.shown = (step, pct, now)

    def finish(self, title, body, critical=False):
        """The result in place of the progress; False if nothing was shown."""
        if not self.id:
            return False
        ok = self._send(title, body, None, icon="dialog-warning" if critical else "system-shutdown")
        self.id, self.step, self.shown = 0, None, (None, -1, 0)
        return ok

    def close(self):
        if self.id:
            from gi.repository import GLib
            try:
                self._bus().call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                      "org.freedesktop.Notifications", "CloseNotification",
                                      GLib.Variant("(u)", (self.id,)), None, 0, 3000)
            except Exception:
                pass
            self.id = 0


_screen = ScreenProgress()


def _close_progress():
    _screen.close()


import atexit                            # noqa: E402  (a command that ends without a result: no bar left behind)
atexit.register(_close_progress)


def progress(step, done=None, total=None, until=None):
    """One step for the settings app's progress bar: a line on stdout, only
    when the app started this command (it reads these lines as they come).
    done/total: a count ("3 of 11"); until: a time limit (epoch seconds) the
    app counts down to."""
    global _last_progress
    if not os.environ.get("PRW_FROM_APP"):
        _screen.show(step, done, total, until)
        return
    line = PROGRESS_PREFIX + json.dumps({"step": step, "done": done, "total": total,
                                         "until": round(until) if until else None})
    if line == _last_progress:
        return
    _last_progress = line
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass


def notify(title, body="", critical=False):
    """A desktop notification - or, when the settings app started this
    command, a line for the app, which shows it in its own window (KDE puts
    notification pop-ups on the main screen, wherever the app is)."""
    if os.environ.get("PRW_FROM_APP"):
        try:
            print(PROGRESS_PREFIX + json.dumps({"notice": title, "text": body, "critical": critical}), flush=True)
            return
        except (OSError, ValueError):
            pass
    if not critical and _screen.finish(title, body):
        return
    _screen.close()
    if os.environ.get("PRW_NO_NOTIFICATIONS"):
        return
    cmd = ["notify-send", "-a", "Reopen windows", "-i", "system-shutdown"]
    if critical:
        cmd += ["-u", "critical"]
    subprocess.run(cmd + [title, body], check=False)
