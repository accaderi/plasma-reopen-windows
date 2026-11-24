"""Settings: validation, comment-preserving edits, upgrades."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import config  # noqa: E402


class TestCheckValue(unittest.TestCase):
    def test_bool(self):
        st = config.find_setting("confirm")
        self.assertEqual(config.check_value(st, "Off"), "no")
        self.assertEqual(config.check_value(st, "1"), "yes")
        with self.assertRaises(ValueError):
            config.check_value(st, "perhaps")

    def test_int_range(self):
        st = config.find_setting("close_timeout")
        self.assertEqual(config.check_value(st, " 045 "), "45")
        for bad in ("4", "601", "x"):
            with self.assertRaises(ValueError):
                config.check_value(st, bad)

    def test_choice_and_list(self):
        self.assertEqual(config.check_value(config.find_setting("place_windows"), "yes"), "yes")
        with self.assertRaises(ValueError):
            config.check_value(config.find_setting("place_windows"), "maybe")
        self.assertEqual(config.check_value(config.find_setting("exclude"), " a   b "), "a b")

    def test_unknown_setting(self):
        with self.assertRaises(ValueError):
            config.find_setting("nope")


class TestConfFile(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "x.conf")

    def test_set_keeps_comments(self):
        with open(self.path, "w") as f:
            f.write("# my note\n[general]\n# about confirm\n#confirm = yes\n")
        config.conf_set(self.path, "confirm", "no")
        text = open(self.path).read()
        self.assertIn("# my note", text)
        self.assertIn("# about confirm\nconfirm = no", text)

    def test_set_comments_out_duplicates(self):
        with open(self.path, "w") as f:
            f.write("[general]\nconfirm = yes\nconfirm = no\n")
        config.conf_set(self.path, "confirm", "yes")
        self.assertEqual(open(self.path).read().count("\nconfirm ="), 1)

    def test_reset(self):
        config.conf_set(self.path, "close_timeout", "60")
        config.conf_set(self.path, "close_timeout", "", reset=True)
        self.assertIn("#close_timeout = 30", open(self.path).read())

    def test_upgrade_keeps_values(self):
        with open(self.path, "w") as f:
            f.write("[general]\nclose_timeout = 45\n")
        self.assertTrue(config.conf_upgrade(self.path))
        text = open(self.path).read()
        self.assertIn("close_timeout = 45", text)
        self.assertIn("#confirm = yes", text)
        self.assertTrue(os.path.exists(self.path + ".old"))
        self.assertFalse(config.conf_upgrade(self.path))   # second run: nothing to do

    def test_template_lists_every_setting(self):
        t = config.conf_template()
        for st in config.SETTINGS:
            self.assertIn("#%s = %s" % (st["key"], st["default"]), t)


if __name__ == "__main__":
    unittest.main()
