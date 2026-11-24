import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import profiles, reopen  # noqa: E402


def saved(app, caption, **extra):
    return dict({"app": app, "caption": caption, "output": "DP-9", "screen": {"model": "LG"}}, **extra)


def open_win(wid, app, caption, output="eDP-1"):
    return {"id": wid, "app": app, "caption": caption, "output": output}


PROFILE = {"name": "Work", "apps": [{"app": a, "enabled": True} for a in ("google-chrome", "org.kde.konsole", "vlc")],
           "windows": [saved("google-chrome", "YouTube - Google Chrome", urls=["https://youtube.com", "https://a"]),
                       saved("google-chrome", "Gmail - Google Chrome", urls=["https://mail.google.com"]),
                       saved("org.kde.konsole", "~ : bash — Konsole", tabs=["/home/user"]),
                       saved("org.kde.konsole", "~ : bash — Konsole", tabs=["/tmp"]),
                       saved("vlc", "film.mkv - VLC")]}


class TestPlan(unittest.TestCase):
    def test_pairs_best_titles_first(self):
        s = list(enumerate(PROFILE["windows"][:2]))
        o = [open_win("a", "google-chrome", "Gmail - Google Chrome"),
             open_win("b", "google-chrome", "YouTube - Google Chrome")]
        self.assertEqual(profiles.pair(s, o), {0: "b", 1: "a"})

    def test_changed_tab_is_a_question_not_a_duplicate(self):
        snap = {"windows": [open_win("a", "google-chrome", "Gmail - Google Chrome"),
                            open_win("b", "google-chrome", "Tyre shop - Google Chrome"),
                            open_win("k", "org.kde.konsole", "~ : bash — Konsole")], "screens": []}
        pl = profiles.make_plan(PROFILE, "unite", snap)
        self.assertEqual(pl["fixed"], {"1": "a", "2": "k"})
        self.assertEqual([q["saved"] for q in pl["questions"]], [0])        # YouTube window: ask
        self.assertEqual([s["id"] for s in pl["questions"][0]["spare"]], ["b"])
        self.assertEqual(pl["reopen"], [3])          # second Konsole: no spare window, open it again
        # vlc is not running: started as a whole, not part of the plan
        self.assertNotIn(4, pl["reopen"])

    def test_arrange_never_opens(self):
        snap = {"windows": [open_win("k", "org.kde.konsole", "~ : bash — Konsole")], "screens": []}
        pl = profiles.make_plan(PROFILE, "arrange", snap)
        self.assertEqual(pl["reopen"], [])

    def test_resolve_choices(self):
        pl = {"fixed": {"1": "a"}, "reopen": [3], "questions": [
            {"saved": 0, "can_open": True, "spare": [{"id": "b"}]},
            {"saved": 2, "can_open": True, "spare": [{"id": "c"}]}]}
        fixed, again = profiles.resolve(pl, {"0": "use:b", "2": "skip"}, "open")
        self.assertEqual((fixed, again), ({1: "a", 0: "b"}, [3]))
        fixed, again = profiles.resolve(pl, {}, "open")         # no answer: the setting's default
        self.assertEqual((fixed, again), ({1: "a"}, [3, 0, 2]))
        fixed, _ = profiles.resolve(pl, {"0": "use:a"}, "open")  # a window already taken is not used twice
        self.assertEqual(fixed, {1: "a"})


class TestReopen(unittest.TestCase):
    def test_folder_in_caption(self):
        self.assertTrue(reopen._folder_in_caption("/x/my_project", "notes.txt - my_project - Visual Studio Code"))
        self.assertTrue(reopen._folder_in_caption("/x/Downloads", "Downloads — Dolphin"))
        self.assertFalse(reopen._folder_in_caption("/x/40", "notes.txt - my_project - Visual Studio Code"))

    def test_commands(self):
        with mock.patch.object(reopen, "exec_args", return_value=["prog"]):
            self.assertEqual(reopen.command({"app": "google-chrome", "urls": ["u1", "u2"]}, "d")["cmd"],
                             ["prog", "--new-window", "u1", "u2"])
            self.assertEqual(reopen.command({"app": "org.kde.dolphin", "folders": ["/a b/[x]"]}, "d")["cmd"],
                             ["prog", "--new-window", "file:///a%20b/%5Bx%5D"])
            self.assertEqual(reopen.command({"app": "code", "folder": "/p"}, "d")["cmd"],
                             ["prog", "--new-window", "/p"])
            self.assertIsNone(reopen.command({"app": "x"}, "d"))
        self.assertEqual(reopen.command({"app": "org.kde.konsole", "tabs": ["/a", "/b"]}, None)["konsole"],
                         [["/a", "/b"]])


