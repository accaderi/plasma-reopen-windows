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
from .config import STATE_DIR, log, notify, progress
from .session import do_restore, do_save

PK = "org.freedesktop.PackageKit"
# where PackageKit (dnf5 backend) puts the packages it downloads (measured)
PK_PACKAGES = "/var/cache/PackageKit/*/metadata/*/packages/*.rpm"
PENDING = os.path.join(STATE_DIR, "update-pending")     # armed: report the result at the next login
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


def installonly_limit():
    """How many kernels dnf keeps (installonly_limit, default 3)."""
    limit = 3
    for path in ["/etc/dnf/dnf.conf"] + sorted(glob.glob("/etc/dnf/libdnf5.conf.d/*.conf")):
        try:
            with open(path) as f:
                for line in f:
                    m = re.match(r"\s*installonly_limit\s*=\s*(\d+)", line)
                    if m:
                        limit = int(m.group(1))
        except OSError:
            pass
    return limit


def vkey(version):
    """Sort key for kernel versions: 7.10.1 after 7.9.3."""
    return [(0, int(x), "") if x.isdigit() else (1, 0, x) for x in re.findall(r"\d+|[a-z]+", version)]


def kernels():
    """Installed kernels, oldest first, as uname -r writes them."""
    r = subprocess.run(["rpm", "-q", "kernel-core", "--qf", "%{VERSION}-%{RELEASE}.%{ARCH}\n"],
                       capture_output=True, text=True, check=False)
    return sorted((l.strip() for l in r.stdout.splitlines() if l.strip() and " " not in l.strip()), key=vkey)


def installed_kernels():
    return len(kernels())


def pinned():
    """The user chooses which kernel starts: new kernels do not become the
    default (UPDATEDEFAULT=no in /etc/sysconfig/kernel, Fedora)."""
    try:
        with open("/etc/sysconfig/kernel") as f:
            return any(re.match(r"\s*UPDATEDEFAULT\s*=\s*no\b", l) for l in f)
    except OSError:
        return False


def kernels_removed(names, installed, running, limit):
    """The kernels an update with these packages removes: dnf keeps
    installonly_limit kernels and removes the oldest, never the running one."""
    new = [n.split("_", 1)[1] for n in names if n.startswith("kernel-core_")]
    excess = len(installed) + len(new) - limit
    return [k for k in installed if k != running][:max(0, excess)]


def boot_short(names, kernel=None, boot="/boot", kernels=None, limit=None):
    """(needed, free) in MiB when a new kernel is among the updates and /boot
    has no room for it, else None. Measured 2026-11-25: /boot (966 MiB) held
    three kernels of about 206 MiB each plus the rescue one; the new kernel
    could not be written and PackageKit cancelled the whole update at the
    restart. A new kernel's files take about what the running one's take: the
    package (vmlinuz, System.map) first, its boot image (initramfs) at the end
    of the update; with the kept number of kernels reached, dnf removes the
    oldest before that image is written."""
    if not any(n.startswith("kernel-core_") for n in names):
        return None
    kernel = kernel or os.uname().release

    def size(f):
        try:
            return os.stat(os.path.join(boot, f % kernel)).st_size // 1048576
        except OSError:
            return 0
    need = 64 + sum(size(f) for f in ("vmlinuz-%s", "System.map-%s", ".vmlinuz-%s.hmac"))
    kernels = installed_kernels() if kernels is None else kernels
    if kernels < (installonly_limit() if limit is None else limit):
        need += size("initramfs-%s.img")              # nothing is removed to make room for it
    st = os.statvfs(boot)
    free = st.f_bavail * st.f_frsize // 1048576
    return (need, free) if free < need else None


def report_result():
    """At the login after Save & Update & Restart: did the updates install?
    (PackageKit cancels all of them when one fails.)"""
    if not os.path.exists(PENDING):
        return None
    os.remove(PENDING)
    r = subprocess.run(pkcon("offline-status"), capture_output=True, text=True, env=_env(), check=False)
    out = r.stdout
    status = next((l.split(None, 1)[1].strip() for l in out.splitlines() if l.startswith("Status:")), "")
    details = next((l.split(":", 1)[1].strip() for l in out.splitlines() if l.startswith("ErrorDetails:")), "")
    log("update result: %s %s" % (status or "unknown", details))
    if status.lower().startswith("fail"):
        notify("The updates were NOT installed", (details or "PackageKit reported a failure.") +
               " Nothing was changed; the downloaded updates are kept.", critical=True)
    elif status:
        notify("System updated", "The updates were installed during the restart.")
    return status


TOLD = os.path.join(STATE_DIR, "kernels-told")    # newer kernels already told about


