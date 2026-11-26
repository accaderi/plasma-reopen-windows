"""Progress on the desktop (no settings app open): one notification, updated
in place, replaced by the result."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import config  # noqa: E402


class TestScreenProgress(unittest.TestCase):
    def setUp(self):
        self.sp = config.ScreenProgress()
        self.sent = []

        def send(summary, body, pct=None, timeout=-1, icon=None):
            self.sent.append((summary, body, pct))
            self.sp.id = 7
            return True
        self.sp._send = send

    def test_count_and_result_in_place(self):
        with mock.patch.object(config.time, "time", return_value=1000):
            self.sp.show("waiting for windows: 2 of 8 in place", 2, 8)
            self.sp.show("waiting for windows: 2 of 8 in place", 2, 8)     # same: not sent again
        self.assertEqual(self.sent, [("Working…", "Waiting for windows: 2 of 8 in place", 25)])
        self.assertTrue(self.sp.finish("Session restored", "6 apps started again"))
        self.assertEqual(self.sent[-1], ("Session restored", "6 apps started again", None))
        self.assertEqual(self.sp.id, 0)

    def test_countdown_fills_the_bar(self):
        with mock.patch.object(config.time, "time", return_value=1000):
            self.sp.show("waiting for 1 video to appear", until=1030)
        with mock.patch.object(config.time, "time", return_value=1015):
            self.sp.show("waiting for 1 video to appear", until=1030)
        self.assertEqual([s[2] for s in self.sent], [0, 50])
        self.assertIn("up to 15 s", self.sent[-1][1])

    def test_nothing_shown_nothing_to_finish(self):
        self.assertFalse(self.sp.finish("x", "y"))


if __name__ == "__main__":
    unittest.main()