if __name__ == "__main__":
    unittest.main()


class TestSummary(unittest.TestCase):
    def test_text(self):
        self.assertEqual(profiles.summary(["Kontact"], ["Dolphin"], 1, 9),
                         "Closed Kontact; started Dolphin; opened 1 window; put 9 windows in place.")
        self.assertEqual(profiles.summary(), "Nothing to change.")


class TestMerge(unittest.TestCase):
    def test_new_window_and_switched_off_app_join_the_profile(self):
        p = {"apps": [{"app": "firefox", "name": "Firefox", "enabled": True},
                      {"app": "dragon", "name": "Dragon Player", "enabled": False}],
             "windows": [{"app": "firefox", "caption": "A"}, {"app": "dragon", "caption": "old place"}]}
        st = {"apps": {"firefox": [], "dragon": [], "vlc": []},
              "wins": [{"id": "1", "app": "firefox"}, {"id": "2", "app": "firefox"},
                       {"id": "3", "app": "dragon"}, {"id": "4", "app": "vlc"}]}
        built = {"apps": [{"app": "firefox"}, {"app": "dragon"}, {"app": "vlc", "name": "VLC"}],
                 "windows": [{"app": "firefox", "caption": "A"}, {"app": "firefox", "caption": "new"},
                             {"app": "dragon", "caption": "laptop"}, {"app": "vlc", "caption": "film"}]}
        added = profiles.merge(p, st, built, matched_ids={"1"})
        self.assertEqual(added, ["Firefox", "Dragon Player", "VLC"])
        self.assertEqual([w["caption"] for w in p["windows"]], ["A", "new", "laptop", "film"])
        self.assertTrue(all(a["enabled"] for a in p["apps"]))


class TestBrowserWindow(unittest.TestCase):
    """How one browser window is opened again (behaviour checked with a real
    Chrome and Firefox in temporary profiles, see INVESTIGATION.md)."""
    URLS = ["https://mail/", "https://b/", "https://youtube/watch"]

    def spec(self, app, **w):
        with mock.patch.object(reopen, "exec_args", return_value=["browser"]):
            return reopen.command(dict({"app": app, "urls": self.URLS}, **w), "d")

    def test_chrome_front_tab_last(self):
        s = self.spec("google-chrome", active=2)
        self.assertEqual(s["cmd"], ["browser", "--new-window", "https://mail/", "https://b/"])
        self.assertEqual(s["then"], ["browser", "https://youtube/watch"])          # added to that window
        self.assertEqual(s["alone"], ["browser", "--new-window", "https://youtube/watch"])

    def test_chrome_front_tab_first_or_unknown(self):
        for w in ({}, {"active": 0}, {"active": 9}):
            s = self.spec("google-chrome", **w)
            self.assertEqual(s["cmd"], ["browser", "--new-window"] + self.URLS)
            self.assertNotIn("then", s)

    def test_firefox_one_window(self):
        # "--new-window A B" would split them over two windows in Firefox
        self.assertEqual(self.spec("org.mozilla.firefox", active=2)["cmd"], ["browser"] + self.URLS)
        with mock.patch.object(reopen, "exec_args", return_value=["browser"]):
            one = reopen.command({"app": "org.mozilla.firefox", "urls": ["https://a/"]}, "d")
        self.assertEqual(one["cmd"], ["browser", "--new-window", "https://a/"])


