"""plasma-reopen-windows: profiles, named arrangements of apps and windows.

A profile is a snapshot taken without closing anything. Loading it can
  unite    both ways: start the profile's apps that are not open, open its missing
           windows, arrange all its windows; and add the windows open now that
           are not in the profile to it; nothing is closed
  reload   close the apps that are not in the profile, then unite
  arrange  only move the windows that are open; nothing is started or closed
Each of them first remembers what is open, so "undo" can go back once.
Before acting, a plan pairs saved windows with open ones by title. A saved
window that is not found while its app has other windows open is "unsure":
the user chooses (open the saved one / use an open one / leave it out).
The file is plain JSON and may be edited by hand: per app "enabled" and
"start_delay"; per window what it shows ("urls", "tabs", "folders", "folder",
"file"), used to open that window again.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from .browsers import tabs_of, window_title
from .apps import app_kind, app_name, find_desktop, ignored, launch_spec
from .config import CFG, STATE_DIR, log, notify, progress
UNDO = os.path.join(STATE_DIR, "undo.json")   # what was open before the last load (not a profile)
from .kwin import KWin
from .media import mpris_players, same_video, similar
from .reopen import can_reopen, command
from .session import bring_back, busy_message, close_apps, snapshot, strip_caption, write_json

DATA_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                        "plasma-reopen-windows")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
MENU_DIR = os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")),
                        "applications")
MENU_PREFIX = "plasma-reopen-windows-profile-"
SCHEMA = 1
BROWSERS = {
    "google-chrome", "com.google.Chrome", "chromium-browser", "chromium", "org.chromium.Chromium",
    "brave-browser", "com.brave.Browser", "microsoft-edge", "com.microsoft.Edge", "vivaldi-stable",
    "org.mozilla.firefox", "firefox", "firefox-esr", "org.mozilla.firefox_esr",
}
# runtime details of a window that mean nothing in a saved profile
RUNTIME_KEYS = ("id", "pid")


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "profile"


def path_for(name):
    return os.path.join(PROFILES_DIR, slug(name) + ".json")


def migrate(p):
    """Bring a profile from an older format to the current one."""
    schema = p.get("schema", 1)
    if schema > SCHEMA:
        raise ValueError("profile %r was made by a newer version of plasma-reopen-windows" % p.get("name"))
    for key, default in (("apps", []), ("windows", []), ("screens", []), ("auto", False)):
        p.setdefault(key, default)
    for a in p["apps"]:
        a.setdefault("enabled", True)
        a.setdefault("start_delay", 0)
    p["schema"] = SCHEMA
    return p


def read(path):
    with open(path) as f:
        p = json.load(f)
    if not isinstance(p, dict) or "name" not in p:
        raise ValueError("%s is not a plasma-reopen-windows profile" % path)
    return migrate(p)


def find(name):
    """A profile by name or slug, or None."""
    path = path_for(name)
    return read(path) if os.path.exists(path) else None


def need(name):
    p = find(name)
    if p is None:
        raise ValueError("no profile %r (see: plasma-reopen-windows profile list)" % name)
    return p


def all_profiles():
    out = []
    if os.path.isdir(PROFILES_DIR):
        for f in sorted(os.listdir(PROFILES_DIR)):
            if f.endswith(".json"):
                try:
                    out.append(read(os.path.join(PROFILES_DIR, f)))
                except (OSError, ValueError) as e:
                    log("profile %s skipped: %s" % (f, e))
    return out


def build(name, st, old=None, auto=False):
    """A profile from a snapshot (pure: no desktop access). Settings the user
    made in an older version of the same profile are kept."""
    keep = {a["app"]: a for a in (old or {}).get("apps", [])}
    apps = []
    for app, ws in st["apps"].items():
        spec = launch_spec(app, app_kind(app), ws, st["konsole"], st["files"])
        if not spec:
            continue
        prev = keep.get(app, {})
        apps.append({"app": app, "name": app_name(find_desktop(app, ws[0]) or app), "launch": spec,
                     "enabled": prev.get("enabled", True), "start_delay": prev.get("start_delay", 0)})
    # the open tabs of every browser window, from the browser's session file
    tabs = {}
    for app, ws in st["apps"].items():
        if app in BROWSERS:
            spec = next((a["launch"] for a in apps if a["app"] == app), {})
            found = tabs_of(app, [w["caption"] for w in ws], spec.get("firefox_profiles", []))
            tabs.update({w["id"]: t for w, t in zip(ws, found) if t and t["urls"]})
    windows = []
    old_wins = (old or {}).get("windows", [])
    for w in st["wins"]:
        wid = w.get("id")
        w = {k: v for k, v in w.items() if k not in RUNTIME_KEYS}
        w.update(st.get("details", {}).get(wid, {}))     # folders, tabs, file
        if wid in tabs:                       # read from the browser just now
            w["urls"] = tabs[wid]["urls"]
            w["active"] = tabs[wid]["active"]  # the tab in front
        elif w["app"] in BROWSERS:            # not found: keep the earlier list
            same = [o for o in old_wins if o["app"] == w["app"] and o.get("urls")]
            best = max(same, key=lambda o: similar(strip_caption(o["caption"]), strip_caption(w["caption"])),
                       default=None)
            w["urls"] = list(best["urls"]) if best else []
            w["active"] = best.get("active", 0) if best else 0
            if best:
                old_wins = [o for o in old_wins if o is not best]
        windows.append(w)
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    return {"schema": SCHEMA, "name": name, "auto": auto,
            "created": (old or {}).get("created", now), "updated": now,
            "screens": st["snap"]["screens"], "apps": apps, "windows": windows, "sound": st["sound"],
            "media": st.get("media", [])}


def save(name, auto=False):
    """Take a profile of what is open now; nothing is closed."""
    st = snapshot(KWin())
    p = build(name, st, find(name), auto)
    write_json(path_for(name), p)
    if not auto:
        sync_menu()
    log("profile \"%s\" saved: %d apps, %d windows" % (name, len(p["apps"]), len(p["windows"])))
    return p


def enabled(p):
    apps = [a for a in p["apps"] if a.get("enabled", True)]
    ids = {a["app"] for a in apps}
    return apps, ids, [w for w in p["windows"] if w["app"] in ids]


MATCH = 0.6             # title similarity that counts as "the same window"


def pair(saved, open_wins):
    """{saved index: open window id}, by title, best pairs first (pure)."""
    scores = sorted(((similar(strip_caption(w["caption"]), strip_caption(o["caption"])), i, o["id"])
                     for i, w in saved for o in open_wins if o["app"] == w["app"]),
                    key=lambda t: (-t[0], t[1]))          # equal titles: the window saved first
    out, used = {}, set()
    for score, i, oid in scores:
        if score >= MATCH and i not in out and oid not in used:
            out[i] = oid
            used.add(oid)
    return out


def screen_label(name, screens):
    s = next((s for s in screens if s.get("name") == name), {})
    return s.get("model") or name or "?"


def make_plan(p, mode, snap):
    """What loading would do with the windows open now (pure). mode: unite,
    reload or arrange. Saved windows are given by their index in p["windows"]."""
    _, ids, _ = enabled(p)
    open_wins = [w for w in snap["windows"] if not ignored(w["app"])]
    running = {w["app"] for w in open_wins}
    saved = [(i, w) for i, w in enumerate(p["windows"]) if w["app"] in ids and w["app"] in running]
    fixed = pair(saved, open_wins)
    used = set(fixed.values())
    reopen, questions = [], []
    for i, w in saved:
        if i in fixed:
            continue
        spare = [o for o in open_wins if o["app"] == w["app"] and o["id"] not in used]
        can = can_reopen(w) and mode != "arrange"
        if spare:
            questions.append({
                "saved": i, "app": w["app"], "caption": w["caption"], "can_open": can,
                "screen": (w.get("screen") or {}).get("model") or w.get("output", ""),
                "pages": len(w.get("urls") or w.get("tabs") or w.get("folders") or []),
                "what": next(("%d %s" % (len(w[k]), word) for k, word in
                              (("urls", "pages"), ("tabs", "tabs"), ("folders", "folders")) if w.get(k)), ""),
                "spare": [{"id": o["id"], "caption": o["caption"],
                           "screen": screen_label(o.get("output"), snap.get("screens", []))} for o in spare]})
        elif can:
            reopen.append(i)
    return {"profile": p["name"], "mode": mode, "fixed": {str(k): v for k, v in fixed.items()},
            "reopen": reopen, "questions": questions}


def plan(p, mode):
    return make_plan(p, mode, KWin().windows())


def settings_app():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plasma-reopen-windows-settings")
    return shutil.which("plasma-reopen-windows-settings") or (here if os.path.exists(here) else None)


def ask_choices(pl):
    """Show the question window of the settings app; None = cancelled."""
    app = settings_app()
    if not app:
        log("unsure windows: settings app not installed, using \"open\"")
        return {}
    fd, path = tempfile.mkstemp(prefix="plan-", suffix=".json", dir=STATE_DIR)
    with os.fdopen(fd, "w") as f:
        json.dump(pl, f)
    try:
        res = subprocess.run([app, "--choose", path], capture_output=True, text=True)
    finally:
        os.remove(path)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None


def default_choice(q, how):
    if how == "use" and q["spare"]:
        return "use:" + q["spare"][0]["id"]
    if how == "skip":
        return "skip"
    return "open" if q["can_open"] else "skip"


def resolve(pl, choices, how):
    """fixed pairs + windows to open again, after the user's choices (pure)."""
    fixed = {int(k): v for k, v in pl["fixed"].items()}
    reopen = list(pl["reopen"])
    used = set(fixed.values())
    for q in pl["questions"]:
        c = (choices or {}).get(str(q["saved"])) or default_choice(q, how)
        if c.startswith("use:") and c[4:] not in used and c[4:] in {s["id"] for s in q["spare"]}:
            fixed[q["saved"]] = c[4:]
            used.add(c[4:])
        elif c == "open" and q["can_open"]:
            reopen.append(q["saved"])
    return fixed, reopen


