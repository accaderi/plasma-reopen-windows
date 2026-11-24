"""plasma-reopen-windows: recognising monitors and placing windows on them.

A window is stored with the identity of its monitor and its position inside
that monitor, so it lands on the same monitor even if the monitor's connection
name changes (DP-9 -> DP-8 behind a dock) or the arrangement of monitors does.
"""


def identity(screen):
    """What identifies a monitor, most reliable first."""
    return {k: screen.get(k, "") for k in ("serial", "manufacturer", "model", "name")}


def match_screen(saved, screens):
    """The current monitor for a saved monitor identity, or None.
    Serial number first, then manufacturer + model (if unique), then the
    connection name."""
    if not saved:
        return None
    if saved.get("serial"):
        for s in screens:
            if s.get("serial") == saved["serial"]:
                return s
    same = [s for s in screens if s.get("manufacturer") == saved.get("manufacturer")
            and s.get("model") == saved.get("model") and saved.get("model")]
    if len(same) == 1:
        return same[0]
    for s in same or screens:
        if s.get("name") == saved.get("name"):
            return s
    return None


def screen_of(window, screens):
    """The monitor a window is on, from the monitor name KWin reports for it."""
    for s in screens:
        if s.get("name") == window.get("output"):
            return s
    return None


def relative(window, screen):
    """Window rectangle relative to the top-left corner of its monitor."""
    return [window["x"] - screen["x"], window["y"] - screen["y"], window["width"], window["height"]]


def absolute(rel, saved_screen, screen):
    """Rectangle on the current monitor, scaled if its size changed, and kept
    inside the monitor."""
    x, y, w, h = rel
    sw, sh = saved_screen.get("width") or screen["width"], saved_screen.get("height") or screen["height"]
    fx, fy = screen["width"] / sw, screen["height"] / sh
    if abs(fx - 1) > 0.01 or abs(fy - 1) > 0.01:
        x, y, w, h = x * fx, y * fy, w * fx, h * fy
    w, h = min(w, screen["width"]), min(h, screen["height"])
    x = min(max(x, 0), screen["width"] - w)
    y = min(max(y, 0), screen["height"] - h)
    return {"x": screen["x"] + x, "y": screen["y"] + y, "width": w, "height": h}


def annotate(windows, screens):
    """Add monitor identity and relative position to saved windows."""
    for w in windows:
        s = screen_of(w, screens)
        if s:
            w["screen"] = dict(identity(s), width=s["width"], height=s["height"])
            w["rel"] = relative(w, s)
    return windows


def target_geometry(saved_window, screens):
    """Where a saved window goes now, or None if its monitor is not there.
    Windows saved by older versions (no monitor identity) use the absolute
    position if that monitor name is still present."""
    if saved_window.get("screen") and saved_window.get("rel"):
        s = match_screen(saved_window["screen"], screens)
        return absolute(saved_window["rel"], saved_window["screen"], s) if s else None
    if saved_window.get("output") in {s.get("name") for s in screens}:
        return {k: saved_window[k] for k in ("x", "y", "width", "height")}
    return None
