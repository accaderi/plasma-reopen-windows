"""plasma-reopen-windows: media players over MPRIS (videos, music)."""

import difflib
import json
import os
import re
import subprocess
import sys
import time
from gi.repository import GLib
from .config import CFG, STATE_DIR, log

LATE = os.path.join(STATE_DIR, "late-videos.json")   # saved videos still to be put back after a restore


# ---- videos and sound output -------------------------------------------------
MPRIS = "org.mpris.MediaPlayer2"
# Chrome gives a live stream an endless length (the largest number) and a
# position that is not the place in the stream (measured: 4 s at 10.5 h);
# Plasma's browser integration reports the same video with its real place.
ENDLESS = 2 ** 62


def similar(a, b):
    return difflib.SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def mpris_players(bus):
    """Media players on D-Bus. A browser reports only one video, the one
    started last (measured with Chrome and Firefox; Firefox only audible ones)."""
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
        length = md.get("mpris:length", 0)
        try:
            pid = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                "GetConnectionUnixProcessID", GLib.Variant("(s)", (n,)),
                                GLib.VariantType("(u)"), 0, 3000).unpack()[0]
        except GLib.Error:
            pid = 0
        out.append({"bus": n, "title": md.get("xesam:title", ""), "trackid": md.get("mpris:trackid", ""),
                    "url": md.get("xesam:url", ""), "pid": pid,
                    "position": props.get("Position", 0), "status": props.get("PlaybackStatus", ""),
                    "can_seek": props.get("CanSeek", False), "length": length, "live": length >= ENDLESS})
    return out


def same_video(a, b):
    """Titles of the same video: equal, or one is the other plus the site's name
    after a separator ("X" and "X - YouTube": a browser integration leaves the
    site out). Not a similarity score: "Episode 1" and "Episode 2" are different."""
    a, b = (a or "").strip().lower(), (b or "").strip().lower()
    if not a or not b:
        return False
    short, long = sorted((a, b), key=len)
    return long == short or (long.startswith(short) and re.match(r"\s+[-—|·:]\s", long[len(short):]) is not None)


class Unknown:
    """Pauses videos that start playing during a restore or profile load but
    were not saved: their position is not known (a browser reports only one
    video), and they should not suddenly play out loud. What was playing
    before the load is left alone."""

    def __init__(self, bus, saved_titles):
        self.bus, self.saved = bus, list(saved_titles)
        self.before = {(p["bus"], p["title"]) for p in mpris_players(bus) if p["status"] == "Playing"}
        self.paused = []

    def step(self):
        for p in mpris_players(self.bus):
            if p["status"] != "Playing" or not p["title"] or (p["bus"], p["title"]) in self.before:
                continue
            if any(same_video(p["title"], t) for t in self.saved):
                continue                       # a saved video: Media puts it back
            try:
                self.bus.call_sync(p["bus"], "/org/mpris/MediaPlayer2", MPRIS + ".Player", "Pause",
                                   None, None, 0, 3000)
            except GLib.Error:
                continue
            if p["title"] not in self.paused:
                self.paused.append(p["title"])

    def report(self):
        if self.paused:
            log("paused %d videos that were not saved (position unknown): %s" % (
                len(self.paused), "; ".join(t[:50] for t in self.paused)))


class Media:
    """Puts videos back at their saved position, paused or playing."""

    def __init__(self, bus, saved):
        self.bus = bus
        # a video near its start that was playing needs nothing
        self.todo = [dict(m, tries=0) for m in saved
                     if m["status"] == "Paused" or m["position"] > 5000000]
        self.done = []
        self.start = time.time()

    def step(self):
        if not self.todo:
            return
        players = mpris_players(self.bus)
        for m in list(self.todo):
            match = [p for p in players if same_video(p["title"], m["title"])]
            if not match:
                continue        # tab not loaded yet, or an ad is playing first
            for p in match:     # the same video reported twice: neither is another saved one
                players.remove(p)
            # a live stream: only a report with a real length knows the place
            # in it (Chrome's own one would jump to the very beginning)
            p = next((p for p in match if not p["live"]), match[0])
            pos = p["position"]
            # a paused video must show the very picture (1 s); a playing one
            # moves on, by at most the time since the restore began (a live
            # stream or a site's own "resume" can start it much further on)
            tol = 1000000 if m["status"] == "Paused" else 3000000
            ahead = tol + (int((time.time() - self.start) * 1000000) if m["status"] == "Playing" else 0)
            no_place = p["live"] or (m.get("live") and not m.get("place"))   # nothing to seek to
            pos_ok = no_place or not p["can_seek"] or m["position"] - tol <= pos <= m["position"] + ahead
            if pos_ok and p["status"] == m["status"]:
                log("media: \"%s\" %s, %s" % (m["title"][:50], "live, left where the stream is" if no_place
                                                else "back at %d s" % (pos // 1000000), m["status"].lower()))
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


def watch_later(items):
    """Saved videos whose tab had not loaded when the restore ended: a
    background process keeps looking for them for video_late seconds, and
    puts each back (paused at its place) when it appears (measured
    2026-11-27: a video saved paused had not appeared after video_wait, and
    played out loud when its tab was shown later). Returns whether it watches."""
    secs = int(CFG.get("video_late"))
    if not items or secs <= 0:
        return False
    try:
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(LATE, "w") as f:
            json.dump({"until": time.time() + secs, "items": [
                {k: v for k, v in m.items() if k != "tries"} for m in items]}, f)
    except OSError as e:
        log("media: could not keep watching for late videos: %s" % e)
        return False
    log("media: watching for %s for %d s more: %s" % (
        "%d videos" % len(items) if len(items) > 1 else "1 video", secs,
        "; ".join(m["title"][:50] for m in items)))
    if os.environ.get("PRW_NO_BACKGROUND"):          # tests
        return True
    try:
        subprocess.Popen([sys.executable, os.path.realpath(sys.argv[0]), "media-late"], start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        log("media: could not keep watching for late videos: %s" % e)
        return False
    return True


def watch_late(bus, poll=1.0):
    """The background part of watch_later: until the saved time, or until all
    are put back. A newer restore replaces the file and this one stops."""
    try:
        with open(LATE) as f:
            job = json.load(f)
    except (OSError, ValueError):
        return
    stamp = os.stat(LATE).st_mtime
    media = Media(bus, job["items"])
    media.start = time.time() - 3600         # a playing video may be anywhere on by now
    while media.todo and time.time() < job["until"]:
        try:
            if os.stat(LATE).st_mtime != stamp:
                return
        except OSError:
            return
        media.step()
        time.sleep(poll)
    for m in media.done:
        log("media: \"%s\" put back after the restore" % m["title"][:50])
    if media.todo:
        log("media: \"%s\" not seen in time after the restore" % "; ".join(m["title"][:50] for m in media.todo))
    try:
        if os.stat(LATE).st_mtime == stamp:
            os.remove(LATE)
    except OSError:
        pass