def remember(action, st=None, profile=None):
    """Keep what is open now (and the profile, if the load changes it), for "undo".
    action: what is about to be done (unite, reload, arrange), named on the undo button."""
    screen = build("Before the last load", st or snapshot(KWin()), auto=True)
    data = {"undo_kind": "both", "screen": screen, "profile": profile} if profile else dict(screen)
    data["undo_action"] = action
    write_json(UNDO, data)


def undo_action():
    """What "undo" would take back: save, unite, reload, arrange; None = nothing."""
    try:
        with open(UNDO) as f:
            last = json.load(f)
    except (OSError, ValueError):
        return None
    return last.get("undo_action") or {"edit": "save", "both": "unite"}.get(last.get("undo_kind"), "load")


def merge(p, st, built, matched_ids):
    """Unite the other way round: windows open now that are not the profile's
    are added to it, at their place; an app switched off but open is switched
    on again. Returns the names of the apps that got new windows (pure)."""
    new_apps = {a["app"]: a for a in built["apps"]}
    by_app = {a["app"]: a for a in p["apps"]}
    added = []
    # an app switched off but open: its windows as they are now replace the saved ones
    back_on = {app for app, a in by_app.items() if not a.get("enabled", True) and app in st["apps"]}
    p["windows"] = [w for w in p["windows"] if w["app"] not in back_on]
    for w, entry in zip(st["wins"], built["windows"]):
        if w["id"] in matched_ids:
            continue
        app = by_app.get(w["app"])
        if app is None:
            if w["app"] not in new_apps:
                continue                  # an app that cannot be started again (no menu entry)
            app = by_app[w["app"]] = dict(new_apps[w["app"]], enabled=True, start_delay=0)
            p["apps"].append(app)
        app["enabled"] = True
        p["windows"].append(entry)
        if app.get("name") not in added:
            added.append(app.get("name") or app["app"])
    return added