def new_kernel_note(running=None):
    """At login: a kernel newer than the one running was installed (by an
    update; with UPDATEDEFAULT=no the computer keeps starting the old one).
    Told once per kernel, with how to try it. Returns the kernel told about."""
    running = running or os.uname().release
    newer = [k for k in kernels() if vkey(k) > vkey(running)]
    try:
        with open(TOLD) as f:
            told = set(f.read().split())
        first = False
    except OSError:
        told, first = set(), True                     # first run: what is there now is known
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(TOLD, "w") as f:
        f.write("\n".join(sorted(told | set(newer))) + "\n")
    fresh = [k for k in newer if k not in told]
    if first or not fresh:
        return None
    k = fresh[-1]
    log("new kernel installed: %s (running %s)" % (k, running))
    steps = "Restart and choose it in the boot menu"
    if os.path.exists("/usr/local/sbin/hibernate-prepare"):
        steps += ("; run \"sudo hibernate-prepare calibrate\", then hibernate once and choose it again "
                  "when starting")
    notify("New kernel %s installed" % k.split(".fc")[0],
           "This computer still starts %s%s. To try the new one: %s. If it works: "
           "sudo grubby --set-default /boot/vmlinuz-%s" % (
               running.split(".fc")[0], " (you chose which kernel starts)" if pinned() else "", steps, k), stay=True)
    return k


OFFERED = os.path.join(STATE_DIR, "kernels-offered")   # available kernels already told about


def available_kernel():
    """The newest kernel the update would install, as uname -r writes it, or
    None. pkcon lists it as "<kind>   kernel-core-VERSION-RELEASE.ARCH (repo)"
    (from the package cache: about a second, no download)."""
    try:
        r = subprocess.run(pkcon("get-updates"), capture_output=True, text=True, env=_env(), check=False,
                           timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    found = re.findall(r"^\S+\s+kernel-core-(\d\S*)\s+\(\S+\)$", r.stdout, re.M)
    return max(found, key=vkey) if found else None


def kernel_available_note(running=None):
    """At login: a kernel newer than every installed one can be installed.
    Told once per kernel, with what Save & Update will do with it. Returns
    the kernel told about."""
    running = running or os.uname().release
    k = available_kernel()
    if not k or any(vkey(i) >= vkey(k) for i in kernels() + [running]):
        return None
    try:
        with open(OFFERED) as f:
            told = set(f.read().split())
    except OSError:
        told = set()
    if k in told:
        return None
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(OFFERED, "w") as f:
        f.write("\n".join(sorted(told | {k})) + "\n")
    log("new kernel available: %s (running %s)" % (k, running))
    short, now = k.split(".fc")[0], running.split(".fc")[0]
    if pinned():
        what = ("Save & Update installs it next to %s, which keeps starting (you chose which kernel starts). "
                "Then restart, choose it once in the boot menu and try it" % now)
        if os.path.exists("/usr/local/sbin/hibernate-prepare"):
            what += ": \"sudo hibernate-prepare calibrate\", then one real hibernation"
    else:
        what = "Save & Update installs it, and the computer then starts it"
    notify("New kernel %s available" % short, what + ".", stay=True)
    return k


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
    names = prepared()
    n = len(names)
    log("update: %d packages ready" % n)
    if n == 0:
        log("update: nothing prepared after the download")
        notify("Update cancelled", "PackageKit downloaded nothing it could install. Nothing was closed.",
               critical=True)
        return None
    gone = kernels_removed(names, kernels(), os.uname().release, installonly_limit())
    if gone:
        log("update: it would remove kernel %s (installonly_limit %d)" % (", ".join(gone), installonly_limit()))
        if pinned():
            # the user keeps a kernel that works (a newer one may not hibernate):
            # no kernel is removed behind their back
            notify("Update cancelled", "It would remove kernel %s, to keep only %d. You choose which kernel "
                   "starts (UPDATEDEFAULT=no), so none is removed without you: remove one you do not need "
                   "(sudo dnf remove kernel-core-VERSION), or allow one more (installonly_limit in "
                   "/etc/dnf/dnf.conf). Nothing was closed." % (", ".join(gone), installonly_limit()),
                   critical=True)
            return None
    short = boot_short(names)
    if short:
        log("update: /boot too small for the new kernel: needs about %d MiB, %d MiB free" % short)
        notify("Update cancelled", "The new kernel needs about %d MB on /boot, only %d MB are free, so the "
               "update would fail at the restart. Remove an old kernel first (README, Troubleshooting). "
               "Nothing was closed." % short, critical=True)
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
    with open(PENDING, "w") as f:
        f.write(str(n))
    power("reboot")
    return True