class TestOpenInSteps(unittest.TestCase):
    def run_steps(self, appears):
        from plasma_reopen_windows import apps
        kw = mock.Mock()
        kw.windows.side_effect = [{"windows": []}] + [
            {"windows": [{"id": "new", "app": "google-chrome"}] if appears else []}] * 200
        kw.run.return_value = True
        spec = {"app": "google-chrome", "desktop": "google-chrome", "cmd": ["c", "--new-window", "u1"],
                "then": ["c", "u2"], "alone": ["c", "--new-window", "u2"]}
        with mock.patch.object(apps.subprocess, "Popen") as popen, mock.patch.object(apps.time, "sleep"), \
                mock.patch.object(apps, "log"), mock.patch.object(apps.time, "time",
                                                                  side_effect=[float(i) for i in range(500)]):
            apps.open_in_steps(kw, spec)
        return [c.args[0][-len(spec["cmd"]):] if i == 0 else c.args[0][4:] for i, c in enumerate(popen.call_args_list)], kw

    def test_window_made_active_before_the_rest(self):
        calls, kw = self.run_steps(appears=True)
        self.assertEqual(calls, [["c", "--new-window", "u1"], ["c", "u2"]])
        self.assertIn('"new"', kw.run.call_args.args[0])          # the new window was activated

    def test_never_into_another_window(self):
        calls, _ = self.run_steps(appears=False)
        self.assertEqual(calls, [["c", "--new-window", "u1"], ["c", "--new-window", "u2"]])


class TestVideos(unittest.TestCase):
    GTA = {"title": "GTA 6 - Wanted Level - YouTube", "position": 6000000, "status": "Paused", "app": "google-chrome"}
    GTA_COPY = {"title": "GTA 6 - Wanted Level", "position": 6000000, "status": "Paused", "app": None}
    WIN = {"app": "google-chrome", "caption": "GTA 6 - Wanted Level - YouTube - Google Chrome", "urls": ["u"]}

    def test_each_video_once(self):
        self.assertEqual(profiles.one_per_video([self.GTA_COPY, self.GTA]), [self.GTA])

    def test_video_of_a_window_opened_again(self):
        # Chrome is running (not started), the window with the video is opened again
        self.assertEqual(profiles.videos_for([self.GTA, self.GTA_COPY], set(), [self.WIN], []), [self.GTA])

    def test_video_open_already_is_left_alone(self):
        self.assertEqual(profiles.videos_for([self.GTA], {"google-chrome"}, [self.WIN],
                                             ["GTA 6 - Wanted Level - YouTube"]), [])

    def test_video_of_a_window_not_opened_is_not_waited_for(self):
        self.assertEqual(profiles.videos_for([self.GTA], set(), [], []), [])

    def test_video_of_an_app_started_now(self):
        self.assertEqual(profiles.videos_for([self.GTA], {"google-chrome"}, [], []), [self.GTA])


class TestMissingAfterStart(unittest.TestCase):
    def test_only_windows_of_apps_started_now_and_not_twice(self):
        started_win = {"app": "google-chrome", "caption": "A", "urls": ["u"]}
        reopened = {"app": "google-chrome", "caption": "B", "urls": ["u"]}
        running_app = {"app": "org.kde.dolphin", "caption": "C", "folders": ["/x"]}
        nothing = {"app": "google-chrome", "caption": "D"}               # nothing saved to open it with
        todo = {"google-chrome": [started_win, reopened, nothing], "org.kde.dolphin": [running_app]}
        self.assertEqual(profiles.missing_after_start(todo, {"google-chrome"}, [reopened]), [started_win])


class TestUnknownVideos(unittest.TestCase):
    def test_pauses_only_new_unsaved_videos(self):
        from plasma_reopen_windows import media
        before = [{"bus": "music", "title": "Song", "status": "Playing"}]
        after = before + [
            {"bus": "chrome", "title": "Saved video - YouTube", "status": "Playing"},     # Media handles it
            {"bus": "chrome2", "title": "Other video - YouTube", "status": "Playing"},    # unknown: pause
            {"bus": "ff", "title": "Paused one", "status": "Paused"}]
        bus = mock.Mock()
        with mock.patch.object(media, "mpris_players", side_effect=[before, after]):
            u = media.Unknown(bus, ["Saved video"])
            u.step()
        paused = [c.args[0] for c in bus.call_sync.call_args_list if c.args[3] == "Pause"]
        self.assertEqual(paused, ["chrome2"])          # not the music that played before, not the saved video
        self.assertEqual(u.paused, ["Other video - YouTube"])


