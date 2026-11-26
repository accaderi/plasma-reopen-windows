"""plasma-reopen-windows: autosave, the open apps and windows every while.

Save & Shut Down only helps when it is used. An autosave covers the rest: a
hang or long press, an empty battery, a crash, or the normal Shut Down (which
closes browsers window by window, so only their last window comes back).

  every few minutes   the systemd user timer runs "autosave": when the
                      interval (setting autosave_minutes) has passed, the
                      same snapshot as a profile is taken - nothing is closed
                      or moved - and written if anything changed
  "Save now"          the same at once, also with autosave switched off
  restore             like a profile's Unite: missing apps and windows come
                      back, nothing is closed, Undo works
  browsers            each snapshot keeps a copy of the browsers' own session
                      files (browser_copy.py); a restore puts it back before
                      a browser starts, so it brings back all its windows
                      with each tab's history itself
  at login            after an end without Save & Shut Down: one question

The last two snapshots are kept (autosave-1.json is the newer): a snapshot
taken while a normal shutdown was closing the apps one by one is half empty,
and the one before it is the good one.
"""

import json
import os
import time
from .config import CFG, STATE_DIR, log

SLOTS = [os.path.join(STATE_DIR, "autosave-1.json"), os.path.join(STATE_DIR, "autosave-2.json")]
CHECKED = os.path.join(STATE_DIR, "autosave-checked")      # time of the last look (interval)
OFFERED = os.path.join(STATE_DIR, "autosave-offered")      # snapshot already offered at login
LOCK = os.path.join(STATE_DIR, "busy")                     # pid of a load or restore running now


# ---- "a load or restore is running" ---------------------------------------------
def mark_busy():
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(LOCK, "w") as f:
        f.write(str(os.getpid()))


def clear_busy():
    try:
        with open(LOCK) as f:
            if f.read().strip() == str(os.getpid()):
                os.remove(LOCK)
    except OSError:
        pass


def busy():
    """A load or restore is running: windows are still appearing or closing."""
    try:
        with open(LOCK) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
        return pid != os.getpid()
    except (OSError, ValueError):
        return False


# ---- the snapshots ------------------------------------------------------------------
def read(slot):
    try:
        with open(SLOTS[slot]) as f:
            return json.load(f)
    except (OSError, ValueError, IndexError):
        return None


def all_saved():
    """[(slot, snapshot)] newest first."""
    return [(i, p) for i, p in ((i, read(i)) for i in range(len(SLOTS))) if p]


def fingerprint(p):
    """What counts as "changed": apps, and per window its place and content."""
    return sorted(json.dumps([w.get(k) for k in ("app", "caption", "output", "x", "y", "width", "height",
                                                 "minimized", "urls", "active", "tabs", "folders", "folder",
                                                 "file")])
                  for w in p.get("windows", []))


def describe(p):
    """"Tue 14:32, 11 windows, 8 apps" """
    return "%s, %d windows, %d apps" % (time.strftime("%a %H:%M", time.localtime(p.get("saved", 0))),
                                        len(p.get("windows", [])), len(p.get("apps", [])))


def due(now=None):
    """The interval has passed since the last look."""
    now = now or time.time()
    try:
        last = os.path.getmtime(CHECKED)
    except OSError:
        return True
    return now - last >= int(CFG.get("autosave_minutes")) * 60 - 30      # timer runs every few minutes


def keep(p):
    """Write a new snapshot: the newest becomes the earlier one (pure file work)."""
    os.makedirs(STATE_DIR, exist_ok=True)
    if os.path.exists(SLOTS[0]):
        os.replace(SLOTS[0], SLOTS[1])
    tmp = SLOTS[0] + ".new"
    with open(tmp, "w") as f:
        json.dump(p, f, ensure_ascii=False)
    os.replace(tmp, SLOTS[0])


def apps_changed():
    """An app was opened or closed since the newest autosave (then the timer
    saves at once, not after the interval: measured 2026-11-25, apps started
    by a Unite were lost in a crash 13 minutes later, the next autosave was
    still 10 minutes away)."""
    newest = read(0)
    if not newest:
        return True
    from .apps import ignored
    from .kwin import KWin
    now = {w["app"] for w in KWin().windows()["windows"] if not ignored(w["app"])}
    return bool(now) and now != {w["app"] for w in newest.get("windows", [])}


