"""plasma-reopen-windows: media players over MPRIS (videos, music)."""

import difflib
from gi.repository import GLib
from .config import log


# ---- videos and sound output -------------------------------------------------
MPRIS = "org.mpris.MediaPlayer2"


def similar(a, b):
    return difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def mpris_players(bus):
    """Media players on D-Bus (Chrome reports each tab playing a video)."""
    names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                          "ListNames", None, GLib.VariantType("(as)"), 0, 5000).unpack()[0]
    out = []
    for n in names:
        if not n.startswith(MPRIS + "."):
            continue
        try:
            props = bus.call_sync(n, "/org/mpris/MediaPlayer2", "org.freedesktop.DBus.Properties", "GetAll",
                                  GLib.Variant("(s)", (MPRIS + ".Player",)), GLib.VariantType("(a{sv})"),
                                  0, 3000).unpack()[0]
        except GLib.Error:
            continue
        md = props.get("Metadata", {})
        try:
            pid = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "GetConnectionUnixProcessID", GLib.Variant("(s)", (n,)),
                                GLib.VariantType("(u)"), 0, 3000).unpack()[0]
        except GLib.Error:
            pid = 0
        out.append({"bus": n, "title": md.get("xesam:title", ""), "trackid": md.get("mpris:trackid", ""),
                    "url": md.get("xesam:url", ""), "pid": pid,
                    "position": props.get("Position", 0), "status": props.get("PlaybackStatus", ""),
                    "can_seek": props.get("CanSeek", False)})
    return out


class Media:
    """Puts videos back at their saved position, paused or playing."""

    def __init__(self, bus, saved):
        self.bus = bus
        # a video near its start that was playing needs nothing
        self.todo = [dict(m, tries=0) for m in saved
                     if m["status"] == "Paused" or m["position"] > 5000000]
        self.done = []

    def step(self):
        if not self.todo:
            return
        players = mpris_players(self.bus)
        for m in list(self.todo):
            match = [p for p in players if similar(p["title"], m["title"]) >= 0.85]
            if not match:
                continue        # tab not loaded yet, or an ad is playing first
            p = match[0]
            players.remove(p)
            pos = p["position"]
            pos_ok = not p["can_seek"] or (pos >= m["position"] - 3000000 and
                                           (m["status"] == "Playing" or pos <= m["position"] + 3000000))
            if pos_ok and p["status"] == m["status"]:
                log("media: \"%s\" back at %d s, %s" % (m["title"][:50], pos // 1000000, m["status"].lower()))
                self.todo.remove(m)
                self.done.append(m)
                continue
            if m["tries"] >= 5:
                log("media: could not restore \"%s\"" % m["title"][:50])
                self.todo.remove(m)
                continue
            m["tries"] += 1
            try:
                if m["status"] == "Paused" and p["status"] != "Paused":
                    self._call(p["bus"], "Pause")
                if not pos_ok and p["trackid"]:
                    self._call(p["bus"], "SetPosition", GLib.Variant("(ox)", (p["trackid"], m["position"])))
                if m["status"] == "Playing" and p["status"] != "Playing":
                    self._call(p["bus"], "Play")
            except GLib.Error as e:
                log("media: %s" % e.message)

    def pending(self):
        """Videos still to be put back (paused ones, and playing ones that
        must jump to their position): keep the sound off until then."""
        return bool(self.todo)

    def _call(self, name, method, args=None):
        self.bus.call_sync(name, "/org/mpris/MediaPlayer2", MPRIS + ".Player", method, args, None, 0, 3000)

    def report(self):
        for m in self.todo:
            log("media: \"%s\" did not appear (tab not opened yet?)" % m["title"][:50])
