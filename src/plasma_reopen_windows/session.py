"""plasma-reopen-windows: saving and restoring a session."""

import configparser
import fnmatch
import json
import os
import subprocess
import time
import urllib.parse
import xml.etree.ElementTree as ET
from .apps import CLOSE_TIMEOUT, ORDER, app_kind, app_name, find_desktop, ignored, konsole_tabs, launch_spec, quit_app, real_path, relaunch, wait_closed
from .audio import default_sink, mute_outputs, set_sound_output, unmute_outputs, wait_for_quiet
from .config import CFG, STATE, STATE_DIR, log, notify, progress
from .kwin import JS_PLACE, JS_RAISE, KWin
from .media import Media, Unknown, mpris_players, similar
from .screens import annotate, target_geometry


# ---- save --------------------------------------------------------------------
def snapshot(kw, only=None):
    """Everything about the open apps, without closing anything: windows (with
    their monitor), Konsole tabs, files open in players, videos, sound output."""
    snap = kw.windows()
    wins = [w for w in snap["windows"] if not ignored(w["app"])]
    if only:
        wins = [w for w in wins if w["app"] in only or app_kind(w["app"]) in only]
    annotate(wins, snap["screens"])
    apps = {}
    for w in sorted(wins, key=lambda w: w["stack"]):
        apps.setdefault(w["app"], []).append(w)
    konsole, busy = {}, []
    for w in apps.get("org.kde.konsole", []):
        if w["pid"] not in konsole:
            tabs, b = konsole_tabs(kw, w["pid"])
            konsole[w["pid"]] = tabs or []
            busy += b
    players = mpris_players(kw.bus)
    app_of = {w["pid"]: w["app"] for w in wins}
    media = [dict({k: p[k] for k in ("title", "position", "status")}, app=app_of.get(p["pid"]))
             for p in players if p["title"] and p["status"] in ("Playing", "Paused")]
    from .profiles import one_per_video
    media = one_per_video(media)          # the browser integration reports videos a second time
    files = {}                    # files open in media players (VLC ...), per process
    for p in players:
        if p["url"].startswith("file://") and p["pid"]:
            files.setdefault(p["pid"], []).append(real_path(urllib.parse.unquote(p["url"][7:])))
    from .reopen import details
    return {"snap": snap, "wins": wins, "apps": apps, "konsole": konsole, "busy": busy,
            "media": media, "files": files, "sound": default_sink(), "details": details(kw.bus, wins, files)}


def close_apps(kw, apps, konsole, files, what="Shutdown"):
    """Quit apps as a whole, in a safe order. Returns their launch specs, or None
    if one did not close (then the ones closed so far are started again)."""
    launches = []
    timeout = int(CFG.get("close_timeout"))
    for n, app in enumerate(sorted(apps, key=lambda a: ORDER[app_kind(a)])):
        progress("closing %s" % app_name(find_desktop(app, apps[app][0]) or app), n, len(apps))
        kind, ws = app_kind(app), apps[app]
        launch = launch_spec(app, kind, ws, konsole, files)
        pids = quit_app(kw, app, kind, ws)
        limit = CLOSE_TIMEOUT.get(app, timeout)
        if not wait_closed(kw, app, kind, ws, pids, limit):
            log("cancelled: %s did not close within %d s" % (app, limit))
            notify("%s cancelled" % what, "%s did not close (maybe it asks to save something). "
                   "Close it yourself, then try again. Reopening the apps closed so far." % app, critical=True)
            relaunch(launches)
            return None
        log("closed %s" % app)
        if launch:
            launches.append(launch)
        else:
            log("no desktop file for %s, it will not be started again" % app)
    return launches


def busy_message(busy):
    return ", ".join("%s in %s" % (prog, cwd) for cwd, prog in busy)


