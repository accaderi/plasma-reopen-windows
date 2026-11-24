"""plasma-reopen-windows: paths, settings, log and notifications."""

import configparser
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
    dict(key="restore_timeout", default="120", type="int", min=10, max=900, section="Restoring",
         label="Seconds to wait for windows after login",
         help="Windows that appear later are not placed.",
         when="slow apps (large projects) take longer than 2 minutes to open."),
    dict(key="max_age_days", default="7", type="int", min=1, max=365, section="Restoring",
         label="Oldest session to restore (days)",
         help="A saved session older than this is ignored at login."),
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
    dict(key="profile_load", default="unite", type="choice", choices=["unite", "reload"],
         section="Profiles", label="What a profile's menu entry does",
         help="For the menu entries \"Load profile: NAME\" and for profile:NAME at login. "
              "unite = start the profile's apps and windows that are missing and arrange "
              "all of them, closing nothing; reload = also close the apps that are not in "
              "the profile. Either can be undone once (Undo last)."),
    dict(key="when_unsure", default="ask", type="choice", choices=["ask", "open", "use", "skip"],
         section="Profiles", label="A saved window is not clearly found",
         help="When a saved window's title matches none of the open windows, but its app "
              "has other windows open (for example a browser window with another tab in "
              "front): ask = show a window to choose, per saved window; open = open the "
              "saved one again; use = move an open window of that app to its place; "
              "skip = leave it out."),
]


DEFAULTS = {s["key"]: s["default"] for s in SETTINGS}


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


def notify(title, body="", critical=False):
    cmd = ["notify-send", "-a", "Reopen windows", "-i", "system-shutdown"]
    if critical:
        cmd += ["-u", "critical"]
    subprocess.run(cmd + [title, body], check=False)
