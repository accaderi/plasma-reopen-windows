"""plasma-reopen-windows: running small KWin scripts."""

import json
import os
import tempfile
import warnings
from gi.repository import Gio, GLib

warnings.filterwarnings("ignore", category=DeprecationWarning)   # Gio register_object


# KWin scripts answer to this name; one per process, so two runs at the same
# time (restore at login + a command, the settings app) never get each
# other's answers
BUS_NAME = "io.github.plasma_reopen_windows.p%d" % os.getpid()


BUS_XML = ('<node><interface name="%s"><method name="Result">'
           '<arg type="s" direction="in"/></method></interface></node>' % BUS_NAME)


# ---- talking to KWin ---------------------------------------------------------
class KWin:
    """Runs small KWin scripts; they send their result back over D-Bus.
    One connection per process: KWin() always returns the same object, because
    the answer channel can be registered on the bus only once."""

    _shared = None

    def __new__(cls):
        if cls._shared is None:
            cls._shared = super().__new__(cls)
            cls._shared._ready = False
        return cls._shared

    def __init__(self):
        if self._ready:
            return
        self._ready = True
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION)
        self.result = None
        self.loop = GLib.MainLoop()
        info = Gio.DBusNodeInfo.new_for_xml(BUS_XML).interfaces[0]
        self.bus.register_object("/", info, self._on_call)
        self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                           "RequestName", GLib.Variant("(su)", (BUS_NAME, 4)), None, 0, -1)
        self.tmp = tempfile.mkdtemp(prefix="plasma-reopen-windows-")

    def _on_call(self, conn, sender, path, iface, method, params, inv):
        self.result = params.unpack()[0]
        inv.return_value(None)
        self.loop.quit()

    def _kwin(self, path, iface, method, args, sig):
        return self.bus.call_sync("org.kde.KWin", path, iface, method, args,
                                  GLib.VariantType(sig), 0, 10000).unpack()

    def run(self, js, timeout=10):
        """Run a script; it must end with result(<json-able value>)."""
        src = ("function result(v) { callDBus('%s', '/', '%s', 'Result', JSON.stringify(v)); }\n"
               % (BUS_NAME, BUS_NAME)) + js
        path = os.path.join(self.tmp, "script.js")
        with open(path, "w") as f:
            f.write(src)
        name = "plasma-reopen-windows"
        try:
            self._kwin("/Scripting", "org.kde.kwin.Scripting", "unloadScript", GLib.Variant("(s)", (name,)), "(b)")
        except GLib.Error:
            pass
        sid = self._kwin("/Scripting", "org.kde.kwin.Scripting", "loadScript",
                         GLib.Variant("(ss)", (path, name)), "(i)")[0]
        self.result = None
        self._kwin("/Scripting/Script%d" % sid, "org.kde.kwin.Script", "run", None, "()")
        timer = GLib.timeout_add(timeout * 1000, self.loop.quit)
        self.loop.run()
        if self.result is not None:
            GLib.source_remove(timer)
        self._kwin("/Scripting", "org.kde.kwin.Scripting", "unloadScript", GLib.Variant("(s)", (name,)), "(b)")
        if self.result is None:
            raise RuntimeError("KWin script did not answer")
        return json.loads(self.result)

    def windows(self):
        return self.run(JS_WINDOWS)

    def call(self, service, path, iface, method, args=None, sig="()"):
        return self.bus.call_sync(service, path, iface, method, args,
                                  GLib.VariantType(sig), 0, 10000).unpack()


JS_WINDOWS = r"""
const stack = workspace.stackingOrder;
let out = [];
for (const w of workspace.windowList()) {
    if (!w.normalWindow || w.skipTaskbar) continue;
    const g = w.frameGeometry;
    out.push({
        id: w.internalId.toString(),
        app: w.desktopFileName || w.resourceClass,
        cls: w.resourceClass,
        pid: w.pid,
        caption: w.caption,
        x: g.x, y: g.y, width: g.width, height: g.height,
        output: w.output ? w.output.name : "",
        desktops: w.desktops.map(d => d.id),
        activities: w.activities,
        minimized: w.minimized,
        maximize: w.maximizeMode,
        fullscreen: w.fullScreen,
        keepAbove: w.keepAbove,
        stack: stack.indexOf(w),
    });
}
result({ windows: out, outputs: workspace.screens.map(s => s.name),
         screens: workspace.screens.map(s => ({ name: s.name, manufacturer: s.manufacturer,
             model: s.model, serial: s.serialNumber,
             x: s.geometry.x, y: s.geometry.y, width: s.geometry.width, height: s.geometry.height })) });
"""


JS_CLOSE = r"""
const ids = %s;
let n = 0;
for (const w of workspace.windowList()) {
    if (ids.indexOf(w.internalId.toString()) >= 0) { w.closeWindow(); n++; }
}
result(n);
"""


JS_PLACE = r"""
const moves = %s;
let done = [];
for (const w of workspace.windowList()) {
    const m = moves[w.internalId.toString()];
    if (!m) continue;
    try {
        if (m.desktops.length) {
            const d = workspace.desktops.filter(d => m.desktops.indexOf(d.id) >= 0);
            if (d.length) w.desktops = d;
        }
        if (w.fullScreen) w.fullScreen = false;
        if (w.maximizeMode) w.setMaximize(false, false);
        if (m.geometry) w.frameGeometry = m.geometry;
        if (m.maximize) w.setMaximize((m.maximize & 1) != 0, (m.maximize & 2) != 0);
        if (m.fullscreen) w.fullScreen = true;
        w.keepAbove = m.keepAbove;
        w.minimized = !!m.minimized;      // also brings back a window minimized since
        done.push(w.internalId.toString());
    } catch (e) { done.push("error " + e); }
}
result(done);
"""


JS_FRONT = r"""
// the settings app on top again after a command it started
for (const w of workspace.windowList()) {
    if (w.desktopFileName == %s && !w.minimized) {
        if (workspace.raiseWindow) workspace.raiseWindow(w);
        workspace.activeWindow = w;
    }
}
result(0);
"""


JS_ACTIVATE = r"""
// make one window the active one (a browser adds pages to its active window)
let ok = false;
for (const w of workspace.windowList()) {
    if (w.internalId.toString() == %s) { workspace.activeWindow = w; ok = true; }
}
result(ok);
"""


JS_RAISE = r"""
const ids = %s;      // bottom to top
let byId = {};
for (const w of workspace.windowList()) byId[w.internalId.toString()] = w;
let top = null, n = 0;
for (const id of ids) {
    const w = byId[id];
    if (!w || w.minimized) continue;
    if (workspace.raiseWindow) workspace.raiseWindow(w); else workspace.activeWindow = w;
    top = w; n++;
}
if (top) workspace.activeWindow = top;
result(n);
"""
