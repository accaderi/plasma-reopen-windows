"""plasma-reopen-windows: what comes back at login (setting on_login)."""

import os
import subprocess
from . import autosave, profiles
from .config import CFG, STATE, log
from .session import do_restore


def set_aside(why):
    """Keep a saved session that is not restored, so it can still be looked at."""
    if os.path.exists(STATE):
        os.replace(STATE, STATE + ".skipped")
        log("saved session not restored (%s); kept as %s.skipped" % (why, STATE))


def offer_autosave(timeout):
    """The session ended without Save & Shut Down: offer the last autosave
    (once per snapshot). Without an answer nothing happens."""
    saved = autosave.to_offer()
    if not saved:
        return
    autosave.offered(saved[0][1])
    cmd = ["notify-send", "-a", "Reopen windows", "-i", "view-restore", "-t", str(timeout * 1000)]
    for i, p in saved:
        cmd += ["-A", "%d=%s%s" % (i + 1, "Restore " if i == 0 else "Earlier: ", autosave.describe(p))]
    cmd += ["-A", "no=No", "Reopen windows",
            "The last session did not end with Save & Shut Down. Bring back the windows of the "
            "last autosave? Without an answer: nothing in %d s." % timeout]
    try:
        answer = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        answer = ""
    log("autosave offered at login, answer: %s" % (answer or "(no answer)"))
    if answer in ("1", "2"):
        autosave.restore(int(answer) - 1)


def ask(has_session, names, timeout, auto=None):
    """Notification with the choices; returns "last", "nothing", "autosave", a
    profile slug, or None when there was no answer in time."""
    cmd = ["notify-send", "-a", "Reopen windows", "-i", "view-restore", "-t", str(timeout * 1000)]
    if has_session:
        cmd += ["-A", "last=Restore last session"]
    if auto:
        cmd += ["-A", "autosave=Restore autosave (%s)" % autosave.describe(auto)]
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
    try:                            # after Save & Update & Restart: did it install?
        from .updates import kernel_available_note, new_kernel_note, report_result
        report_result()
        new_kernel_note()
        kernel_available_note()
    except Exception as e:          # never in the way of the restore
        log("update result not read: %s" % e)
    mode = CFG.get("on_login")
    has_session = os.path.exists(STATE)
    if mode == "last":
        if has_session:
            do_restore()
        else:                       # ended without Save & Shut Down
            offer_autosave(int(CFG.get("ask_timeout")))
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
        profiles.load(p)          # unite or reload: setting profile_load
        return
    # ask
    names = [p for p in profiles.all_profiles() if not p.get("auto")]
    offer = autosave.to_offer() if not has_session else []
    auto = offer[0][1] if offer else None
    if not has_session and not names and not auto:
        return
    if auto:
        autosave.offered(auto)
    answer = ask(has_session, names, int(CFG.get("ask_timeout")), auto)
    log("login question answered: %s" % (answer or "(no answer)"))
    if answer in (None, "last"):
        if has_session:
            do_restore()
    elif answer == "autosave":
        autosave.restore(0)
    elif answer == "nothing":
        set_aside("chosen at login")
    else:
        p = profiles.find(answer)
        if p:
            set_aside("profile chosen at login")
            profiles.load(p)          # unite or reload: setting profile_load