class TestSameVideo(unittest.TestCase):
    def test_strict(self):
        from plasma_reopen_windows.media import same_video
        self.assertTrue(same_video("GTA 6 - Wanted Level - YouTube", "GTA 6 - Wanted Level"))
        self.assertTrue(same_video("Film", "film"))
        self.assertFalse(same_video("Video A", "Video C"))
        self.assertFalse(same_video("Episode 1", "Episode 10"))
        self.assertFalse(same_video("Episode 1", "Episode 2 - YouTube"))
        self.assertFalse(same_video("", "x"))


class TestLiveVideo(unittest.TestCase):
    """A live stream (measured 2026-11-24): Chrome's own report has an endless
    length and 4 s as position at 10.5 h; the browser integration has the place."""
    H = 3600 * 10 ** 6
    CHROME = {"bus": "chromium", "title": "Session live - YouTube", "trackid": "/c", "position": 4 * 10 ** 6,
              "status": "Playing", "can_seek": True, "length": 2 ** 63 - 1, "live": True}
    PBI = {"bus": "pbi", "title": "Session live", "trackid": "/p", "position": 0, "status": "Playing",
           "can_seek": True, "length": 11 * H, "live": False}

    def test_saved_with_its_place(self):
        saved = profiles.one_per_video([
            {"title": "Session live - YouTube", "position": 4 * 10 ** 6, "status": "Playing",
             "app": "google-chrome", "live": True},
            {"title": "Session live", "position": 10 * self.H, "status": "Playing", "app": None, "live": False}])
        self.assertEqual(saved, [{"title": "Session live - YouTube", "position": 10 * self.H, "status": "Playing",
                                  "app": "google-chrome", "live": True, "place": True}])

    def run_media(self, saved, players):
        from plasma_reopen_windows import media
        bus = mock.Mock()
        with mock.patch.object(media, "mpris_players", return_value=[dict(p) for p in players]), \
                mock.patch.object(media, "log"):
            m = media.Media(bus, [saved])
            m.step()
        return m, [(c.args[0], c.args[3]) + ((c.args[4].unpack()[1],) if c.args[4] else ())
                   for c in bus.call_sync.call_args_list]

    def test_seeks_with_the_report_that_knows_the_place(self):
        saved = {"title": "Session live - YouTube", "position": 10 * self.H, "status": "Playing",
                 "app": "google-chrome", "live": True, "place": True}
        _, calls = self.run_media(saved, [self.CHROME, self.PBI])
        self.assertEqual(calls, [("pbi", "SetPosition", 10 * self.H)])     # never Chrome's own: that is the start

    def test_no_place_known_no_jump(self):
        saved = {"title": "Session live - YouTube", "position": 4 * 10 ** 6 + 10 ** 7, "status": "Playing",
                 "app": "google-chrome", "live": True}
        m, calls = self.run_media(saved, [self.CHROME])
        self.assertEqual(calls, [])
        self.assertFalse(m.pending())

    def test_playing_far_ahead_is_brought_back(self):
        """YouTube starts a live stream at its live edge, 1 h after the saved
        place: that is not "in place" (measured bug)."""
        saved = {"title": "Session live - YouTube", "position": 10 * self.H, "status": "Playing",
                 "app": "google-chrome", "live": True, "place": True}
        _, calls = self.run_media(saved, [self.CHROME, dict(self.PBI, position=11 * self.H)])
        self.assertEqual(calls, [("pbi", "SetPosition", 10 * self.H)])
        _, calls = self.run_media(saved, [self.CHROME, dict(self.PBI, position=10 * self.H + 2 * 10 ** 6)])
        self.assertEqual(calls, [])                 # 2 s on: it is playing, fine
