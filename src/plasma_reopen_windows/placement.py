"""plasma-reopen-windows: where the menu entries also appear (favorites in the
Application Launcher, panel buttons, desktop icons, keyboard shortcuts).

All through Plasma's own interfaces; each change can be undone the same way.
The fixed power row of the Application Launcher (Sleep, Restart, Shut Down)
cannot be extended: it is built into Plasma.
"""

import os
import shutil
import subprocess
from gi.repository import Gio, GLib

APPS_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), "applications")
ENTRIES = [
    ("plasma-reopen-windows-shutdown", "Save & Shut Down"),
    ("plasma-reopen-windows-reboot", "Save & Restart"),
    ("plasma-reopen-windows-update", "Save & Update & Restart"),
    ("plasma-reopen-windows-save-now", "Save Windows Now"),
    ("plasma-reopen-windows-restore-autosave", "Restore Last Autosave"),
    ("plasma-reopen-windows-settings", "Reopen Windows (settings)"),
]
FAV_AGENT = "org.kde.plasma.favorites.applications"
ICON_WIDGET = "org.kde.plasma.icon"


def _bus():
    return Gio.bus_get_sync(Gio.BusType.SESSION)


def _call(service, path, iface, method, args=None, sig=None):
    return _bus().call_sync(service, path, iface, method, args,
                            GLib.VariantType(sig) if sig else None, 0, 10000).unpack()


def entries(profile_slugs=()):
    """(desktop id, label) of everything that can be placed."""
    out = [e for e in ENTRIES if os.path.exists(desktop_path(e[0]))]
    for slug, name in profile_slugs:
        out.append(("plasma-reopen-windows-profile-" + slug, "Load profile: " + name))
    return out


def desktop_path(desktop):
    return os.path.join(APPS_DIR, desktop + ".desktop")


# ---- favorites in the Application Launcher ---------------------------------------
LINKING = ("org.kde.ActivityManager", "/ActivityManager/Resources/Linking",
           "org.kde.ActivityManager.ResourcesLinking")


def is_favorite(desktop):
    return _call(*LINKING, "IsResourceLinkedToActivity",
                 GLib.Variant("(sss)", (FAV_AGENT, "applications:%s.desktop" % desktop, ":global")), "(b)")[0]


def set_favorite(desktop, on):
    method = "LinkResourceToActivity" if on else "UnlinkResourceFromActivity"
    _call(*LINKING, method, GLib.Variant("(sss)", (FAV_AGENT, "applications:%s.desktop" % desktop, ":global")))


# ---- panel buttons -----------------------------------------------------------------
def _plasma(script):
    return _call("org.kde.plasmashell", "/PlasmaShell", "org.kde.PlasmaShell", "evaluateScript",
                 GLib.Variant("(s)", (script,)), "(s)")[0]


def panels():
    """[(index, description)] of the panels."""
    out = _plasma('for (var i = 0; i < panels().length; i++) {'
                  '  var p = panels()[i]; print(i + "\\t" + p.location + "\\t" + p.screen + "\\n"); }')
    res = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            res.append((int(parts[0]), "Panel %d (%s, screen %d)" % (int(parts[0]) + 1, parts[1], int(parts[2]) + 1)))
    return res


def _find_buttons_js(desktop):
    return ('var found = [];'
            'for (var i = 0; i < panels().length; i++) {'
            '  var p = panels()[i], ids = p.widgetIds;'
            '  for (var j = 0; j < ids.length; j++) {'
            '    var w = p.widgetById(ids[j]);'
            '    if (w && w.type == "%s") {'
            '      w.currentConfigGroup = ["General"];'
            '      if (String(w.readConfig("url", "")).indexOf("/%s.desktop") >= 0) found.push([i, w]); } } }'
            % (ICON_WIDGET, desktop))


def panel_buttons(desktop):
    """Panel indexes that have a button for this entry."""
    out = _plasma(_find_buttons_js(desktop) + 'for (var k = 0; k < found.length; k++) print(found[k][0] + "\\n");')
    return sorted({int(x) for x in out.split() if x.strip().isdigit()})


def add_panel_button(desktop, panel):
    if panel in panel_buttons(desktop):
        return
    _plasma('var p = panels()[%d]; var w = p.addWidget("%s");'
            'w.currentConfigGroup = ["General"]; w.writeConfig("url", "file://%s"); w.reloadConfig();'
            % (panel, ICON_WIDGET, desktop_path(desktop)))


def remove_panel_buttons(desktop):
    _plasma(_find_buttons_js(desktop) + 'for (var k = 0; k < found.length; k++) found[k][1].remove();')


# ---- desktop icons --------------------------------------------------------------------
def desktop_dir():
    try:
        d = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        d = ""
    return d or os.path.expanduser("~/Desktop")


def has_desktop_icon(desktop):
    return os.path.exists(os.path.join(desktop_dir(), desktop + ".desktop"))


def set_desktop_icon(desktop, on):
    target = os.path.join(desktop_dir(), desktop + ".desktop")
    if on:
        os.makedirs(desktop_dir(), exist_ok=True)
        shutil.copyfile(desktop_path(desktop), target)
        os.chmod(target, 0o755)        # Plasma starts desktop files only when executable
    elif os.path.exists(target):
        os.remove(target)


# ---- keyboard shortcuts -----------------------------------------------------------------
ACCEL = ("org.kde.kglobalaccel", "/kglobalaccel", "org.kde.KGlobalAccel")
SET_PRESENT, NO_AUTOLOADING = 2, 4


def _action(desktop, label):
    # same form as System Settings uses for app shortcuts: component = the desktop file
    return [desktop + ".desktop", "_launch", label, "Launch %s" % label]


def shortcut(desktop, label):
    """Current key (Qt key code) or 0."""
    try:
        keys = _call(*ACCEL, "shortcutKeys", GLib.Variant("(as)", (_action(desktop, label),)), "(a(ai))")[0]
    except GLib.Error:
        return 0
    return keys[0][0][0] if keys and keys[0] and keys[0][0] else 0


def shortcut_owner(key, desktop):
    """Who already uses this key ("component: action"), or None."""
    if not key:
        return None
    res = _call(*ACCEL, "globalShortcutsByKey", GLib.Variant("((ai)(i))", (([key],), (0,))),
                "(a(ssssssaiai))")[0]
    for comp, action, comp_name, action_name, *_ in res:
        if comp != desktop + ".desktop":
            return "%s: %s" % (comp_name or comp, action_name or action)
    return None


def set_shortcut(desktop, label, key):
    """Set (key) or remove (0) the shortcut; returns the owner if the key is taken."""
    owner = shortcut_owner(key, desktop)
    if owner:
        return owner
    action = _action(desktop, label)
    _call(*ACCEL, "doRegister", GLib.Variant("(as)", (action,)))
    keys = [([key],)] if key else []
    _call(*ACCEL, "setShortcutKeys", GLib.Variant("(asa(ai)u)", (action, keys, SET_PRESENT | NO_AUTOLOADING)),
          "(a(ai))")
    return None
