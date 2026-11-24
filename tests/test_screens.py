"""Monitor recognition and window geometry."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import screens  # noqa: E402

LAPTOP = {"name": "eDP-1", "manufacturer": "Chimei", "model": "eDP-1", "serial": "",
          "x": 0, "y": 731, "width": 1920, "height": 1080}
LG = {"name": "DP-9", "manufacturer": "LG Electronics", "model": "LG Ultra HD", "serial": "12345",
      "x": 1920, "y": 0, "width": 2259, "height": 1271}
PHILIPS = {"name": "HDMI-A-1", "manufacturer": "Philips", "model": "PHL27E1N1100A", "serial": "ABC123",
           "x": 4179, "y": 533, "width": 1920, "height": 1080}
NOW = [LAPTOP, LG, PHILIPS]


class TestScreens(unittest.TestCase):
    def test_match_by_serial_even_if_renamed(self):
        renamed = dict(LG, name="DP-8")
        self.assertIs(screens.match_screen(screens.identity(LG), [LAPTOP, renamed]), renamed)

    def test_match_by_model_without_serial(self):
        self.assertIs(screens.match_screen(screens.identity(LAPTOP), NOW), LAPTOP)

    def test_missing_monitor(self):
        self.assertIsNone(screens.match_screen(screens.identity(PHILIPS), [LAPTOP, LG]))

    def test_roundtrip_same_layout(self):
        w = {"output": "HDMI-A-1", "x": 4300, "y": 600, "width": 800, "height": 500}
        screens.annotate([w], NOW)
        self.assertEqual(w["rel"], [121, 67, 800, 500])
        self.assertEqual(screens.target_geometry(w, NOW), {"x": 4300, "y": 600, "width": 800, "height": 500})

    def test_monitor_moved_in_layout(self):
        w = {"output": "DP-9", "x": 2000, "y": 100, "width": 900, "height": 600}
        screens.annotate([w], NOW)
        moved = [LAPTOP, dict(LG, x=0, y=0), dict(PHILIPS, x=2259)]
        self.assertEqual(screens.target_geometry(w, moved), {"x": 80, "y": 100, "width": 900, "height": 600})

    def test_scaled_and_kept_inside(self):
        w = {"output": "DP-9", "x": 1920 + 1700, "y": 900, "width": 1000, "height": 400}
        screens.annotate([w], NOW)
        small = [dict(LG, width=1129, height=635)]
        g = screens.target_geometry(w, small)
        self.assertLessEqual(g["x"] + g["width"], LG["x"] + 1129 + 0.5)
        self.assertLessEqual(g["y"] + g["height"], 635 + 0.5)

    def test_old_session_without_identity(self):
        w = {"output": "DP-9", "x": 2000, "y": 10, "width": 500, "height": 400}
        self.assertEqual(screens.target_geometry(w, NOW)["x"], 2000)
        self.assertIsNone(screens.target_geometry(dict(w, output="DP-1"), NOW))


if __name__ == "__main__":
    unittest.main()


class TestStrictMatching(unittest.TestCase):
    def test_open_window_with_other_title_is_not_taken(self):
        from unittest import mock
        from plasma_reopen_windows import session
        saved = {"app": "firefox", "caption": "Fedora Start — Mozilla Firefox", "stack": 1,
                 "output": "DP-9", "x": 0, "y": 0, "width": 10, "height": 10, "desktops": [],
                 "maximize": 0, "fullscreen": False, "keepAbove": False, "minimized": False}
        open_now = {"id": "{new}", "app": "firefox", "caption": "Mozilla Firefox", "output": "eDP-1"}
        kw = mock.Mock()
        kw.windows.return_value = {"windows": [open_now], "screens": []}
        placer = session.Placer(kw, {"windows": [saved]}, set(), move={"firefox"}, strict_ids={"{new}"})
        for _ in range(6):
            placer.step()
        self.assertEqual(placer.matched, [])
        loose = session.Placer(kw, {"windows": [saved]}, set(), move={"firefox"})
        for _ in range(6):
            loose.step()
        self.assertEqual(len(loose.matched), 1)    # a freshly started window may be taken
