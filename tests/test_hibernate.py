"""Windows minimized while hibernating: which ones, the wait, and back after."""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import config, hibernate  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class TestSettle(unittest.TestCase):
    def test_waits_through_the_pauses(self):
        """Measured shape: a pause of a few seconds, then the next step down."""
        c = Clock()
        steps = [1675, 1675, 1510, 1510, 1502, 1502, 1502, 1523, 1550] + [1443] * 7 + [1396] * 20
        values = iter(steps)
        start, end = hibernate.settle(lambda: next(values), c, c.sleep)
        self.assertEqual((start, end), (1675, 1396))
        self.assertLess(c.t, hibernate.SETTLE_MAX)

    def test_at_least_the_minimum(self):
        c = Clock()
        hibernate.settle(lambda: 1300, c, c.sleep)      # nothing to free
        self.assertEqual(c.t, hibernate.SETTLE_MIN)

    def test_gives_up_after_the_limit(self):
        c = Clock()
        n = iter(range(5000, 0, -10))                 # keeps shrinking
        hibernate.settle(lambda: next(n), c, c.sleep)
        self.assertEqual(c.t, hibernate.SETTLE_MAX)

    def test_without_a_reading(self):
        c = Clock()
        self.assertEqual(hibernate.settle(lambda: None, c, c.sleep), (None, None))
        self.assertEqual(c.t, 5)


class TestBeforeAfter(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.kw = mock.Mock()
        self.kw.windows.return_value = {"windows": [
            {"id": "top", "minimized": False, "stack": 5},
            {"id": "hidden", "minimized": True, "stack": 3},
            {"id": "bottom", "minimized": False, "stack": 1}]}
        self.kw.run.return_value = 2
        for x in (mock.patch.object(hibernate, "SAVED", os.path.join(d, "saved.json")),
                  mock.patch.object(hibernate, "STATE_DIR", d),
                  mock.patch.object(hibernate, "KWin", return_value=self.kw),
                  mock.patch.object(hibernate, "settle", return_value=(1600, 1400)),
                  mock.patch.object(hibernate, "gtt_mib", return_value=1600),
                  mock.patch.object(hibernate, "log"),
                  mock.patch.dict(config.CFG, {"hibernate_minimize": "yes"})):
            x.start()
            self.addCleanup(x.stop)

    def test_minimizes_the_shown_ones_and_brings_them_back_in_order(self):
        self.assertEqual(hibernate.before(), 2)
        js = self.kw.run.call_args_list[0][0][0]
        self.assertIn('["bottom", "top"]', js)
        self.assertIn("const on = true", js)
        self.kw.run.reset_mock()
        hibernate.after()
        show, order = [c[0][0] for c in self.kw.run.call_args_list]
        self.assertIn("const on = false", show)
        self.assertIn('["bottom", "top"]', order)     # bottom to top: the top one ends in front
        self.assertFalse(os.path.exists(hibernate.SAVED))

    def test_switched_off(self):
        config.CFG["hibernate_minimize"] = "no"
        self.assertEqual(hibernate.before(), 0)
        self.kw.run.assert_not_called()

    def test_after_without_before(self):
        self.assertEqual(hibernate.after(), 0)
        self.kw.run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