def summary(closed=(), started=(), opened=0, placed=0, added=()):
    """One line for every load: what was closed, started, opened and placed (pure)."""
    parts = []
    if added:
        parts.append("added %s to the profile" % ", ".join(added))
    if closed:
        parts.append("closed %s" % ", ".join(closed))
    if started:
        parts.append("started %s" % ", ".join(started))
    if opened:
        parts.append("opened %d window%s" % (opened, "s" if opened != 1 else ""))
    if placed:
        parts.append("put %d window%s in place" % (placed, "s" if placed != 1 else ""))
    text = "; ".join(parts) or "nothing to change"
    return text[0].upper() + text[1:] + "."


TITLES = {"unite": "Unite", "reload": "Reload", "arrange": "Arrange", "undo": "Undo"}


def one_per_video(items):
    """Each saved video once: entries that know their app win over copies
    without one (the Plasma browser integration reports a video a second time)."""
    out = []
    for m in sorted(items, key=lambda m: m.get("app") is None):
        if not any(same_video(m["title"], o["title"]) for o in out):
            out.append(m)
    return out


def videos_for(media, started, opening, playing):
    """The saved videos to put back now (pure): those of apps started now, and
    the one whose window is opened now (a browser window's title is its front
    tab, the video's page). Never a video that is open already (playing: the
    titles of the videos open now): one you are watching stays as it is."""
    out = []
    for m in one_per_video(media):
        if any(same_video(m["title"], t) for t in playing):
            continue
        if m.get("app") in started or any(same_video(m["title"], window_title(w["caption"])) for w in opening):
            out.append(m)
    return out


