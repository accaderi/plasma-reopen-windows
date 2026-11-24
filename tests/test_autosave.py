"""Autosave: rotation of the two snapshots, when one is taken, what is offered at login."""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import autosave, config  # noqa: E402


def win(caption, x=0):
    return {"app": "org.kde.dolphin", "caption": caption, "output": "eDP-1", "x": x, "y": 0, "width": 10, "height": 10}


class TestAutosave(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.patches = [mock.patch.object(autosave, "SLOTS", [os.path.join(d, "a1"), os.path.join(d, "a2")]),
                        mock.patch.object(autosave, "CHECKED", os.path.join(d, "checked")),
                        mock.patch.object(autosave, "OFFERED", os.path.join(d, "offered")),
                        mock.patch.object(autosave, "LOCK", os.path.join(d, "busy")),
                        mock.patch.object(autosave, "log")]
        for p in self.patches:
            p.start()
        self.cfg = dict(config.CFG)
        config.CFG.update(autosave="yes", autosave_minutes="30", autosave_ask="yes", max_age_days="7")

    def tearDown(self):
        for p in self.patches:
            p.stop()
        config.CFG.clear()
        config.CFG.update(self.cfg)

    def take(self, windows, manual=False):
        with mock.patch("plasma_reopen_windows.kwin.KWin"), \
                mock.patch("plasma_reopen_windows.session.snapshot", return_value={}), \
                mock.patch("plasma_reopen_windows.profiles.build",
                           return_value={"name": "Autosave", "apps": [{"app": "org.kde.dolphin"}], "windows": windows}):
            return autosave.take(manual=manual)

    def test_rotation_and_unchanged(self):
        self.assertIsNotNone(self.take([win("A")], manual=True))
        self.assertIsNone(self.take([win("A")], manual=True))          # nothing changed: nothing written
        self.assertIsNotNone(self.take([win("A", x=50)], manual=True))  # moved: a new one
        self.assertEqual(autosave.read(0)["windows"][0]["x"], 50)
        self.assertEqual(autosave.read(1)["windows"][0]["x"], 0)       # the one before is kept

    def test_browser_sessions_go_with_the_newest(self):
        """Each snapshot gets its copy of the browser sessions; an unchanged one
        gets the fresh copy (a tab's history changes without its title); copies
        no autosave refers to are deleted."""
        from plasma_reopen_windows import browser_copy
        recs = iter([{"dir": "1", "apps": {}}, {"dir": "2", "apps": {}}, {"dir": "3", "apps": {}}])
        with mock.patch.object(browser_copy, "take", side_effect=lambda p: next(recs)), \
                mock.patch.object(browser_copy, "clean") as clean:
            self.take([win("A")], manual=True)
            self.take([win("A", x=50)], manual=True)
            self.assertEqual(autosave.read(0)["browser_sessions"]["dir"], "2")
            self.assertEqual(autosave.read(1)["browser_sessions"]["dir"], "1")
            os.utime(autosave.CHECKED, (time.time() - 3600, time.time() - 3600))
            self.assertIsNone(self.take([win("A", x=50)]))                 # timer, due, unchanged
            self.assertEqual(autosave.read(0)["browser_sessions"]["dir"], "3")
            self.assertEqual(sorted(r["dir"] for r in clean.call_args[0][0]), ["1", "3"])

    def test_same_windows_new_video_place(self):
        """Save Windows Now with the same windows still records the video's
        place now (measured bug: the old place stayed); the earlier one stays."""
        def take(pos):
            with mock.patch("plasma_reopen_windows.kwin.KWin"), \
                    mock.patch("plasma_reopen_windows.session.snapshot", return_value={}), \
                    mock.patch("plasma_reopen_windows.profiles.build", return_value={
                        "name": "Autosave", "apps": [], "windows": [win("A")],
                        "media": [{"title": "V", "position": pos, "status": "Playing"}]}):
                return autosave.take(manual=True)
        take(1)
        take(1)                                     # unchanged: rotated nothing
        self.take([win("B")], manual=True)          # moves the first one to slot 2
        take(5)
        take(9)
        self.assertEqual(autosave.read(0)["media"][0]["position"], 9)
        self.assertEqual(autosave.read(1)["windows"][0]["caption"], "B")

    def test_empty_desktop_is_not_saved(self):
        self.take([win("A")], manual=True)
        self.assertIsNone(self.take([], manual=True))
        self.assertEqual(len(autosave.read(0)["windows"]), 1)

    def test_timer_only_when_on_due_and_not_busy(self):
        self.assertIsNotNone(self.take([win("A")]))                    # first time: due
        self.assertIsNone(self.take([win("B")]))                       # within the interval
        os.utime(autosave.CHECKED, (time.time() - 3600, time.time() - 3600))
        config.CFG["autosave"] = "no"
        self.assertIsNone(self.take([win("B")]))                       # switched off
        config.CFG["autosave"] = "yes"
        with open(autosave.LOCK, "w") as f:
            f.write(str(os.getppid()))                                 # a running process
        self.assertIsNone(self.take([win("B")]))                       # a load is running
        os.remove(autosave.LOCK)
        self.assertIsNotNone(self.take([win("B")]))
        self.assertIsNotNone(self.take([win("C")], manual=True))       # Save now: always

    def test_offered_once_and_not_when_old(self):
        p = self.take([win("A")], manual=True)
        self.assertEqual(len(autosave.to_offer()), 1)
        autosave.offered(p)
        self.assertEqual(autosave.to_offer(), [])                      # the same one not twice
        self.assertEqual(autosave.to_offer(now=time.time() + 30 * 86400), [])
        config.CFG["autosave_ask"] = "no"
        self.take([win("B")], manual=True)
        self.assertEqual(autosave.to_offer(), [])


if __name__ == "__main__":
    unittest.main()
