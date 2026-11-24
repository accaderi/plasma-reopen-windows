"""Save & Update & Restart: the order of the steps and when it stops (pkcon mocked)."""
import os
import subprocess
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import updates  # noqa: E402


def done(code, out=""):
    return subprocess.CompletedProcess([], code, out, "")


class TestParse(unittest.TestCase):
    def test_lines(self):
        self.assertEqual(updates.parse_line("Status: \tDownloading packages"), ("status", "Downloading packages"))
        self.assertIsNone(updates.parse_line("Results:"))

    def test_count_updates(self):
        out = ("Transaction:\tGetting updates\nStatus: \tFinished\nResults:\n"
               "Security     zvbi-0.2.46-1.fc44.x86_64 (updates)\n"
               "Available    xorg-x11-drv-nvidia-3:620.70.06-1.fc44.x86_64 (rpmfusion-nonfree-updates)\n")
        with mock.patch.object(updates.subprocess, "run", return_value=done(0, out)):
            self.assertEqual(updates.count_updates(), 2)

    def test_prepared(self):
        out = "Prepared updates:\nkernel_7.3.4-200.fc44.x86_64\nicu-tzdata_2026f-1.fc44.noarch\n"   # measured
        with mock.patch.object(updates.subprocess, "run", return_value=done(0, out)):
            self.assertEqual(len(updates.prepared()), 2)
        with mock.patch.object(updates.subprocess, "run", return_value=done(1, "")):
            self.assertEqual(updates.prepared(), [])


class TestFlow(unittest.TestCase):
    def setUp(self):
        self.calls = []
        p = [mock.patch.object(updates, "do_save", side_effect=lambda: self.calls.append("save") or {}),
             mock.patch.object(updates, "do_restore", side_effect=lambda: self.calls.append("restore")),
             mock.patch.object(updates, "power", side_effect=lambda a: self.calls.append(a)),
             mock.patch.object(updates, "notify"),
             mock.patch.object(updates.Note, "show")]
        for x in p:
            x.start()
            self.addCleanup(x.stop)

    def pk(self, download=0, trigger=0, prepared=3):
        def run_pkcon(args, note, step, total=0):
            self.calls.append(args[0])
            return (download if args[0] == "update" else 0), ["last line"]

        def run(cmd, **_kw):
            self.calls.append(cmd[-1])
            if cmd[-1] == "offline-trigger":
                return done(trigger)
            if cmd[-1] == "get-updates":
                return done(0, "Security     p-1.x86_64 (updates)\n" * prepared)
            return done(0, "Prepared updates:\n" + "".join("p%d_1.x86_64\n" % i for i in range(prepared)))
        a = mock.patch.object(updates, "run_pkcon", side_effect=run_pkcon)
        b = mock.patch.object(updates.subprocess, "run", side_effect=run)
        a.start(), b.start()
        self.addCleanup(a.stop), self.addCleanup(b.stop)

    def test_all_steps_in_order(self):
        self.pk()
        self.assertTrue(updates.do_update())
        self.assertEqual(self.calls, ["refresh", "get-updates", "update", "offline-get-prepared", "save", "offline-trigger", "reboot"])

    def test_no_updates_closes_nothing(self):
        self.pk(download=updates.NOTHING_TO_DO)
        self.assertFalse(updates.do_update())
        self.assertNotIn("save", self.calls)

    def test_failed_download_closes_nothing(self):
        self.pk(download=1)
        self.assertFalse(updates.do_update())
        self.assertNotIn("save", self.calls)

    def test_nothing_prepared_closes_nothing(self):
        self.pk(prepared=0)
        self.assertFalse(updates.do_update())
        self.assertNotIn("save", self.calls)

    def test_save_cancelled_arms_nothing(self):
        self.pk()
        with mock.patch.object(updates, "do_save", return_value=None):
            self.assertFalse(updates.do_update())
        self.assertNotIn("offline-trigger", self.calls)
        self.assertNotIn("reboot", self.calls)

    def test_trigger_failed_reopens_apps(self):
        self.pk(trigger=1)
        self.assertFalse(updates.do_update())
        self.assertEqual(self.calls[-2:], ["offline-trigger", "restore"])
        self.assertNotIn("reboot", self.calls)


class TestCancel(unittest.TestCase):
    def test_stopped_pkcon_is_cancelled(self):
        """Stopped while pkcon runs: pkcon gets SIGINT (cancels its transaction)."""
        proc = mock.MagicMock()
        proc.stdout = iter([])
        proc.poll.return_value = None
        note = mock.MagicMock()
        note.show.side_effect = SystemExit(143)
        with mock.patch.object(updates.subprocess, "Popen", return_value=proc), \
                mock.patch.object(updates, "percentage", return_value=10):
            with self.assertRaises(SystemExit):
                updates.run_pkcon(["update", "--only-download"], note, "Downloading")
        proc.send_signal.assert_called_once_with(updates.signal.SIGINT)


class TestProgress(unittest.TestCase):
    def test_counts_new_package_files(self):
        """dnf5: no percentage; the files arriving in the cache are the progress."""
        import tempfile
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "44", "metadata", "repo", "packages"))
        pkg = os.path.join(d, "44", "metadata", "repo", "packages", "%s.rpm")
        open(pkg % "old", "w").close()                 # in the cache already: not counted
        shown = []
        proc = mock.MagicMock()
        proc.stdout = iter([])
        polls = iter([None, None, 0, 0, 0])

        def poll():
            p = next(polls)
            if p is None:
                open(pkg % ("n%d" % len(shown)), "w").close()   # one more each second
            return p
        proc.poll.side_effect = poll
        proc.returncode = 0
        note = mock.MagicMock()
        note.show.side_effect = lambda step, pct=None: shown.append(pct)
        with mock.patch.object(updates, "PK_PACKAGES", os.path.join(d, "*", "metadata", "*", "packages", "*.rpm")), \
                mock.patch.object(updates.subprocess, "Popen", return_value=proc), \
                mock.patch.object(updates, "percentage", return_value=None), \
                mock.patch.object(updates.time, "sleep"):
            updates.run_pkcon(["update", "--only-download"], note, "Downloading", total=5)
        self.assertEqual(shown, [25, 50])              # 4 to download: 1 of 4, 2 of 4


if __name__ == "__main__":
    unittest.main()
