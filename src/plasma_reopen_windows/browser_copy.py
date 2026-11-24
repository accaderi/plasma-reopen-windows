"""plasma-reopen-windows: a copy of the browsers' own session files with each
autosave and profile, put back before the browser starts again.

A browser keeps its session file up to date while it runs: Chrome (and
Chromium-based browsers) "Sessions/Session_*" and "Tabs_*", Firefox
"sessionstore-backups/recovery.jsonlz4". They hold every window, every tab and
each tab's back/forward history. A normal shut down closes a browser window by
window, so afterwards it remembers only its last window; with the copy put
back, it restores all of them itself, as after Save & Restart.

Copies live in ~/.local/state/plasma-reopen-windows/browser-sessions/<stamp>/,
one folder per autosave or profile. Files a put-back replaces are moved to
browser-sessions/replaced/<app>/ (only the last time is kept), never deleted.
Nothing is put back while the browser runs.
"""

import configparser
import glob
import json
import os
import shutil
import struct
import time
import zipfile
from .browsers import CHROME_DIRS, read_mozlz4
from .config import STATE_DIR, log

COPIES = os.path.join(STATE_DIR, "browser-sessions")
REPLACED = os.path.join(COPIES, "replaced")
FIREFOX_FILES = ("sessionstore-backups/recovery.jsonlz4", "sessionstore.jsonlz4")
# what Firefox reads at start, first found first: moved away before a put-back
FIREFOX_DIRS = ("~/.config/mozilla/firefox", "~/.mozilla/firefox",
                "~/.var/app/org.mozilla.firefox/.mozilla/firefox")
FIREFOX_READS = ("sessionstore.jsonlz4", "sessionstore-backups/recovery.jsonlz4",
                 "sessionstore-backups/recovery.baklz4")


