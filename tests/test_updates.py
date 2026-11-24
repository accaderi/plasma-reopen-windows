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

    def test_prepared(self):
        out = "Prepared updates:\nbash;5.3.0-1.fc44;x86_64;updates\n  zlib;1.3-2.fc44;x86_64;updates\n"
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
        def run_pkcon(args, note, step):
            self.calls.append(args[0])
            return (download if args[0] == "update" else 0), ["last line"]

        def run(cmd, **_kw):
            self.calls.append(cmd[-1])
            if cmd[-1] == "offline-trigger":
                return done(trigger)
            return done(0, "".join("p%d;1;x86_64;updates\n" % i for i in range(prepared)))
        a = mock.patch.object(updates, "run_pkcon", side_effect=run_pkcon)
        b = mock.patch.object(updates.subprocess, "run", side_effect=run)
        a.start(), b.start()
        self.addCleanup(a.stop), self.addCleanup(b.stop)

    def test_all_steps_in_order(self):
        self.pk()
        self.assertTrue(updates.do_update())
        self.assertEqual(self.calls, ["refresh", "update", "offline-get-prepared", "save", "offline-trigger", "reboot"])

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


if __name__ == "__main__":
    unittest.main()
