"""Profiles: names, format, keeping the user's edits, menu entries, login setting."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import config, profiles  # noqa: E402


def snapshot(caption_chrome="Work mail - Google Chrome"):
    chrome = {"id": "{1}", "pid": 10, "app": "google-chrome", "cls": "google-chrome", "caption": caption_chrome,
              "output": "DP-9", "x": 2000, "y": 10, "width": 900, "height": 700, "stack": 3,
              "screen": {"serial": "12345", "model": "LG Ultra HD"}, "rel": [80, 10, 900, 700]}
    konsole = {"id": "{2}", "pid": 20, "app": "org.kde.konsole", "cls": "org.kde.konsole",
               "caption": "~ : bash — Konsole", "output": "eDP-1", "x": 5, "y": 740, "width": 700,
               "height": 500, "stack": 1}
    return {"snap": {"screens": [{"name": "DP-9"}]}, "wins": [chrome, konsole],
            "apps": {"google-chrome": [chrome], "org.kde.konsole": [konsole]},
            "konsole": {20: [["/home"]]}, "files": {}, "sound": None}


def fake_spec(app, kind, wins, konsole, files):
    return {"app": app, "desktop": app}


@mock.patch.object(profiles, "tabs_of", lambda app, caps, ff=(): [None] * len(caps))
@mock.patch.object(profiles, "launch_spec", fake_spec)
@mock.patch.object(profiles, "app_name", lambda a: a)
@mock.patch.object(profiles, "find_desktop", lambda a, w: a)
class TestBuild(unittest.TestCase):
    def test_runtime_keys_dropped_and_urls_field(self):
        p = profiles.build("Work", snapshot())
        self.assertEqual(p["schema"], profiles.SCHEMA)
        for w in p["windows"]:
            self.assertNotIn("id", w)
            self.assertNotIn("pid", w)
        chrome = [w for w in p["windows"] if w["app"] == "google-chrome"][0]
        self.assertEqual(chrome["urls"], [])
        self.assertNotIn("urls", [w for w in p["windows"] if w["app"] == "org.kde.konsole"][0])

    def test_tabs_read_from_browser(self):
        with mock.patch.object(profiles, "tabs_of",
                               lambda app, caps, ff=(): [{"urls": ["https://a/", "https://b/"], "active": 1}]):
            p = profiles.build("Work", snapshot())
        chrome = [w for w in p["windows"] if w["app"] == "google-chrome"][0]
        self.assertEqual(chrome["urls"], ["https://a/", "https://b/"])
        self.assertEqual(chrome["active"], 1)           # the tab in front is kept too

    def test_resave_keeps_user_edits(self):
        old = profiles.build("Work", snapshot())
        old["apps"][0]["enabled"] = False
        old["apps"][0]["start_delay"] = 5
        old["windows"][0]["urls"] = ["https://example.org"]
        new = profiles.build("Work", snapshot("Work mail (3) - Google Chrome"), old)
        a = [x for x in new["apps"] if x["app"] == old["apps"][0]["app"]][0]
        self.assertFalse(a["enabled"])
        self.assertEqual(a["start_delay"], 5)
        self.assertEqual([w for w in new["windows"] if w["app"] == "google-chrome"][0]["urls"],
                         ["https://example.org"])
        self.assertEqual(new["created"], old["created"])


class TestFormat(unittest.TestCase):
    def test_slug(self):
        self.assertEqual(profiles.slug("Video editing!"), "video-editing")
        self.assertEqual(profiles.slug("  "), "profile")

    def test_migrate_fills_defaults(self):
        p = profiles.migrate({"name": "x", "apps": [{"app": "a"}]})
        self.assertTrue(p["apps"][0]["enabled"])
        self.assertEqual(p["apps"][0]["start_delay"], 0)
        self.assertEqual(p["windows"], [])

    def test_newer_format_refused(self):
        with self.assertRaises(ValueError):
            profiles.migrate({"name": "x", "schema": profiles.SCHEMA + 1})

    def test_menu_entry(self):
        text = profiles.menu_entry({"name": "Video editing"}, "/bin/prw")
        self.assertIn("Name=Load profile: Video editing", text)
        self.assertIn("Exec=/bin/prw profile load video-editing", text)

    def test_save_rename_delete_files(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(profiles, "PROFILES_DIR", d), mock.patch.object(profiles, "MENU_DIR", d), \
                mock.patch.object(profiles.subprocess, "Popen"):
            profiles.write_json(profiles.path_for("A b"), {"name": "A b", "apps": [], "windows": []})
            profiles.sync_menu()
            self.assertTrue(os.path.exists(os.path.join(d, "plasma-reopen-windows-profile-a-b.desktop")))
            profiles.rename("A b", "C")
            self.assertIsNotNone(profiles.find("C"))
            self.assertIsNone(profiles.find("A b"))
            self.assertFalse(os.path.exists(os.path.join(d, "plasma-reopen-windows-profile-a-b.desktop")))
            profiles.delete("C")
            self.assertEqual(profiles.all_profiles(), [])


class TestLoginSetting(unittest.TestCase):
    def test_values(self):
        st = config.find_setting("on_login")
        for ok in ("last", "ask", "nothing", "profile: Work"):
            config.check_value(st, ok)
        self.assertEqual(config.check_value(st, "profile: Work"), "profile:Work")
        for bad in ("profile:", "later"):
            with self.assertRaises(ValueError):
                config.check_value(st, bad)


if __name__ == "__main__":
    unittest.main()


class TestRenameDeleteClean(unittest.TestCase):
    def test_rename_delete_and_clean_old_automatic(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(profiles, "PROFILES_DIR", d), mock.patch.object(profiles, "MENU_DIR", d), \
                mock.patch.object(profiles.subprocess, "Popen"):
            profiles.write_json(profiles.path_for("Work"), {"name": "Work"})
            profiles.rename("Work", "Office")
            self.assertIsNone(profiles.find("Work"))
            self.assertIsNotNone(profiles.find("Office"))
            profiles.delete("Office")
            self.assertEqual(profiles.all_profiles(), [])
            # automatic profiles of older versions ("Before NAME", "Undo try") are cleaned away
            profiles.write_json(profiles.path_for("Before X"), {"name": "Before X", "auto": True})
            profiles.write_json(profiles.path_for("Y"), {"name": "Y"})
            self.assertEqual(profiles.clean(), ["Before X"])
            self.assertEqual([p["name"] for p in profiles.all_profiles()], ["Y"])

    def test_undo_point_is_not_a_profile(self):
        self.assertFalse(profiles.UNDO.startswith(profiles.PROFILES_DIR))


class TestSaveEditAndUndo(unittest.TestCase):
    def test_save_reloads_and_undo_only_reloads(self):
        d = tempfile.mkdtemp()
        with mock.patch.object(profiles, "PROFILES_DIR", d), mock.patch.object(profiles, "UNDO", d + "/undo.json"), \
                mock.patch.object(profiles, "reload", return_value=True) as reload:
            old = profiles.migrate({"name": "Work", "apps": [{"app": "a"}, {"app": "b"}], "windows": []})
            profiles.write_json(profiles.path_for("Work"), old)
            new = dict(old, apps=[{"app": "a", "enabled": False, "start_delay": 0}])
            profiles.save_edit(new)
            self.assertEqual([a["app"] for a in profiles.find("Work")["apps"]], ["a"])
            self.assertEqual(reload.call_args[0][0]["apps"], new["apps"])      # the screens follow the save
            profiles.undo()
            # undo puts the old version on the screens but never writes the file
            self.assertEqual([a["app"] for a in profiles.find("Work")["apps"]], ["a"])
            self.assertEqual([a["app"] for a in reload.call_args[0][0]["apps"]], ["a", "b"])
            self.assertFalse(profiles.can_undo())
