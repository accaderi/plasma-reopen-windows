"""plasma-reopen-windows: what comes back at login (setting on_login)."""

import os
import subprocess
from . import profiles
from .config import CFG, STATE, log
from .session import do_restore


def set_aside(why):
    """Keep a saved session that is not restored, so it can still be looked at."""
    if os.path.exists(STATE):
        os.replace(STATE, STATE + ".skipped")
        log("saved session not restored (%s); kept as %s.skipped" % (why, STATE))


def ask(has_session, names, timeout):
    """Notification with the choices; returns "last", "nothing", a profile
    slug, or None when there was no answer in time."""
    cmd = ["notify-send", "-a", "Reopen windows", "-i", "view-restore", "-t", str(timeout * 1000)]
    if has_session:
        cmd += ["-A", "last=Restore last session"]
    if names:
        cmd += ["-A", "choose=Load a profile…"]
    cmd += ["-A", "nothing=Nothing", "Reopen windows",
            "What should come back? Without an answer: %s in %d s." %
            ("the last session" if has_session else "nothing", timeout)]
    try:
        answer = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None
    if answer != "choose":
        return answer or None
    menu = ["kdialog", "--title", "Reopen windows", "--menu", "Load which profile?"]
    for p in names:
        menu += [profiles.slug(p["name"]), p["name"]]
    r = subprocess.run(menu, capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "nothing"


def login():
    mode = CFG.get("on_login")
    has_session = os.path.exists(STATE)
    if mode == "last":
        do_restore()
        return
    if mode == "nothing":
        set_aside("on_login = nothing")
        return
    if mode.startswith("profile:"):
        p = profiles.find(mode[8:])
        if p is None:
            log("on_login: no profile %r, restoring the last session instead" % mode[8:])
            do_restore()
            return
        set_aside("on_login = %s" % mode)
        profiles.unite(p)
        return
    # ask
    names = [p for p in profiles.all_profiles() if not p.get("auto")]
    if not has_session and not names:
        return
    answer = ask(has_session, names, int(CFG.get("ask_timeout")))
    log("login question answered: %s" % (answer or "(no answer)"))
    if answer in (None, "last"):
        if has_session:
            do_restore()
    elif answer == "nothing":
        set_aside("chosen at login")
    else:
        p = profiles.find(answer)
        if p:
            set_aside("profile chosen at login")
            profiles.unite(p)
