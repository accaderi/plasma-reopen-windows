"""Reading tabs from browser session files (synthetic data, no real profiles)."""
import json
import os
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import browsers  # noqa: E402


def cmd(cid, payload):
    return struct.pack("<H", len(payload) + 1) + bytes([cid]) + payload


def pstr(s):
    b = s.encode()
    return struct.pack("<i", len(b)) + b + b"\0" * ((4 - len(b) % 4) % 4)


def pstr16(s):
    b = s.encode("utf-16-le")
    return struct.pack("<i", len(s)) + b + b"\0" * ((4 - len(b) % 4) % 4)


def nav(tab, index, url, title):
    body = struct.pack("<ii", tab, index) + pstr(url) + pstr16(title)
    return cmd(6, struct.pack("<I", len(body)) + body)


def snss(*commands):
    return b"SNSS" + struct.pack("<i", 3) + b"".join(commands)


class TestChrome(unittest.TestCase):
    def test_replay(self):
        data = snss(
            cmd(0, struct.pack("<ii", 1, 10)), cmd(0, struct.pack("<ii", 1, 11)), cmd(0, struct.pack("<ii", 2, 12)),
            cmd(2, struct.pack("<ii", 10, 0)), cmd(2, struct.pack("<ii", 11, 1)), cmd(2, struct.pack("<ii", 12, 0)),
            nav(10, 0, "https://a.example/", "A"), nav(11, 0, "https://b.example/", "B old"),
            nav(11, 1, "https://b.example/new", "B"), cmd(7, struct.pack("<ii", 11, 1)),
            nav(12, 0, "https://c.example/", "C"),
            cmd(0, struct.pack("<ii", 2, 13)), nav(13, 0, "https://gone.example/", "Gone"),
            cmd(16, struct.pack("<iiq", 13, 0, 0)),
            cmd(8, struct.pack("<ii", 1, 1)))
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "Default", "Sessions"))
        with open(os.path.join(d, "Default", "Sessions", "Session_1"), "wb") as f:
            f.write(data)
        wins = sorted(browsers.chrome_windows(d), key=lambda w: len(w["urls"]))
        self.assertEqual(wins[0], {"title": "C", "urls": ["https://c.example/"], "active": 0})
        # window 1: its second tab is in front
        self.assertEqual(wins[1], {"title": "B", "urls": ["https://a.example/", "https://b.example/new"],
                                   "active": 1})

    def test_front_tab_counted_among_web_pages_only(self):
        data = snss(
            cmd(0, struct.pack("<ii", 1, 10)), cmd(0, struct.pack("<ii", 1, 11)), cmd(0, struct.pack("<ii", 1, 12)),
            cmd(2, struct.pack("<ii", 10, 0)), cmd(2, struct.pack("<ii", 11, 1)), cmd(2, struct.pack("<ii", 12, 2)),
            nav(10, 0, "chrome://settings/", "Settings"), nav(11, 0, "https://a.example/", "A"),
            nav(12, 0, "https://b.example/", "B"), cmd(8, struct.pack("<ii", 1, 2)))
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "Default", "Sessions"))
        with open(os.path.join(d, "Default", "Sessions", "Session_1"), "wb") as f:
            f.write(data)
        # tab 2 of the strip is in front; without the settings page it is page 1
        self.assertEqual(browsers.chrome_windows(d),
                         [{"title": "B", "urls": ["https://a.example/", "https://b.example/"], "active": 1}])

    def test_not_snss(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"nope")
        with self.assertRaises(ValueError):
            list(browsers.chrome_commands(f.name))


class TestFirefox(unittest.TestCase):
    def test_lz4_literals_and_match(self):
        # "abcabcabcX": literals "abc", then a match of 6 at offset 3, then literal "X"
        block = bytes([0x32]) + b"abc" + struct.pack("<H", 3) + bytes([0x10]) + b"X"
        self.assertEqual(browsers.lz4_block(block, 10), b"abcabcabcX")

    def test_session(self):
        session = {"windows": [{"selected": 2, "tabs": [
            {"index": 1, "entries": [{"url": "https://x.example/", "title": "X"}]},
            {"index": 2, "entries": [{"url": "https://y.example/1", "title": "Y1"},
                                     {"url": "https://y.example/2", "title": "Y2"}]},
            {"index": 1, "entries": [{"url": "about:blank", "title": "New Tab"}]}]}]}
        raw = json.dumps(session).encode()
        # literal-only LZ4 block of any length
        n = len(raw)
        if n < 15:
            block = bytes([n << 4]) + raw
        else:
            ext, rest = [], n - 15
            while rest >= 255:
                ext.append(255)
                rest -= 255
            block = bytes([0xF0] + ext + [rest]) + raw
        d = tempfile.mkdtemp()
        os.makedirs(os.path.join(d, "sessionstore-backups"))
        with open(os.path.join(d, "sessionstore-backups", "recovery.jsonlz4"), "wb") as f:
            f.write(b"mozLz40\0" + struct.pack("<I", n) + block)
        self.assertEqual(browsers.firefox_windows(d),
                         [{"title": "Y2", "urls": ["https://x.example/", "https://y.example/2"], "active": 1}])


class TestMatching(unittest.TestCase):
    def test_window_title(self):
        self.assertEqual(browsers.window_title("News - Google Chrome"), "News")
        self.assertEqual(browsers.window_title("Docs — Mozilla Firefox"), "Docs")


if __name__ == "__main__":
    unittest.main()