def missing_after_start(todo, started, opened):
    """Saved windows of apps started now that the app did not bring back (pure);
    never one that is being opened by this load already."""
    return [w for ws in todo.values() for w in ws
            if w["app"] in started and can_reopen(w) and not any(w is o for o in opened)]


def apply(p, mode, choices=None, ask=True, keep_undo=True, closed=(), title=None, both_ways=False):
    """Unite or arrange (reload closes the other apps first, then comes here).
    Returns False if the user cancelled."""
    progress("looking at the open windows")
    kw = KWin()
    snap = kw.windows()
    pl = make_plan(p, mode, snap)
    how = CFG.get("when_unsure")
    if pl["questions"] and choices is None:
        if ask and how == "ask":
            choices = ask_choices(pl)
            if choices is None:
                log("profile \"%s\": cancelled" % p["name"])
                return False
        else:
            choices = {}
    if keep_undo:
        remember(mode, profile=json.loads(json.dumps(p)) if both_ways else None)
    fixed_idx, reopen = resolve(pl, choices, "open" if how == "ask" else how)
    apps, ids, _ = enabled(p)
    open_now = [w for w in snap["windows"]]
    running = {w["app"] for w in open_now}
    existing = {w["id"] for w in open_now}
    fixed = {oid: p["windows"][i] for i, oid in fixed_idx.items()}
    launches, started = [], set()
    if mode != "arrange":
        launches = [dict(a["launch"], delay=a.get("start_delay", 0)) for a in apps if a["app"] not in running]
        started = {l["app"] for l in launches}
        # an autosave: browsers about to start get their own session of then back
        if p.get("browser_sessions"):
            from .browser_copy import put_back
            back = put_back(p["browser_sessions"], started)
            for l in launches:
                if l["app"] in back:
                    l["restore_session"] = True
    desktops = {a["app"]: a["launch"].get("desktop") for a in apps}
    opened, opening = [], []
    for i in reopen:
        spec = command(p["windows"][i], desktops.get(p["windows"][i]["app"]))
        if spec:
            opened.append(spec)
            opening.append(p["windows"][i])
    playing = [pl["title"] for pl in mpris_players(kw.bus) if pl["title"]] if mode != "arrange" else []
    media = videos_for(p.get("media", []), started, opening, playing) if mode != "arrange" else []
    # windows placed: the pairs, the windows of apps started now, the windows opened again
    windows = [w for w in p["windows"] if w["app"] in ids and w["app"] not in running and mode != "arrange"]
    windows += opening
    log("profile \"%s\" (%s): starting %d apps, opening %d windows, %d windows already open" % (
        p["name"], mode, len(launches), len(opened), len(fixed)))
    busy = launches or opened
    placer = bring_back(kw, launches + opened, windows, existing, ids, media_items=media,
               sound=p.get("sound") if mode != "arrange" else None,
               timeout=None if busy else 8, settle=20 if busy else 3, fixed=fixed,
               pause_unknown=bool(busy))      # only when something is started or opened
    # An app started now may bring back fewer windows than it had (Chrome after
    # a hard power-off): open the missing ones from what is saved for them.
    left = missing_after_start(placer.todo if placer else {}, started, opening)
    pairs = [(w, command(w, desktops.get(w["app"]))) for w in left]
    left = [w for w, spec in pairs if spec]
    again = [spec for _, spec in pairs if spec]
    if again:
        log("%d windows did not come back with their app: opening them" % len(again))
        progress("opening %d windows that did not come back" % len(again))
        now = {w["id"] for w in kw.windows()["windows"]}
        playing = [pl["title"] for pl in mpris_players(kw.bus) if pl["title"]]
        later = bring_back(kw, again, left, now, {w["app"] for w in left}, timeout=60, settle=8, pause_unknown=True,
                           media_items=videos_for(p.get("media", []), set(), left, playing))
        opened += again
        if later and placer:
            placer.matched += later.matched
            if CFG.get("restore_stacking") == "yes":
                placer.restack()          # all windows in the saved order, the late ones included
    added = []
    if both_ways:                         # Unite: what is open now also goes into the profile
        progress("adding windows that are not in the profile yet")
        st = snapshot(kw)
        added = merge(p, st, build(p["name"], st, auto=p.get("auto", False)),
                      {wid for _, wid in (placer.matched if placer else [])})
        if added:
            write_json(path_for(p["name"]), p)
    text = summary(closed, [a.get("name") or a["app"] for a in apps if a["app"] not in running]
                   if mode != "arrange" else [], len(opened), len(placer.matched) if placer else 0, added)
    head = title or "%s %s" % (TITLES[mode], p["name"])
    log("%s: %s" % (head, text))
    notify(head, text)
    return True


