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
        with mock.patch.dict(session.CFG, {"video_wait": "1"}), \
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


    def test_browser_window_not_coming_is_not_waited_for(self):
        """Chrome shows 1 of its 2 saved windows; the video was in the other:
        given up 8 s after Chrome's first window, not after 20 s + 30 s."""
        chrome = dict(SAVED, id="c1", app="google-chrome", caption="Mail - Google Chrome", urls=["https://m/"])
        lost = dict(SAVED, id="c2", app="google-chrome", caption="Video - YouTube - Google Chrome",
                    urls=["https://y/"])
        kw = mock.Mock()
        kw.windows.return_value = {"windows": [dict(chrome, id="{w}", pid=1)], "screens": []}
        kw.run.return_value = 1
        video = {"title": "Video - YouTube", "position": 60000000, "status": "Paused", "app": "google-chrome"}
        shift = []
        real = time.time
        with mock.patch.object(session, "mpris_players", return_value=[]), \
                mock.patch("plasma_reopen_windows.media.mpris_players", return_value=[]), \
                mock.patch.object(session, "relaunch"), mock.patch.object(session, "mute_outputs", return_value=[]), \
                mock.patch.object(session, "unmute_outputs"), mock.patch.object(session, "wait_for_quiet"), \
                mock.patch.object(session, "session_windows", return_value=[]), \
                mock.patch.object(session, "live_tabs", return_value=[]), \
                mock.patch.object(session.time, "sleep", side_effect=lambda x: shift.append(x)), \
                mock.patch.object(session.time, "time", side_effect=lambda: real() + sum(shift)):
            placer = session.bring_back(kw, [{"app": "google-chrome"}], [chrome, lost], set(), set(),
                                        media_items=[video], timeout=120, settle=20)
        self.assertLess(sum(shift), 12)
        self.assertEqual([c["id"] for cands in placer.todo.values() for c in cands], ["c2"])


    def test_progress_names_the_setting(self):
        """Each wait in the progress text names its setting, as the settings
        app shows it, so it is easy to find and shorten."""
        placer = mock.Mock(matched=[1], last_match=time.time(), todo={"x": [1]})
        placer.waiting.return_value = [1]
        placer.browser_until.return_value = 0
        media = mock.Mock(todo=[])
        texts = []
        with mock.patch.object(session, "progress", side_effect=lambda t, *a, **k: texts.append(t)):
            session.report_wait(placer, media, 3, time.time() + 100, 20, 0, 0)
            placer.waiting.return_value = []
            media.todo = [1]
            session.report_wait(placer, media, 3, time.time() + 100, 20, 0, 0, media_until=time.time() + 5)
            media.todo = []
            session.report_wait(placer, media, 3, time.time() + 100, 20, 0, 0, sound_until=time.time() + 5)
        self.assertIn("(“Seconds to wait for one more window” = 20 s)", texts[0])
        self.assertIn("(“Seconds to wait for a saved video to appear” = 20 s)", texts[1])
        self.assertIn("(“Seconds to wait for the sound output” = 20 s)", texts[2])


class TestLateVideo(unittest.TestCase):
    """A video saved paused whose tab loads only when shown (measured
    2026-11-27: it played out loud after the restore had given it up)."""
    VIDEO = {"title": "Saved video", "position": 710000000, "status": "Paused", "app": "google-chrome"}

    def test_put_back_when_it_appears_later(self):
        from plasma_reopen_windows import media
        self.assertTrue(media.watch_later([dict(self.VIDEO, tries=0)]))
        playing = {"bus": "org.mpris.MediaPlayer2.chrome", "title": "Saved video - YouTube", "trackid": "/t/1",
                   "position": 2000000, "status": "Playing", "can_seek": True, "live": False}
        seen = [[], [], [playing], [dict(playing, status="Paused", position=710000000)]]
        bus = mock.Mock()
        with mock.patch.object(media, "mpris_players", side_effect=lambda b: seen.pop(0) if len(seen) > 1 else seen[0]), \
                mock.patch.object(media.time, "sleep"):
            media.watch_late(bus, poll=0)
        calls = [c[0][3] for c in bus.call_sync.call_args_list]
        self.assertEqual(calls, ["Pause", "SetPosition"])
        self.assertFalse(os.path.exists(media.LATE))

    def test_not_watched_when_off(self):
        from plasma_reopen_windows import media
        with mock.patch.dict(media.CFG, {"video_late": "0"}):
            self.assertFalse(media.watch_later([dict(self.VIDEO)]))


if __name__ == "__main__":
    unittest.main()