def write_mozlz4(path, data):
    """Firefox's .jsonlz4: the JSON as one LZ4 block of literals only (valid
    LZ4, just not smaller)."""
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode()
    n, token = len(raw), b""
    if n >= 15:
        rest = n - 15
        token = bytes([0xF0]) + b"\xff" * (rest // 255) + bytes([rest % 255])
    else:
        token = bytes([n << 4])
    with open(path + ".new", "wb") as f:
        f.write(b"mozLz40\0" + struct.pack("<I", n) + token + raw)
    os.replace(path + ".new", path)


def _newest(pattern):
    files = glob.glob(pattern)
    return max(files, key=os.path.getmtime) if files else None


def lock_pid(path):
    """The process id in a browser's lock link (Chrome "host-PID", Firefox
    "ip:+PID"), or None."""
    try:
        target = os.readlink(path)
    except OSError:
        return None
    digits = target.rsplit("-", 1)[-1].rsplit("+", 1)[-1]
    return int(digits) if digits.isdigit() else None


def running(lock):
    """True if the lock link names a living process (after a restart an old
    number can belong to another process: then the browser counts as running
    and is left alone, the safe side)."""
    pid = lock_pid(lock)
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def sources(app, launch):
    """[(file to copy, where it goes back)] for one browser app."""
    out = []
    if app in CHROME_DIRS:
        user = os.path.expanduser(CHROME_DIRS[app])
        for sessions in glob.glob(os.path.join(user, "*", "Sessions")):
            for kind in ("Session_*", "Tabs_*"):
                f = _newest(os.path.join(sessions, kind))
                if f:
                    out.append((f, f))
    for prof in launch.get("firefox_profiles", []):
        f = next((os.path.join(prof, n) for n in FIREFOX_FILES if os.path.exists(os.path.join(prof, n))), None)
        if f:
            out.append((f, os.path.join(prof, "sessionstore.jsonlz4")))
    return out


def take(p, stamp=None):
    """Copy the session files of the browsers in profile/snapshot p; the
    record for p["browser_sessions"], or None if there is no browser."""
    stamp = stamp or "%d" % (time.time() * 1000)
    apps = {}
    for a in p.get("apps", []):
        files = []
        for n, (src, dest) in enumerate(sources(a["app"], a.get("launch", {}))):
            copy = os.path.join(a["app"], "%d-%s" % (n, os.path.basename(src)))
            target = os.path.join(COPIES, stamp, copy)
            try:
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(src, target)      # a file being written: the browser appends, a copy is whole up to there
            except OSError as e:
                log("browser session copy: %s: %s" % (src, e))
                continue
            files.append({"copy": copy, "to": dest})
        if files:
            apps[a["app"]] = files
    if not apps:
        shutil.rmtree(os.path.join(COPIES, stamp), ignore_errors=True)
        return None
    return {"dir": stamp, "apps": apps}


def browser_running(app, files):
    if app in CHROME_DIRS:
        return running(os.path.join(os.path.expanduser(CHROME_DIRS[app]), "SingletonLock"))
    return any(running(os.path.join(os.path.dirname(f["to"]), "lock")) for f in files)


def _move_away(app, paths):
    """Move files a put-back replaces to replaced/<app>/ (the last time only)."""
    dest = os.path.join(REPLACED, app)
    shutil.rmtree(dest, ignore_errors=True)
    for n, path in enumerate(paths):
        if os.path.exists(path):
            os.makedirs(dest, exist_ok=True)
            shutil.move(path, os.path.join(dest, "%d-%s" % (n, os.path.basename(path))))


def put_back(record, apps):
    """Put the copied session files of these browser apps back (only those not
    running). Returns the apps done."""
    done = set()
    for app in sorted(set(apps) & set((record or {}).get("apps", {}))):
        files = record["apps"][app]
        base = os.path.join(COPIES, record["dir"])
        if not all(os.path.exists(os.path.join(base, f["copy"])) for f in files):
            log("%s: the copy of its session is gone; it restores its own" % app)
            continue
        if browser_running(app, files):
            log("%s is running: its session is left as it is" % app)
            continue
        try:
            if app in CHROME_DIRS:
                folders = sorted({os.path.dirname(f["to"]) for f in files})
                _move_away(app, [x for d in folders for k in ("Session_*", "Tabs_*")
                                 for x in glob.glob(os.path.join(d, k))])
                for f in files:
                    shutil.copy2(os.path.join(base, f["copy"]), f["to"])
            else:
                profs = sorted({os.path.dirname(f["to"]) for f in files})
                _move_away(app, [os.path.join(d, n) for d in profs for n in FIREFOX_READS])
                for f in files:
                    data = read_mozlz4(os.path.join(base, f["copy"]))
                    s = data.setdefault("session", {})
                    s.pop("state", None)        # "running" would count as a crash
                    s["recentCrashes"] = 0
                    write_mozlz4(f["to"], data)
        except (OSError, ValueError) as e:
            log("%s: could not put its session back: %s" % (app, e))
            continue
        log("%s: session of the autosave put back (%d files; replaced ones in %s)"
            % (app, len(files), os.path.join(REPLACED, app)))
        done.add(app)
    return done


def records_in(data):
    """Every browser_sessions record inside a saved file (undo points hold
    whole profiles)."""
    if isinstance(data, dict):
        if isinstance(data.get("browser_sessions"), dict):
            yield data["browser_sessions"]
        for v in data.values():
            yield from records_in(v)
    elif isinstance(data, list):
        for v in data:
            yield from records_in(v)


def clean_all():
    """Delete the copies that no autosave, profile or undo point refers to."""
    from . import autosave, profiles
    saved = [p for _, p in autosave.all_saved()] + profiles.all_profiles() + [profiles.undo_point()]
    clean([r for d in saved for r in records_in(d)])


def clean(records):
    """Delete the copies none of these records refers to."""
    keep = {r["dir"] for r in records if r}
    for d in glob.glob(os.path.join(COPIES, "*")):
        name = os.path.basename(d)
        if name != "replaced" and name not in keep:
            shutil.rmtree(d, ignore_errors=True)


# ---- a profile with its session copy in one file (export / import) --------------------
def pack(p, dest):
    """Write the profile and the files of its session copy into one zip file."""
    rec = p.get("browser_sessions")
    with zipfile.ZipFile(dest + ".new", "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("profile.json", json.dumps(p, indent=1, ensure_ascii=False))
        for files in (rec or {}).get("apps", {}).values():
            for f in files:
                src = os.path.join(COPIES, rec["dir"], f["copy"])
                if os.path.exists(src):
                    z.write(src, "sessions/" + f["copy"])
    os.replace(dest + ".new", dest)


def firefox_default_profile():
    """The profile Firefox starts with on this computer, or None."""
    for base in FIREFOX_DIRS:
        base = os.path.expanduser(base)
        ini = configparser.ConfigParser(strict=False, interpolation=None)
        if not ini.read(os.path.join(base, "profiles.ini")):
            continue
        # the install's own default first (what Firefox really uses), then Default=1
        paths = [ini[s]["Default"] for s in ini.sections() if s.startswith("Install") and "Default" in ini[s]]
        paths += [ini[s]["Path"] for s in ini.sections()
                  if s.startswith("Profile") and ini[s].get("Default") == "1" and "Path" in ini[s]]
        for path in paths:
            full = path if os.path.isabs(path) else os.path.join(base, path)
            if os.path.isdir(full):
                return full
    return None


def rehome(p):
    """Targets of the session copy on this computer: unchanged where the
    folder exists (the same computer); else Chrome's into the same profile
    folder of this computer's browser folder, Firefox's into its default
    profile (also used to start it). Files with no place are left out."""
    rec = p.get("browser_sessions") or {}
    for app, files in list(rec.get("apps", {}).items()):
        kept = []
        for f in files:
            to = f["to"]
            if not os.path.isdir(os.path.dirname(to)):
                if app in CHROME_DIRS:
                    parts = to.split(os.sep)[-3:]           # <profile>/Sessions/<file>
                    to = os.path.join(os.path.expanduser(CHROME_DIRS[app]), *parts)
                else:
                    prof = firefox_default_profile()
                    to = os.path.join(prof, "sessionstore.jsonlz4") if prof else None
                    for a in p.get("apps", []):
                        if a["app"] == app and prof:
                            a.setdefault("launch", {})["firefox_profiles"] = [prof]
            if to and os.path.isdir(os.path.dirname(to)):
                kept.append(dict(f, to=to))
        if kept:
            rec["apps"][app] = kept
        else:
            del rec["apps"][app]
    if rec and not rec.get("apps"):
        p.pop("browser_sessions", None)
    return p


def unpack(src):
    """The profile from a zip made by pack(), its session copy stored as a new
    copy of this computer (only the files the profile names are read)."""
    with zipfile.ZipFile(src) as z:
        p = json.loads(z.read("profile.json"))
        rec = p.get("browser_sessions")
        if not rec:
            return p
        stamp = "%d" % (time.time() * 1000)
        names = set(z.namelist())
        for app, files in list(rec.get("apps", {}).items()):
            kept = []
            for f in files:
                rel = os.path.normpath(f.get("copy", ""))
                if rel.startswith(("..", "/")) or "sessions/" + f["copy"] not in names:
                    continue                    # never outside the copy folder
                target = os.path.join(COPIES, stamp, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with open(target, "wb") as out:
                    out.write(z.read("sessions/" + f["copy"]))
                kept.append(dict(f, copy=rel))
            rec["apps"][app] = kept
        rec["dir"] = stamp
    return rehome(p)