def take(manual=False, why=None):
    """Take a snapshot. From the timer only when switched on, due (or an app
    was opened or closed), and no load is running. Returns the snapshot
    written, or None."""
    # The interval moves the newest snapshot to the earlier slot; a save in
    # between (an app opened or closed, after a Unite, before a hibernation)
    # updates the newest in place, so the earlier one is not pushed out by a
    # few quick changes (Save Windows Now always keeps the one before)
    # The newest one offered at login (what was open before a crash) is
    # never written over: the first save after it moves it to the earlier
    # slot (measured 2026-11-26: the save after its restore replaced it)
    rotate = due() or (manual and not why) or was_offered(read(0))
    if not manual:
        if CFG.get("autosave") != "yes" or busy() or not (rotate or apps_changed()):
            return None
    from .kwin import KWin
    from .profiles import build
    from .session import snapshot
    from . import browser_copy
    p = build("Autosave", snapshot(KWin()), auto=True)
    p["saved"] = time.time()
    os.makedirs(STATE_DIR, exist_ok=True)
    if rotate:                           # the interval counts from here
        with open(CHECKED, "w") as f:
            f.write(str(int(p["saved"])))
    if not p["windows"]:
        log("autosave: no windows open, nothing saved")
        return None
    newest = read(0)
    copies = browser_copy.take(p)
    if newest and fingerprint(newest) == fingerprint(p) and was_offered(newest):
        browser_copy.clean_all()         # the one offered at login stays exactly as it was
        return None
    if newest and fingerprint(newest) == fingerprint(p):
        # same windows: the newest is brought up to date in place (videos,
        # browser sessions: they change without the windows), the earlier one
        # stays; the time changes only when asked for
        if not manual:
            p["saved"] = newest.get("saved", p["saved"])
        if copies:
            p["browser_sessions"] = copies
        write_slot(0, p)
        browser_copy.clean_all()
        return None
    if copies:
        p["browser_sessions"] = copies
    if rotate or not newest:
        keep(p)
    else:
        write_slot(0, p)
    browser_copy.clean_all()
    log("autosave%s: %s" % (" (%s)" % why if why else " (Save now)" if manual else "", describe(p)))
    return p


def after(why):
    """Right after this tool changed the windows (Unite, Reload, a restore),
    or before a hibernation: saved now, not at the next interval."""
    if CFG.get("autosave") != "yes":
        return None
    try:
        return take(manual=True, why=why)
    except Exception as e:               # never spoils what it follows
        log("autosave (%s) skipped: %s" % (why, e))
        return None


def write_slot(slot, p):
    tmp = SLOTS[slot] + ".new"
    with open(tmp, "w") as f:
        json.dump(p, f, ensure_ascii=False)
    os.replace(tmp, SLOTS[slot])


def delete():
    from . import browser_copy
    for path in SLOTS + [OFFERED]:
        if os.path.exists(path):
            os.remove(path)
    browser_copy.clean_all()
    log("autosaves deleted")


def restore(slot=0):
    """Bring back the windows of a snapshot, like a profile's Unite (nothing is
    closed, no profile is changed, Undo works)."""
    p = read(slot)
    if p is None:
        raise ValueError("no autosave to restore")
    from .profiles import apply, migrate
    p = migrate(p)
    return apply(p, "unite", title="Restore autosave (%s)" % describe(p))


# ---- at login -------------------------------------------------------------------------
def was_offered(p):
    """This snapshot was offered at login."""
    try:
        with open(OFFERED) as f:
            return bool(p) and f.read().strip() == str(int(p.get("saved", 0)))
    except OSError:
        return False


def to_offer(now=None):
    """The snapshots to offer at login (newest first), or [] (pure on the files):
    only when asking is on, the newest is not too old and was not offered yet."""
    now = now or time.time()
    saved = all_saved()
    if CFG.get("autosave_ask") != "yes" or not saved:
        return []
    newest = saved[0][1]
    if (now - newest.get("saved", 0)) / 86400 > float(CFG.get("max_age_days")):
        return []
    try:
        with open(OFFERED) as f:
            if f.read().strip() == str(int(newest.get("saved", 0))):
                return []
    except OSError:
        pass
    return saved


def offered(p):
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(OFFERED, "w") as f:
        f.write(str(int(p.get("saved", 0))))
