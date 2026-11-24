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

import glob
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
# where PackageKit (dnf5 backend) puts the packages it downloads (measured)
PK_PACKAGES = "/var/cache/PackageKit/*/metadata/*/packages/*.rpm"
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


def downloaded():
    """Package files in PackageKit's cache."""
    return set(glob.glob(PK_PACKAGES))


def run_pkcon(args, note, step, total=0):
    """Run pkcon, showing its progress; (exit code, last lines). The dnf5
    backend reports no percentage (always 101) and pkcon lists the packages
    only at the end (both measured): then the progress is the package files
    that arrive in the cache, out of total minus those there already.
    Stopped from outside: the PackageKit transaction is cancelled too
    (SIGINT = pkcon's cancel)."""
    before = downloaded() if total else set()
    todo = max(1, total - len(before))
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
            if pct is None and total:
                pct = min(99, len(downloaded() - before) * 100 // todo)
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
    """Packages downloaded and ready for an offline update: pkcon lists them
    under "Prepared updates:", one name_version.arch per line (measured)."""
    r = subprocess.run(pkcon("offline-get-prepared"), capture_output=True, text=True, env=_env(), check=False)
    if r.returncode != 0:
        return []
    lines = r.stdout.splitlines()
    if "Prepared updates:" in lines:
        lines = lines[lines.index("Prepared updates:") + 1:]
    return [l.strip() for l in lines if l.strip() and " " not in l.strip()]


def count_updates():
    """How many updates there are (for the progress), 0 if unknown. pkcon
    lists each as "<kind>   name-version.arch (repo)"."""
    r = subprocess.run(pkcon("get-updates"), capture_output=True, text=True, env=_env(), check=False)
    return sum(1 for l in r.stdout.splitlines()
               if re.match(r"\S.*\s\S+\s\(\S+\)$", l) and not l.startswith(("Status", "Transaction", "Results")))


def download(note):
    """Download all updates and prepare them; the number of packages, 0 when
    there is nothing to update, None on an error (already told)."""
    note.show("Checking for updates")
    log("update: refreshing the package lists")
    code, tail = run_pkcon(["refresh"], note, "Checking for updates")
    if code != 0:
        log("update: refresh failed (%d): %s" % (code, " | ".join(tail)))
        # not fatal: the lists may just be fresh enough
    total = count_updates()
    log("update: downloading %d updates" % total)
    code, tail = run_pkcon(["update", "--only-download"], note, "Downloading %d updates" % total if total
                           else "Downloading updates", total)
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