def unite(p, choices=None, ask=True):
    """Start the profile's apps that are not open, open its missing windows,
    then arrange all its windows."""
    return apply(p, "unite", choices, ask, both_ways=True)


def arrange(p, choices=None, ask=True):
    """Move the windows of the profile's open apps into place; start and close nothing."""
    return apply(p, "arrange", choices, ask)


def reload(p, choices=None, ask=True, keep_undo=True, title=None):
    """Close the apps that are not in the profile, then unite."""
    kw = KWin()
    if choices is None and ask and CFG.get("when_unsure") == "ask":
        pl = plan(p, "reload")              # ask first, before anything is closed
        if pl["questions"]:
            choices = ask_choices(pl)
            if choices is None:
                log("profile \"%s\": cancelled" % p["name"])
                return False
    progress("saving what is open now (for undo)")
    st = snapshot(kw)
    if st["busy"]:
        notify("Reload cancelled", "Programs are still running in Konsole: %s." % busy_message(st["busy"]),
               critical=True)
        return False
    if keep_undo:
        remember("reload", st)
    _, ids, _ = enabled(p)
    others = {app: ws for app, ws in st["apps"].items() if app not in ids}
    if others and close_apps(kw, others, st["konsole"], st["files"], what="Reloading profile") is None:
        return False
    closed = sorted(app_name(find_desktop(a, ws[0]) or a) for a, ws in others.items())
    return apply(p, "unite", choices or {}, ask=False, keep_undo=False, closed=closed,
                 title=title or "Reload %s" % p["name"])


