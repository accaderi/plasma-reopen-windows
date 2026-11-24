"""plasma-reopen-windows: Save & Update & Restart.

The system updates the same way Discover's "Update and restart" does: through
PackageKit's offline updates. First the updates are downloaded while every app
is still open (nothing is lost if that fails or takes long), then the apps are
closed and saved as for Save & Restart, the update is armed for the next boot
(pkcon offline-trigger) and Plasma restarts. The computer boots once into the
update, installs it, restarts again, and at the login the windows come back.

PackageKit lets the user of the active session do all of this without a
password (polkit: org.freedesktop.packagekit.system-update and
trigger-offline-update are "yes" for active sessions on Fedora).
"""

import os
import re
import signal
import subprocess
import threading
import time
from gi.repository import Gio, GLib
from .apps import power
from .config import log, notify, progress
from .session import do_restore, do_save

PK = "org.freedesktop.PackageKit"
# pkcon exit code when there is nothing to do ("no updates available")
NOTHING_TO_DO = 5


def pkcon(*args):
    """pkcon in machine-readable form, in English (its lines are parsed)."""
    return ["pkcon", "--plain", "--noninteractive"] + list(args)


def _env():
    return dict(os.environ, LC_ALL="C", LANG="C")


class Note:
    """Progress for a run from the app menu: one desktop notification that is
    replaced as the download goes on (from the settings app: its own bar)."""

    def __init__(self):
        self.id = None
        self.shown = -100
        self.app = bool(os.environ.get("PRW_FROM_APP"))

    def show(self, text, pct=None):
        if self.app:
            progress(text + (" %d %%" % pct if pct is not None else ""), pct, 100 if pct is not None else None)
            return
        if pct is not None and pct - self.shown < 5:
            return
        self.shown = pct if pct is not None else self.shown
        cmd = ["notify-send", "-a", "Reopen windows", "-i", "system-software-update", "-p"]
        if self.id:
            cmd += ["-r", self.id]
        if pct is not None:
            cmd += ["-h", "int:value:%d" % pct]
        try:
            out = subprocess.run(cmd + ["Save & Update & Restart", text], capture_output=True,
                                 text=True, check=False, timeout=10).stdout.strip()
            self.id = out or self.id
        except (OSError, subprocess.TimeoutExpired):
            pass


def parse_line(line):
    """("status", text) for a status line of pkcon --plain, else None."""
    m = re.match(r"\s*Status:\s*(.+)", line)
    return ("status", m.group(1).strip()) if m else None


def percentage():
    """Percentage of the PackageKit transaction running now, None if unknown
    (pkcon --plain prints none, the daemon has it)."""
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        for path in bus.call_sync(PK, "/org/freedesktop/PackageKit", PK, "GetTransactionList",
                                  None, GLib.VariantType("(as)"), 0, 2000).unpack()[0]:
            n = bus.call_sync(PK, path, "org.freedesktop.DBus.Properties", "Get",
                              GLib.Variant("(ss)", (PK + ".Transaction", "Percentage")),
                              GLib.VariantType("(v)"), 0, 2000).unpack()[0]
            if 0 <= n <= 100:               # 101 = not known
                return n
    except GLib.Error:
        pass
    return None


def run_pkcon(args, note, step):
    """Run pkcon, showing its progress; (exit code, last lines). Stopped from
    outside: the PackageKit transaction is cancelled too (SIGINT = pkcon's cancel)."""
    proc = subprocess.Popen(pkcon(*args), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=_env())
    tail = []

    def read():
        for line in proc.stdout:
            if line.strip():
                tail[:] = (tail + [line.rstrip()])[-5:]
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        while proc.poll() is None:
            pct = percentage()
            if pct is not None:
                note.show(step, pct)
            time.sleep(1)
        reader.join(5)
        return proc.returncode, tail
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(30)
            except subprocess.TimeoutExpired:
                proc.kill()


def prepared():
    """Package ids (name;version;arch;repo) downloaded and ready for an offline update."""
    r = subprocess.run(pkcon("offline-get-prepared"), capture_output=True, text=True, env=_env(), check=False)
    if r.returncode != 0:
        return []
    return [w for l in r.stdout.splitlines() for w in l.split() if w.count(";") == 3]


def download(note):
    """Download all updates and prepare them; the number of packages, 0 when
    there is nothing to update, None on an error (already told)."""
    note.show("Checking for updates")
    log("update: refreshing the package lists")
    code, tail = run_pkcon(["refresh"], note, "Checking for updates")
    if code != 0:
        log("update: refresh failed (%d): %s" % (code, " | ".join(tail)))
        # not fatal: the lists may just be fresh enough
    log("update: downloading")
    code, tail = run_pkcon(["update", "--only-download"], note, "Downloading updates")
    if code == NOTHING_TO_DO:
        return 0
    if code != 0:
        log("update: download failed (%d): %s" % (code, " | ".join(tail)))
        notify("Update cancelled", "The updates could not be downloaded: %s. Nothing was closed."
               % (tail[-1] if tail else "pkcon %d" % code), critical=True)
        return None
    n = len(prepared())
    log("update: %d packages ready" % n)
    if n == 0:
        log("update: nothing prepared after the download")
        notify("Update cancelled", "PackageKit downloaded nothing it could install. Nothing was closed.",
               critical=True)
        return None
    return n


def do_update():
    note = Note()
    n = download(note)
    if not n:
        if n == 0:
            log("update: no updates")
            notify("No updates", "Nothing to install. Nothing was closed.")
        return False
    note.show("%d updates downloaded; closing the apps" % n)
    if do_save() is None:
        log("update: saving was cancelled; the downloaded updates stay for later")
        return False
    r = subprocess.run(pkcon("offline-trigger"), capture_output=True, text=True, env=_env(), check=False)
    if r.returncode != 0:
        why = (r.stdout + r.stderr).strip().splitlines()
        log("update: offline-trigger failed (%d): %s" % (r.returncode, " | ".join(why)))
        notify("Update cancelled", "The update could not be armed for the restart (%s). "
               "Your apps are opened again." % (why[-1] if why else r.returncode), critical=True)
        do_restore()
        return False
    log("update: %d packages are installed at the restart" % n)
    power("reboot")
    return True
