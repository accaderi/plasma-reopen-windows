"""Browser windows recognized by their pages when the title does not help
(measured 2026-11-25: after a Chrome update, "What's New" was put in front of a
restored window; a window opened shortly before the restart was missing from
Chrome's session file)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import browsers, session  # noqa: E402

LG = {"x": 1920, "y": 0, "width": 2259, "height": 1271, "maximize": 3, "fullscreen": False, "keepAbove": False,
      "minimized": False, "desktops": [], "output": "DP-9", "screen": {"model": "LG"}, "rel": [0, 0, 2259, 1271]}


def saved(sid, caption, urls, active=0):
    return dict(LG, id=sid, app="google-chrome", caption=caption + " - Google Chrome", urls=urls, active=active)


VIDEO = saved("s1", "Video - YouTube", ["https://www.google.com/search?q=a", "https://youtube.com/watch?v=1",
                                        "https://youtube.com/watch?v=2"], active=2)
SHOP = saved("s2", "Search results - Shop", ["https://shop.example/search?q=phone"])
MUSIC = saved("s3", "Music - YouTube", ["https://youtube.com/watch?v=9", "https://example.org/"])


class TestWindowTabs(unittest.TestCase):
    def test_stale_session_and_missing_window(self):
        """The session file has an older front tab for one window and lacks
        another: the pages come from the file by the live front page, and the
        missing window gets its front page."""
        stale = [{"title": "YouTube", "urls": ["https://example.org/", "https://youtube.com/watch?v=9"], "active": 0}]
        live = {"Music - YouTube": [("7", "https://youtube.com/watch?v=9")],
                "Search results - Shop": [("8", "https://shop.example/search?q=phone")]}
        wins = [{"id": "a", "caption": "Music - YouTube - Google Chrome"},
                {"id": "b", "caption": "Search results - Shop - Google Chrome"}]
        with mock.patch.object(browsers, "session_windows", return_value=list(stale)), \
                mock.patch.object(browsers, "live_tabs", side_effect=lambda bus, t: live.get(t, [])):
            got = browsers.window_tabs(None, {"google-chrome": wins})
        self.assertEqual(got["a"], {"urls": stale[0]["urls"], "active": 1})
        self.assertEqual(got["b"], {"urls": ["https://shop.example/search?q=phone"], "active": 0})

    def test_overlap_ignores_page_parts(self):
        self.assertEqual(browsers.tab_overlap(["https://a/x#top", "https://b/"], ["https://a/x", "chrome://whats-new/"]),
                         0.5)


class TestPlacer(unittest.TestCase):
    def setUp(self):
        self.kw = mock.Mock()
        now = {"id": "{new}", "app": "google-chrome", "caption": "What's New - Google Chrome", "output": "eDP-1",
               "pid": 1}
        self.kw.windows.return_value = {"windows": [now], "screens": []}
        # Chrome's session now: the What's New tab put in front of the saved pages
        self.file = [{"title": "What's New", "urls": ["chrome://whats-new/"] + VIDEO["urls"], "active": 0}]
        p = mock.patch.object(session, "session_windows", side_effect=lambda app, ff=(): self.file)
        p.start()
        self.addCleanup(p.stop)

    def test_matched_by_pages_placed_and_front_tab_back(self):
        """Not the window with the most similar title; placed although
        "Remember Window Positions" handles Chrome (move is empty)."""
        placer = session.Placer(self.kw, {"windows": [SHOP, VIDEO]}, set(), move=set())
        with mock.patch.object(session, "bring_tab_to_front", return_value=True) as front:
            placer.step()
            placer.step()
        self.assertEqual([(c["id"], wid) for c, wid in placer.matched], [("s1", "{new}")])
        self.assertTrue(self.kw.run.called)                       # moved
        front.assert_called_once_with(self.kw.bus, "Video - YouTube")

    def test_title_match_left_to_the_script(self):
        self.kw.windows.return_value["windows"][0]["caption"] = "Video - YouTube - Google Chrome"
        placer = session.Placer(self.kw, {"windows": [VIDEO]}, set(), move=set())
        placer.step()
        self.assertEqual(len(placer.matched), 1)
        self.assertFalse(self.kw.run.called)

    def test_new_window_not_taken_for_a_saved_one(self):
        """A window you open during the restore, with none of the saved pages,
        is not paired with the saved window that is still missing."""
        self.kw.windows.return_value["windows"][0]["caption"] = "Weather - Google Chrome"
        self.file = [{"title": "Weather", "urls": ["https://weather.example/"], "active": 0}]
        placer = session.Placer(self.kw, {"windows": [SHOP]}, set(), move=set())
        for _ in range(15):
            placer.step()
        self.assertEqual(placer.matched, [])
        self.assertFalse(self.kw.run.called)

    def test_pages_not_written_yet_waits_longer(self):
        self.kw.windows.return_value["windows"][0]["caption"] = "Something - Google Chrome"
        self.file = []
        placer = session.Placer(self.kw, {"windows": [SHOP]}, set(), move=set())
        for _ in range(11):
            placer.step()
        self.assertEqual(placer.matched, [])
        placer.step()
        self.assertEqual(len(placer.matched), 1)     # then the remaining one, as before

    def test_new_tab_window_not_taken_for_a_saved_one(self):
        """Chrome after a crash opens a New Tab window: it has no pages, so it
        is never the saved window with pages (measured 2026-11-26: it was taken
        for one by elimination, and that window's 17 pages never came back)."""
        self.kw.windows.return_value["windows"][0]["caption"] = "New Tab - Google Chrome"
        self.file = [{"title": "New Tab", "urls": [], "active": 0}]
        placer = session.Placer(self.kw, {"windows": [SHOP]}, set(), move=set())
        for _ in range(15):
            placer.step()
        self.assertEqual(placer.matched, [])

    def test_lost_window_found(self):
        placer = session.Placer(self.kw, {"windows": [SHOP, MUSIC]}, set(), move=set())
        self.file = [{"title": "Music", "urls": MUSIC["urls"], "active": 0}]
        with mock.patch.object(session, "live_tabs", return_value=[]):
            self.assertEqual([c["id"] for c in placer.lost()], ["s2"])
        with mock.patch.object(session, "live_tabs", return_value=[("8", "x")]):
            self.assertEqual(placer.lost(), [])                   # open somewhere after all

    def test_lost_window_opened_again_and_placed(self):
        placer = session.Placer(self.kw, {"windows": [SHOP]}, set(), move=set())
        with mock.patch.object(session, "live_tabs", return_value=[]), \
                mock.patch.object(session, "relaunch") as relaunch, mock.patch("time.sleep"):
            def appear(specs, kw):
                self.kw.windows.return_value = {"windows": [
                    {"id": "{shop}", "app": "google-chrome", "caption": SHOP["caption"], "pid": 1}], "screens": []}
            relaunch.side_effect = appear
            session.reopen_lost(self.kw, placer, [{"app": "google-chrome", "desktop": "google-chrome"}], wait=5)
        spec = relaunch.call_args[0][0][0]
        self.assertIn("https://shop.example/search?q=phone", spec["cmd"])
        self.assertEqual(placer.matched[0][1], "{shop}")
        self.assertTrue(self.kw.run.called)                       # placed by this tool


if __name__ == "__main__":
    unittest.main()
