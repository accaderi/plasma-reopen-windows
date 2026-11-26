"""bring_back ends soon after the windows are in place (measured 2026-11-25:
a saved video whose tab never appeared, and before that a sound output saved
under a generic name, made Unite and Reload wait their whole 2 minutes)."""
import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import session  # noqa: E402

SAVED = {"id": "s", "app": "org.kde.dolphin", "caption": "home — Dolphin", "stack": 1, "x": 0, "y": 0,
         "width": 800, "height": 600, "desktops": [], "maximize": 0, "fullscreen": False, "keepAbove": False,
         "minimized": False, "output": "eDP-1", "screen": {"model": "eDP-1"}, "rel": [0, 0, 800, 600]}


class TestEnd(unittest.TestCase):
    def run_back(self, media=(), sound=None, sound_found=False):
        kw = mock.Mock()
        kw.windows.return_value = {"windows": [dict(SAVED, id="{w}", pid=1)], "screens": []}
        kw.run.return_value = 1
        with mock.patch.object(session, "MEDIA_WAIT", 0.5), \
                mock.patch.object(session, "mpris_players", return_value=[]), \
                mock.patch("plasma_reopen_windows.media.mpris_players", return_value=[]), \
                mock.patch.object(session, "relaunch"), mock.patch.object(session, "mute_outputs", return_value=[]), \
                mock.patch.object(session, "unmute_outputs"), mock.patch.object(session, "wait_for_quiet"), \
                mock.patch.object(session, "set_sound_output", return_value=sound_found) as snd, \
                mock.patch.object(session.time, "sleep", side_effect=lambda s: time_shift.append(s)):
            time_shift = []
            real = time.time
            with mock.patch.object(session.time, "time", side_effect=lambda: real() + sum(time_shift)):
                session.bring_back(kw, [], [SAVED], set(), {"org.kde.dolphin"}, media_items=list(media),
                                   sound=sound, timeout=120, settle=2)
        return sum(time_shift), snd

    def test_missing_video_does_not_hold_the_end(self):
        video = {"title": "Some video", "position": 60000000, "status": "Paused", "app": "google-chrome"}
        waited, _ = self.run_back(media=[video])
        self.assertLess(waited, 10)

    def test_sound_output_not_found_does_not_hold_the_end(self):
        waited, snd = self.run_back(sound={"name": "x", "nick": "y"})
        self.assertLess(waited, 30)
        self.assertTrue(snd.call_args[1].get("final"))


if __name__ == "__main__":
    unittest.main()
