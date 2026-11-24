"""plasma-reopen-windows: sound outputs (PipeWire/WirePlumber)."""

import json
import re
import subprocess
import time
from .config import log


def sinks():
    try:
        data = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=10).stdout or "[]")
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return []
    out = []
    for o in data:
        p = o.get("info", {}).get("props", {}) if o.get("type") == "PipeWire:Interface:Node" else {}
        if p.get("media.class") == "Audio/Sink":
            out.append({"id": o["id"], "name": p.get("node.name", ""), "nick": p.get("node.nick", ""),
                        "description": p.get("node.description", "")})
    return out


def wait_for_quiet(least=3, most=8):
    """Wait until no app plays sound (the login sound), within least..most s."""
    start = time.time()
    time.sleep(least)
    while time.time() - start < most:
        try:
            data = json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True,
                                             timeout=10).stdout or "[]")
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return
        playing = [o for o in data if o.get("type") == "PipeWire:Interface:Node"
                   and o.get("info", {}).get("props", {}).get("media.class") == "Stream/Output/Audio"
                   and o.get("info", {}).get("state") == "running"]
        if not playing:
            return
        time.sleep(0.5)


def mute_outputs(quiet=False):
    """Mute every sound output that is not muted yet; returns their names.
    Names, not ids: an output that reconnects (Bluetooth) gets a new id."""
    names = []
    for s in sinks():
        r = subprocess.run(["wpctl", "get-volume", str(s["id"])], capture_output=True, text=True, check=False)
        if "MUTED" not in r.stdout:
            subprocess.run(["wpctl", "set-mute", str(s["id"]), "1"], check=False)
            names.append(s["name"])
    if names and not quiet:
        log("sound muted until videos are back at their position or paused")
    return names


def unmute_outputs(names):
    """Unmute the outputs muted by mute_outputs, under their current ids."""
    if not names:
        return
    for s in sinks():
        if s["name"] in names:
            subprocess.run(["wpctl", "set-mute", str(s["id"]), "0"], check=False)
    log("sound on again")


def default_sink():
    try:
        r = subprocess.run(["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r'node\.name = "([^"]*)"', r.stdout)
    return next((s for s in sinks() if m and s["name"] == m.group(1)), None)


def set_sound_output(saved, final=False):
    """Select the saved output again. Monitors (HDMI/DisplayPort) are found by
    the monitor's name: their port numbers can change between boots."""
    all_sinks = sinks()
    # HDMI/DisplayPort outputs change numbers between boots: find them by the
    # monitor's name. Others (Bluetooth, USB, built-in) keep their name.
    if re.search(r"HDMI|DisplayPort|\.DP", saved["name"]) and saved["nick"]:
        by_nick = [s for s in all_sinks if s["nick"] == saved["nick"]]
        target = by_nick[0] if len(by_nick) == 1 else None
    else:
        target = next((s for s in all_sinks if s["name"] == saved["name"]), None)
    if target is None:
        if not final:
            return False      # the monitor may still be waking up
        target = next((s for s in all_sinks if s["name"] == saved["name"]), None)
        if target is None:
            log("sound output %s not found" % (saved["nick"] or saved["name"]))
            return False
    cur = default_sink()
    if cur and cur["id"] == target["id"]:
        log("sound output: %s (already selected)" % (target["description"] or target["name"]))
    else:
        subprocess.run(["wpctl", "set-default", str(target["id"])], check=False)
        log("sound output: %s selected again" % (target["description"] or target["name"]))
    return True
