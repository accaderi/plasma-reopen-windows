"""Pure helper functions: paths, titles, similarity."""
import os
import sys
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import apps, media, session  # noqa: E402


class TestHelpers(unittest.TestCase):
    def test_real_path(self):
        self.assertEqual(apps.real_path("/run/user/1000/kio-fuse-AbC/file/mnt/x/a b.mkv"), "/mnt/x/a b.mkv")
        self.assertEqual(apps.real_path("/run/user/1000/kio-fuse-AbC/smb/host/share/f"), "smb://host/share/f")
        self.assertEqual(apps.real_path("/home/user/f.txt"), "/home/user/f.txt")

    def test_strip_caption(self):
        self.assertEqual(session.strip_caption("README.md - proj - Visual Studio Code"), "readme.md - proj")
        self.assertEqual(session.strip_caption("Models : bash — Konsole"), "models : bash")

    def test_similar(self):
        self.assertGreater(media.similar("Dark City (1998)", "dark city (1998) "), 0.95)
        self.assertLess(media.similar("YouTube", "Dark City"), 0.6)

    @unittest.skipUnless(apps.desktop_exists("org.kde.konsole"), "Konsole not installed")
    def test_command_with_file(self):
        cmd = apps.command_with_file("org.kde.konsole", "/tmp/x")
        self.assertEqual(cmd[-1], "/tmp/x")
        self.assertFalse(any(a.startswith("%") for a in cmd))


if __name__ == "__main__":
    unittest.main()


class TestKWinShared(unittest.TestCase):
    def test_one_connection_per_process(self):
        from plasma_reopen_windows import kwin
        with unittest.mock.patch.object(kwin.Gio, "bus_get_sync") as bus, \
                unittest.mock.patch.object(kwin.tempfile, "mkdtemp", return_value="/tmp/x"):
            kwin.KWin._shared = None
            a, b = kwin.KWin(), kwin.KWin()
            self.assertIs(a, b)
            self.assertEqual(bus.return_value.register_object.call_count, 1)
            kwin.KWin._shared = None
