"""Spectacle: closed before a shut down, never started again (Plasma restarted
a background one at login, measured 2026-11-25)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import apps, session  # noqa: E402

SHOT = {"id": "w1", "app": "org.kde.spectacle", "pid": 77, "caption": "Spectacle"}


class TestCloseOnly(unittest.TestCase):
    def setUp(self):
        for x in (mock.patch.object(apps, "log"), mock.patch.object(apps.os, "kill")):
            x.start()
            self.addCleanup(x.stop)
        self.kill = apps.os.kill                       # the mock

    def kw(self, wins):
        kw = mock.Mock()
        kw.windows.return_value = {"windows": wins}
        return kw

    def test_window_closed_like_the_x_button(self):
        kw = self.kw([SHOT])
        with mock.patch.object(apps, "wait_closed", return_value=True), \
                mock.patch.object(apps, "user_pids", return_value=[]):
            self.assertIsNone(apps.close_only_apps(kw, 30))
        self.assertIn("w1", kw.run.call_args[0][0])

    def test_unsaved_screenshot_stops_it(self):
        with mock.patch.object(apps, "wait_closed", return_value=False), \
                mock.patch.object(apps, "user_pids", return_value=[77]):
            self.assertEqual(apps.close_only_apps(self.kw([SHOT]), 30), "org.kde.spectacle")
        self.kill.assert_not_called()                  # never ended while it asks

    def test_background_one_is_ended(self):
        with mock.patch.object(apps, "user_pids", return_value=[88]):
            self.assertIsNone(apps.close_only_apps(self.kw([]), 30))
        self.kill.assert_called_once_with(88, apps.signal.SIGTERM)

    def test_save_cancelled_before_any_app_closes(self):
        kw = mock.Mock()
        st = {"apps": {"google-chrome": [{}]}, "busy": [], "media": [], "sound": None}
        with mock.patch.object(session, "KWin", return_value=kw), \
                mock.patch.object(session, "snapshot", return_value=st), \
                mock.patch.object(session, "close_only_apps", return_value="org.kde.spectacle"), \
                mock.patch.object(session, "close_apps") as close, \
                mock.patch.object(session, "notify"), mock.patch.object(session, "log"), \
                mock.patch.dict(session.CFG, {"confirm": "no"}):
            self.assertIsNone(session.do_save())
        close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
