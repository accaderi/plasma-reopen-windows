"""plasma-reopen-windows: windows minimized while the computer hibernates.

What a window shows is kept in graphics memory; on a graphics chip that shares
the RAM (Radeon, Intel) it stays in RAM and goes into the hibernation image,
and it cannot be moved to swap first. Measured 2026-11-25 (Radeon 680M, KWin
draws every screen on it, the external ones too): a maximized video window on
a 4K screen held about 280 MiB; minimized, about 50 MiB after 10 s.

  hibernate before   minimize every window that is shown, remember them, and
                     wait until the graphics memory stops shrinking
  hibernate after    show the same windows again, in the same order

hibernate-prepare runs both (as the user of each desktop session) when it
finds this tool; any sleep hook can do the same.
"""

import glob
import json
import os
import time
from .config import CFG, STATE_DIR, log
from .kwin import JS_MINIMIZE, JS_RAISE, KWin

SAVED = os.path.join(STATE_DIR, "hibernate-minimized.json")
# The memory comes back in steps with pauses of 3-7 s between them (measured:
# 9 s and 16 s after minimizing), so: at least SETTLE_MIN, then until it has
# not shrunk for SETTLE_STEADY, at most SETTLE_MAX seconds.
SETTLE_MIN = 15
SETTLE_STEADY = 5
SETTLE_MAX = 25


def gtt_mib():
    """Graphics memory in RAM (GTT) of all graphics cards that report it, or None."""
    files = glob.glob("/sys/class/drm/card[0-9]*/device/mem_info_gtt_used")
    if not files:
        return None
    total = 0
    for f in files:
        try:
            with open(f) as fh:
                total += int(fh.read())
        except (OSError, ValueError):
            pass
    return total // 1048576


TRACE = []                # the readings of the last settle(), one per second


def settle(read=gtt_mib, clock=time.time, sleep=time.sleep, start=None):
    """Wait until the graphics memory stops shrinking; (start, after) in MiB,
    start = the reading before minimizing when given. Without a reading: a
    fixed few seconds."""
    start = read() if start is None else start
    if start is None:
        sleep(5)
        return None, None
    begin = clock()
    TRACE[:] = [start]
    lowest, since, end = start, begin, begin + SETTLE_MAX
    while clock() < end and (clock() - begin < SETTLE_MIN or clock() - since < SETTLE_STEADY):
        sleep(1)
        now = read()
        TRACE.append(now)
        if now is not None and now < lowest:
            lowest, since = now, clock()
    return start, read()


def before():
    if CFG.get("hibernate_minimize") != "yes":
        log("hibernate: minimizing is switched off (hibernate_minimize)")
        return 0
    kw = KWin()
    shown = sorted((w for w in kw.windows()["windows"] if not w["minimized"]), key=lambda w: w["stack"])
    ids = [w["id"] for w in shown]               # bottom to top
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(SAVED + ".new", "w") as f:
        json.dump({"time": time.time(), "ids": ids}, f)
    os.replace(SAVED + ".new", SAVED)
    first = gtt_mib()                                # before: what the shown windows hold
    kw.run(JS_MINIMIZE % (json.dumps(ids), "true"))
    start, end = settle(start=first)
    msg = "hibernate: minimized %d windows" % len(ids)
    if start is not None:
        msg += "; graphics memory in RAM %d -> %d MiB (each second: %s)" % (
            start, end, " ".join(str(t) for t in TRACE))
    log(msg)                                     # log() also prints it
    return len(ids)


def after():
    try:
        with open(SAVED) as f:
            ids = json.load(f)["ids"]
    except (OSError, ValueError, KeyError):
        return 0
    kw = KWin()
    n = kw.run(JS_MINIMIZE % (json.dumps(ids), "false"))
    kw.run(JS_RAISE % (json.dumps(ids), "true"))      # the same order, the top one active
    os.remove(SAVED)
    log("hibernate: %s windows shown again" % n)
    return n
