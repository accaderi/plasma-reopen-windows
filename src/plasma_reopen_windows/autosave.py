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


def take(manual=False):
    """Take a snapshot. From the timer only when switched on, due, and no load
    is running. Returns the snapshot written, or None."""
    if not manual:
        if CFG.get("autosave") != "yes" or not due() or busy():
            return None
    from .kwin import KWin
    from .profiles import build
    from .session import snapshot
    p = build("Autosave", snapshot(KWin()), auto=True)
    p["saved"] = time.time()
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(CHECKED, "w") as f:
        f.write(str(int(p["saved"])))
    if not p["windows"]:
        log("autosave: no windows open, nothing saved")
        return None
    newest = read(0)
    if newest and fingerprint(newest) == fingerprint(p):
        if manual:                          # asked for: the newest gets the current time
            keep_time(p["saved"])
        return None
    keep(p)
    log("autosave%s: %s" % (" (Save now)" if manual else "", describe(p)))
    return p


def keep_time(t):
    p = read(0)
    if p:
        p["saved"] = t
        with open(SLOTS[0], "w") as f:
            json.dump(p, f, ensure_ascii=False)


def delete():
    for path in SLOTS + [OFFERED]:
        if os.path.exists(path):
            os.remove(path)
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
