"""Copies of the browsers' session files: taken with an autosave, put back
before the browser starts, never while it runs (fake browser folders)."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import browser_copy as bc  # noqa: E402
from plasma_reopen_windows.browsers import read_mozlz4  # noqa: E402


def write(path, data, age=0):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    t = time.time() - age
    os.utime(path, (t, t))


def read(path):
    with open(path, "rb") as f:
        return f.read()


class TestMozLz4(unittest.TestCase):
    def test_round_trip(self):
        d = tempfile.mkdtemp()
        for text in ("", "short", "x" * 14, "x" * 15, "y" * 270, "z" * 100000):
            bc.write_mozlz4(os.path.join(d, "f"), {"t": text, "ü": 1})
            self.assertEqual(read_mozlz4(os.path.join(d, "f")), {"t": text, "ü": 1})


class TestCopies(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.chrome = os.path.join(self.root, "chrome")
        self.ff = os.path.join(self.root, "firefox", "abc.default")
        p = [mock.patch.object(bc, "COPIES", os.path.join(self.root, "copies")),
             mock.patch.object(bc, "REPLACED", os.path.join(self.root, "copies", "replaced")),
             mock.patch.dict(bc.CHROME_DIRS, {"google-chrome": self.chrome}),
             mock.patch.object(bc, "log")]
        for x in p:
            x.start()
            self.addCleanup(x.stop)
        self.sess = os.path.join(self.chrome, "Default", "Sessions")
        write(os.path.join(self.sess, "Session_1"), b"old", age=100)
        write(os.path.join(self.sess, "Session_2"), b"all windows", age=10)
        write(os.path.join(self.sess, "Tabs_1"), b"tabs", age=10)
        write(os.path.join(self.ff, "prefs.js"), b"")
        os.makedirs(os.path.join(self.ff, "sessionstore-backups"))
        bc.write_mozlz4(os.path.join(self.ff, "sessionstore-backups", "recovery.jsonlz4"),
                        {"windows": [1, 2, 3], "session": {"state": "running", "recentCrashes": 1}})
        self.p = {"apps": [{"app": "google-chrome", "launch": {}},
                           {"app": "org.mozilla.firefox", "launch": {"firefox_profiles": [self.ff]}},
                           {"app": "org.kde.konsole", "launch": {}}]}

    def test_take_copies_the_newest(self):
        r = bc.take(self.p, "s1")
        self.assertEqual(sorted(r["apps"]), ["google-chrome", "org.mozilla.firefox"])
        chrome = {os.path.basename(f["to"]) for f in r["apps"]["google-chrome"]}
        self.assertEqual(chrome, {"Session_2", "Tabs_1"})
        self.assertEqual(r["apps"]["org.mozilla.firefox"][0]["to"], os.path.join(self.ff, "sessionstore.jsonlz4"))

    def test_no_browser_no_copy(self):
        self.assertIsNone(bc.take({"apps": [{"app": "org.kde.konsole", "launch": {}}]}, "s1"))
        self.assertFalse(os.path.exists(os.path.join(bc.COPIES, "s1")))

    def test_put_back_after_a_window_by_window_close(self):
        r = bc.take(self.p, "s1")
        # the normal shut down: Chrome wrote a new file with one window left
        write(os.path.join(self.sess, "Session_3"), b"one window")
        bc.write_mozlz4(os.path.join(self.ff, "sessionstore.jsonlz4"), {"windows": [1]})
        done = bc.put_back(r, {"google-chrome", "org.mozilla.firefox"})
        self.assertEqual(done, {"google-chrome", "org.mozilla.firefox"})
        self.assertEqual(sorted(os.listdir(self.sess)), ["Session_2", "Tabs_1"])
        self.assertEqual(read(os.path.join(self.sess, "Session_2")), b"all windows")
        moved = os.listdir(os.path.join(bc.REPLACED, "google-chrome"))
        self.assertEqual(len(moved), 4)            # Session_1..3, Tabs_1: kept, not deleted
        data = read_mozlz4(os.path.join(self.ff, "sessionstore.jsonlz4"))
        self.assertEqual(data["windows"], [1, 2, 3])
        self.assertNotIn("state", data["session"])  # not a crash
        self.assertEqual(data["session"]["recentCrashes"], 0)
        self.assertFalse(os.path.exists(os.path.join(self.ff, "sessionstore-backups", "recovery.jsonlz4")))

    def test_only_apps_that_start(self):
        r = bc.take(self.p, "s1")
        self.assertEqual(bc.put_back(r, {"org.kde.konsole"}), set())
        self.assertIn("Session_1", os.listdir(self.sess))

    def test_never_while_running(self):
        r = bc.take(self.p, "s1")
        os.symlink("host-%d" % os.getpid(), os.path.join(self.chrome, "SingletonLock"))
        os.symlink("127.0.0.1:+%d" % os.getpid(), os.path.join(self.ff, "lock"))
        self.assertEqual(bc.put_back(r, {"google-chrome", "org.mozilla.firefox"}), set())
        self.assertIn("Session_1", os.listdir(self.sess))

    def test_stale_lock_is_not_running(self):
        os.symlink("host-99999999", os.path.join(self.chrome, "SingletonLock"))
        self.assertFalse(bc.running(os.path.join(self.chrome, "SingletonLock")))

    def test_copy_gone(self):
        r = bc.take(self.p, "s1")
        bc.clean([])
        self.assertEqual(bc.put_back(r, {"google-chrome"}), set())
        self.assertIn("Session_1", os.listdir(self.sess))

    def test_clean_keeps_referenced_and_replaced(self):
        a, b = bc.take(self.p, "s1"), bc.take(self.p, "s2")
        bc.put_back(b, {"google-chrome"})
        bc.clean([None, a])
        self.assertEqual(sorted(os.listdir(bc.COPIES)), ["replaced", "s1"])


class TestProfiles(unittest.TestCase):
    """Profiles get the same copy as autosaves."""
    def setUp(self):
        from plasma_reopen_windows import profiles
        self.pr = profiles
        self.dir = tempfile.mkdtemp()
        for x in (mock.patch.object(profiles, "PROFILES_DIR", self.dir),
                  mock.patch.object(profiles, "sync_menu"), mock.patch.object(profiles, "log")):
            x.start()
            self.addCleanup(x.stop)

    def profile(self, caption="A", enabled=True, urls=("u",)):
        return {"name": "Work", "apps": [{"app": "google-chrome", "enabled": enabled, "launch": {}}],
                "windows": [{"app": "google-chrome", "caption": caption, "urls": list(urls)}],
                "browser_sessions": {"dir": "s1", "apps": {"google-chrome": [{"copy": "x", "to": "y"}]}}}

    def test_save_takes_a_copy(self):
        rec = {"dir": "9", "apps": {"google-chrome": []}}
        with mock.patch.object(self.pr, "snapshot", return_value={}), mock.patch.object(self.pr, "KWin"), \
                mock.patch.object(self.pr, "build", return_value={"name": "Work", "apps": [], "windows": []}), \
                mock.patch.object(bc, "take", return_value=rec), mock.patch.object(bc, "clean_all") as clean:
            self.pr.save("Work")
        self.assertEqual(self.pr.find("Work")["browser_sessions"], rec)
        clean.assert_called_once()

    def test_edits_drop_the_copy_of_that_browser(self):
        old = self.profile()
        same = self.profile()
        self.pr.drop_changed_copies(same, old)
        self.assertIn("browser_sessions", same)
        for changed in (self.profile(caption="B"), self.profile(enabled=False), self.profile(urls=("u", "v"))):
            self.pr.drop_changed_copies(changed, old)
            self.assertNotIn("browser_sessions", changed)

    def test_export_and_import_leave_it_out(self):
        self.pr.write_json(self.pr.path_for("Work"), self.profile())
        dest = self.pr.export("Work", tempfile.mkdtemp())
        self.assertNotIn("browser_sessions", self.pr.read(dest))
        self.assertIn("browser_sessions", self.pr.find("Work"))

    def test_records_everywhere(self):
        undo = {"undo_kind": "edit", "profile": self.profile()}
        self.assertEqual([r["dir"] for r in bc.records_in(undo)], ["s1"])


if __name__ == "__main__":
    unittest.main()