def save_edit(p, choices=None):
    """Save a profile edited in the settings app (ticks, start delays, pages,
    removed apps), then reload it so the screens show it. "undo" puts the
    previous version back and reloads that."""
    progress("saving the profile")
    old = find(p["name"])
    if old:
        write_json(UNDO, {"undo_kind": "edit", "profile": old, "undo_action": "save"})
    write_json(path_for(p["name"]), migrate(p))
    log("profile \"%s\": changes saved" % p["name"])
    return reload(p, choices, keep_undo=False, title="Save changes %s" % p["name"])


def can_undo():
    return os.path.exists(UNDO)


def undo_point():
    """The undo point as saved, or None."""
    try:
        with open(UNDO) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def undo():
    """Undo the last thing done, on the screens: apps it started close again,
    apps it closed start again, windows go back; no questions. The profile file
    is never written: the settings app shows the profile as it was before as
    unsaved edits (an editor's undo), to save or discard."""
    last = undo_point()
    if last is None:
        raise ValueError("nothing to undo")
    kind = last.get("undo_kind")
    if kind == "both":                    # a Unite: the screens as they were before it
        target = migrate(last["screen"])
    elif kind == "edit":                  # a Save changes: the profile as it was, on the screens
        target = migrate(last["profile"])
    else:
        target = migrate(last)
    if reload(target, ask=False, keep_undo=False, title="Undo"):
        os.remove(UNDO)


def load(p, how=None, choices=None):
    how = how or CFG.get("profile_load")
    return reload(p, choices) if how in ("reload", "switch") else unite(p, choices)


def rename(old, new):
    p = need(old)
    if find(new) and slug(new) != slug(old):
        raise ValueError("a profile %r already exists" % new)
    p["name"] = new
    write_json(path_for(new), p)
    if slug(new) != slug(old):
        os.remove(path_for(old))
    sync_menu()


def delete(name):
    p = need(name)
    os.remove(path_for(p["name"]))
    sync_menu()


def clean():
    """Delete the automatic profiles older versions made ("Before NAME",
    "Undo try"); returns their names."""
    gone = []
    for p in all_profiles():
        if p.get("auto"):
            os.remove(path_for(p["name"]))
            gone.append(p["name"])
    return gone


def export(name, dest):
    p = need(name)
    if os.path.isdir(dest):
        dest = os.path.join(dest, slug(name) + ".json")
    write_json(dest, p)
    return dest


def import_file(src, overwrite=False):
    p = read(src)
    if find(p["name"]) and not overwrite:
        raise ValueError("a profile %r already exists (import with --overwrite to replace it)" % p["name"])
    write_json(path_for(p["name"]), p)
    sync_menu()
    return p


# ---- menu entries --------------------------------------------------------------
def menu_entry(p, binary):
    return ("[Desktop Entry]\nType=Application\nName=Load profile: %s\n"
            "Comment=Open the apps and windows of the plasma-reopen-windows profile \"%s\"\n"
            "Exec=%s profile load %s\nIcon=view-restore\nCategories=System;\n"
            "Keywords=profile;layout;windows;workspace;\n" % (p["name"], p["name"], binary, slug(p["name"])))


def sync_menu():
    """One app-menu entry per profile (also found by KRunner, can get a shortcut)."""
    binary = shutil.which("plasma-reopen-windows") or os.path.expanduser("~/.local/bin/plasma-reopen-windows")
    os.makedirs(MENU_DIR, exist_ok=True)
    wanted = {}
    for p in all_profiles():
        if not p.get("auto"):
            wanted[MENU_PREFIX + slug(p["name"]) + ".desktop"] = menu_entry(p, binary)
    for f in os.listdir(MENU_DIR):
        if f.startswith(MENU_PREFIX) and f not in wanted:
            os.remove(os.path.join(MENU_DIR, f))
    for f, text in wanted.items():
        path = os.path.join(MENU_DIR, f)
        current = None
        if os.path.exists(path):
            with open(path) as fh:
                current = fh.read()
        if current != text:
            with open(path, "w") as fh:
                fh.write(text)
    subprocess.Popen(["kbuildsycoca6"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