def do_save(only=None):
    kw = KWin()
    st = snapshot(kw, only)
    apps = st["apps"]
    if st["busy"]:
        msg = busy_message(st["busy"])
        log("cancelled: programs running in Konsole: " + msg)
        notify("Shutdown cancelled", "Programs are still running in Konsole: %s. "
               "Finish or stop them, then try again." % msg, critical=True)
        return None

    if CFG.get("confirm") == "yes" and only is None:
        names = ", ".join(sorted(app_name(find_desktop(a, apps[a][0]) or a) for a in apps)) or "no apps"
        r = subprocess.run(["kdialog", "--title", "Reopen windows", "--yesno",
                            "Close all apps, save the session and continue?\n\nOpen: %s" % names],
                           check=False)
        if r.returncode != 0:
            log("cancelled by the user")
            return None

    for m in st["media"]:
        log("media: \"%s\" %s at %d s" % (m["title"][:50], m["status"].lower(), m["position"] // 1000000))
    if st["sound"]:
        log("sound output: %s" % (st["sound"]["description"] or st["sound"]["name"]))

    notify("Saving session", "Closing %d apps…" % len(apps))
    log("saving: " + ", ".join("%s (%d)" % (a, len(ws)) for a, ws in apps.items()))
    launches = close_apps(kw, apps, st["konsole"], st["files"])
    if launches is None:
        return None

    state = {"saved": time.time(), "outputs": st["snap"]["outputs"], "screens": st["snap"]["screens"],
             "launch": launches, "windows": st["wins"], "media": st["media"], "sound": st["sound"]}
    write_json(STATE, state)
    log("saved %d apps, %d windows to %s" % (len(launches), len(st["wins"]), STATE))
    return state


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(data, f, indent=1, ensure_ascii=False)
    os.replace(path + ".tmp", path)


# ---- restore -----------------------------------------------------------------
def strip_caption(c):
    for sep in (" — ", " - "):
        if sep in c:
            c = c.rsplit(sep, 1)[0]
    return c.strip().lower()


RWP_DIR = os.path.expanduser("~/.local/share/kwin/scripts/rememberwindowpositions")


def rwp_config():
    """Settings of the KWin script "Remember Window Positions", or None when it
    is off: its mode and app lists (yours from kwinrc, else its defaults)."""
    kwinrc = configparser.ConfigParser(strict=False, interpolation=None)
    kwinrc.optionxform = str
    kwinrc.read(os.path.expanduser("~/.config/kwinrc"))
    if kwinrc.get("Plugins", "rememberwindowpositionsEnabled", fallback="false") != "true":
        return None
    cfg = {"restoreType": "2", "blacklist": "", "whitelist": ""}
    try:
        for e in ET.parse(os.path.join(RWP_DIR, "contents/config/main.xml")).getroot().iter():
            if e.tag.endswith("entry") and e.get("name") in cfg:
                d = [c for c in e if c.tag.endswith("default")]
                if d:
                    cfg[e.get("name")] = d[0].text or ""
    except (OSError, ET.ParseError):
        pass
    for key in cfg:
        v = kwinrc.get("Script-rememberwindowpositions", key, fallback=None)
        if v is not None:
            cfg[key] = v.replace("\\n", "\n")
    return {"type": int(cfg["restoreType"] or 2),
            "black": [l.strip() for l in cfg["blacklist"].splitlines() if l.strip()],
            "white": [l.strip() for l in cfg["whitelist"].splitlines() if l.strip()]}


def rwp_handles(rwp, w, count):
    """Does "Remember Window Positions" place this window itself?
    Its modes: 0 all apps, 1 apps with several windows, 2 all but its blacklist,
    3 only its whitelist."""
    names = {w.get("cls", ""), w.get("app", "")} - {""}
    listed = lambda lst: any(fnmatch.fnmatch(n, pat) for n in names for pat in lst)
    if rwp["type"] == 0:
        return True
    if rwp["type"] == 1:
        return count > 1
    if rwp["type"] == 3:
        return listed(rwp["white"])
    return not listed(rwp["black"])


def placement_for(state):
    """Which saved windows this tool places: all, none, or (auto, with
    "Remember Window Positions" on) only the apps that script leaves alone."""
    mode = CFG.get("place_windows")
    if mode == "no":
        return set()
    every = {app for app in (w["app"] for w in state["windows"])}
    if mode == "yes":
        return every
    rwp = rwp_config()
    if rwp is None:
        return every
    count = {}
    for w in state["windows"]:
        count[w["app"]] = count.get(w["app"], 0) + 1
    mine = {w["app"] for w in state["windows"] if not rwp_handles(rwp, w, count[w["app"]])}
    log("\"Remember Window Positions\" places %s; this tool places %s" % (
        ", ".join(sorted(every - mine)) or "nothing", ", ".join(sorted(mine)) or "nothing"))
    return mine


def do_restore(state=None):
    if state is None:
        if not os.path.exists(STATE):
            return
        with open(STATE) as f:
            state = json.load(f)
        os.replace(STATE, STATE + ".restored")
    age = (time.time() - state["saved"]) / 86400
    if age > float(CFG.get("max_age_days")):
        log("saved session is %.0f days old, not restored" % age)
        return
    log("restoring %d apps" % len(state["launch"]))
    kw = KWin()
    before = {w["id"] for w in kw.windows()["windows"]}
    bring_back(kw, state["launch"], state["windows"], before, placement_for(state),
               media_items=state.get("media", []), sound=state.get("sound"), pause_unknown=True, login=True)
    notify("Session restored", "%d apps started again" % len(state["launch"]))


def bring_back(kw, launches, windows, ignore_ids, move, media_items=(), sound=None,
               timeout=None, settle=20, strict_ids=(), fixed=None, pause_unknown=False, login=False):
    """Start apps, then put windows in place (matched by app and title), seek
    and pause videos, select the sound output, and restore the stacking order.
    ignore_ids: windows that must not be matched (already open before)."""
    media = Media(kw.bus, list(media_items))
    # videos that start by themselves but were not saved: paused (watched for a while,
    # a background tab can start late)
    watch = int(CFG.get("video_watch"))
    unknown = Unknown(kw.bus, [m["title"] for m in media_items]) \
        if pause_unknown and CFG.get("pause_unknown_videos") == "yes" and watch > 0 else None
    # Videos start playing as soon as their tab loads, before they can be
    # paused again (saved ones put back, unknown ones paused): keep the sound
    # off until then (at most 30 s).
    muting = media.pending() or bool(unknown and launches)
    if muting and login:
        wait_for_quiet()          # let the login sound finish
    muted = mute_outputs() if muting else []
    placer = None
    try:
        relaunch(launches, kw)
        unmute_at = time.time() + 30      # counted from when all apps are started
        watch_until = time.time() + watch if unknown else 0
        quiet_until = time.time() + min(watch, 12) if unknown and launches else 0   # they start within seconds
        placer = Placer(kw, {"windows": windows}, set(ignore_ids), move=move, strict_ids=strict_ids, fixed=fixed)
        end = time.time() + (timeout or int(CFG.get("restore_timeout")))
        while time.time() < end:
            if sound and set_sound_output(sound):
                sound = None
            if muting:
                muted += mute_outputs(quiet=True)     # monitors that woke up late
            media.step()
            if unknown:
                unknown.step()
            if muting and ((not media.pending() and time.time() > quiet_until) or time.time() > unmute_at):
                unmute_outputs(muted)
                muted, muting = [], False
            placer.step()
            report_wait(placer, media, len(windows) + len(fixed or {}), end, settle, unmute_at if muting else 0,
                        watch_until)
            # done, or no further window for a while (an app may open fewer windows)
            if not sound and not media.todo and time.time() > watch_until and \
                    (not placer.todo or time.time() - placer.last_match > settle):
                break
            time.sleep(1.5 if launches else 0.4)
        if sound:
            set_sound_output(sound, final=True)
    finally:
        unmute_outputs(muted)
    media.report()
    if unknown:
        unknown.report()
    if placer:
        placer.report()
        if CFG.get("restore_stacking") == "yes":
            time.sleep(2 if launches else 0.3)    # let other scripts finish placing windows first
            placer.restack()
    return placer


def report_wait(placer, media, total, end, settle, unmute_at, watch_until):
    """What bring_back is waiting for, for the settings app's progress bar."""
    now = time.time()
    placed = len(placer.matched)
    if media.todo:
        progress("putting %d video%s back at its place (sound off meanwhile)" % (
            len(media.todo), "s" if len(media.todo) > 1 else ""), until=unmute_at or None)
    elif placer.todo:
        progress("waiting for windows: %d of %d in place" % (placed, total), placed, total,
                 until=min(end, placer.last_match + settle))
    elif watch_until > now:
        progress("watching for videos that start by themselves", until=watch_until)
    else:
        progress("windows in place", placed, total)


class Placer:
    """Match new windows to saved ones (same app, most similar title), move them
    (if move) and, at the end, stack them in the saved order."""

    def __init__(self, kw, state, ignore_ids, move=(), strict_ids=(), fixed=None):
        """move: the app ids whose windows are moved; the others are only
        matched (for the stacking order)."""
        self.kw, self.ignore, self.move = kw, ignore_ids, move
        # windows that were open before: matched only when the title fits, never
        # "the remaining window of that app" (that is for freshly started apps)
        self.strict = set(strict_ids)
        self.matched = []    # (saved window, new window id)
        self.last_match = time.time()
        self.todo = {}
        for w in state["windows"]:
            self.todo.setdefault(w["app"], []).append(w)
        self.seen = {}       # window id -> passes seen without a good match
        # pairs decided beforehand (window id -> saved window): placed at once
        self.fixed = dict(fixed or {})
        self.ignore |= set(self.fixed)

    def step(self):
        if not self.todo and not self.fixed:
            return
        snap = self.kw.windows()
        moves = {}
        if self.fixed:
            for w in snap["windows"]:
                c = self.fixed.pop(w["id"], None)
                if c:
                    self.matched.append((c, w["id"]))
                    if c["app"] in self.move:
                        moves[w["id"]] = self.target(c, snap)
                        log("placing %s \"%s\" as \"%s\" (chosen)" % (w["app"], w["caption"][:40], c["caption"][:40]))
            self.fixed = {}
        for w in snap["windows"]:
            if w["id"] in self.ignore or w["app"] not in self.todo:
                continue
            cands = self.todo[w["app"]]
            self.seen[w["id"]] = self.seen.get(w["id"], 0) + 1
            score, i = max((similar(strip_caption(w["caption"]), strip_caption(c["caption"])), i)
                           for i, c in enumerate(cands))
            # Titles fill in after loading; wait a few passes for a good match
            if score < 0.6 and (w["id"] in self.strict or self.seen[w["id"]] < 4):
                continue
            c = cands.pop(i)
            if not cands:
                del self.todo[w["app"]]
            self.ignore.add(w["id"])
            self.matched.append((c, w["id"]))
            self.last_match = time.time()
            if c["app"] not in self.move:
                continue
            moves[w["id"]] = self.target(c, snap)
            log("placing %s \"%s\" as \"%s\" (match %.2f)" % (w["app"], w["caption"][:40], c["caption"][:40], score))
        if moves:
            self.kw.run(JS_PLACE % json.dumps(moves))

    @staticmethod
    def target(c, snap):
        return {"geometry": target_geometry(c, snap.get("screens", [])),
                "desktops": c["desktops"], "maximize": c["maximize"], "fullscreen": c["fullscreen"],
                "keepAbove": c["keepAbove"], "minimized": c["minimized"]}

    def restack(self):
        progress("putting the windows in their saved order")
        order = [wid for c, wid in sorted(self.matched, key=lambda m: m[0]["stack"])]
        if order:
            n = self.kw.run(JS_RAISE % (json.dumps(order), json.dumps(not os.environ.get("PRW_FROM_APP"))))
            log("stacked %d windows in the saved order" % n)

    def report(self):
        if self.todo:
            log("not matched (window did not appear): " +
                ", ".join("%s x%d" % (a, len(c)) for a, c in self.todo.items()))
