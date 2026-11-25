"""The sound output is found again (measured 2026-11-25: a profile saved the
LG's output as "HDMI 0", before the monitor had told its name; the lookup by
name never succeeded and a Unite waited its whole 2 minutes)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import _isolated  # noqa: E402,F401  (before the package: temporary folders)
from plasma_reopen_windows import audio  # noqa: E402

PORT1 = "alsa_output.pci-0000_01_00.1.HiFi__HDMI1__sink"
NOW = [{"id": 58, "name": "alsa_output.pci-0000_01_00.1.HiFi__HDMI2__sink", "nick": "PHL27E1N1100A", "description": "2"},
       {"id": 59, "name": PORT1, "nick": "LG Ultra HD", "description": "1 [LG Ultra HD]"}]


class TestSoundOutput(unittest.TestCase):
    def pick(self, saved, sinks=NOW):
        with mock.patch.object(audio, "sinks", return_value=sinks), \
                mock.patch.object(audio, "default_sink", return_value={"id": 1}), \
                mock.patch.object(audio.subprocess, "run") as run, mock.patch.object(audio, "log"):
            ok = audio.set_sound_output(saved)
        return ok, run.call_args[0][0][-1] if run.called else None

    def test_generic_nick_found_by_port(self):
        self.assertEqual(self.pick({"name": PORT1, "nick": "HDMI 0"}), (True, "59"))

    def test_monitor_found_by_its_name(self):
        """The ports swapped: the LG is now on port 2."""
        swapped = [dict(NOW[1], id=70, name=NOW[0]["name"]), dict(NOW[0], id=71, name=PORT1)]
        self.assertEqual(self.pick({"name": PORT1, "nick": "LG Ultra HD"}, swapped), (True, "70"))

    def test_monitor_not_awake_yet_waits(self):
        self.assertEqual(self.pick({"name": PORT1, "nick": "LG Ultra HD"}, NOW[:1]), (False, None))


if __name__ == "__main__":
    unittest.main()
