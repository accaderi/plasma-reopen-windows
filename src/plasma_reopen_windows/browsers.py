"""plasma-reopen-windows: the open tabs of each browser window, read from the
browser's own session file (read only, nothing is changed).

Chrome and Chromium-based browsers write "Sessions/Session_*" (SNSS: a log of
commands that is replayed here); Firefox writes "sessionstore-backups/
recovery.jsonlz4" (JSON, LZ4-compressed). A window is matched to KWin's window
by its active tab: that tab's title is the window title.
"""

import glob
import json
import os
import struct

CHROME_DIRS = {
    "google-chrome": "~/.config/google-chrome",
    "com.google.Chrome": "~/.var/app/com.google.Chrome/config/google-chrome",
    "chromium-browser": "~/.config/chromium", "chromium": "~/.config/chromium",
    "org.chromium.Chromium": "~/.var/app/org.chromium.Chromium/config/chromium",
    "brave-browser": "~/.config/BraveSoftware/Brave-Browser",
    "com.brave.Browser": "~/.var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser",
    "microsoft-edge": "~/.config/microsoft-edge",
    "vivaldi-stable": "~/.config/vivaldi",
}
TITLE_SUFFIXES = (" - Google Chrome", " - Chromium", " - Brave", " - Microsoft Edge", " - Vivaldi",
                  " — Mozilla Firefox", " - Mozilla Firefox")


# ---- Firefox ---------------------------------------------------------------------
def lz4_block(data, size):
    """Decompress one LZ4 block (the format inside Firefox's .jsonlz4 files)."""
    out = bytearray()
    i, n = 0, len(data)
    while i < n:
        token = data[i]
        i += 1
        lit = token >> 4
        if lit == 15:
            while True:
                b = data[i]
                i += 1
                lit += b
                if b != 255:
                    break
        out += data[i:i + lit]
        i += lit
        if i >= n:
            break
        off = data[i] | data[i + 1] << 8
        i += 2
        ml = token & 15
        if ml == 15:
            while True:
                b = data[i]
                i += 1
                ml += b
                if b != 255:
                    break
        ml += 4
        start = len(out) - off
        for k in range(ml):               # may overlap itself, so byte by byte
            out.append(out[start + k])
    if len(out) != size:
        raise ValueError("LZ4 data damaged")
    return bytes(out)


def read_mozlz4(path):
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:8] != b"mozLz40\0":
        raise ValueError("not a mozlz4 file")
    return json.loads(lz4_block(raw[12:], struct.unpack("<I", raw[8:12])[0]))


def firefox_windows(profile):
    """[{"title": front tab's title, "urls": [...], "active": index of the front
    tab in urls (0 if it is not a web page)}] from a Firefox profile folder."""
    for name in ("sessionstore-backups/recovery.jsonlz4", "sessionstore.jsonlz4"):
        path = os.path.join(profile, name)
        if os.path.exists(path):
            break
    else:
        return []
    out = []
    for w in read_mozlz4(path).get("windows", []):
        tabs = w.get("tabs", [])
        urls, titles, kept = [], [], []      # kept: index in urls of each tab, or None
        for t in tabs:
            entries = t.get("entries", [])
            if not entries:
                continue
            e = entries[max(0, min(t.get("index", len(entries)), len(entries)) - 1)]
            if e.get("url", "").startswith(("http", "file")):
                kept.append(len(urls))
                urls.append(e["url"])
            else:
                kept.append(None)
            titles.append(e.get("title", ""))
        sel = w.get("selected", 1) - 1
        front = 0 <= sel < len(titles)
        out.append({"title": titles[sel] if front else "", "urls": urls,
                    "active": (kept[sel] or 0) if front else 0})
    return out


# ---- Chrome ----------------------------------------------------------------------
def _pickle_string(buf, pos):
    n = struct.unpack_from("<i", buf, pos)[0]
    pos += 4
    s = buf[pos:pos + n].decode("utf-8", "replace")
    return s, pos + ((n + 3) & ~3)


def _pickle_string16(buf, pos):
    n = struct.unpack_from("<i", buf, pos)[0]
    pos += 4
    s = buf[pos:pos + 2 * n].decode("utf-16-le", "replace")
    return s, pos + ((2 * n + 3) & ~3)


def chrome_commands(path):
    """(command id, payload) of an SNSS session file."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:4] != b"SNSS":
        raise ValueError("not a Chrome session file")
    if struct.unpack_from("<i", raw, 4)[0] not in (1, 3):
        raise ValueError("Chrome session file version not supported (encrypted?)")
    pos = 8
    while pos + 2 <= len(raw):
        size = struct.unpack_from("<H", raw, pos)[0]
        pos += 2
        if size == 0 or pos + size > len(raw):
            break
        yield raw[pos], raw[pos + 1:pos + size]
        pos += size


def chrome_windows(user_dir):
    """[{"title": active tab title, "urls": [...]}] from the newest session of a
    Chrome-style user folder (all its profiles)."""
    files = sorted(glob.glob(os.path.join(user_dir, "*", "Sessions", "Session_*")), key=os.path.getmtime)
    out = []
    for path in files[-6:]:              # newest per profile is enough
        prof = os.path.dirname(os.path.dirname(path))
        newest = max(glob.glob(os.path.join(prof, "Sessions", "Session_*")), key=os.path.getmtime)
        if path != newest:
            continue
        try:
            out += _replay(chrome_commands(path))
        except (OSError, ValueError, struct.error):
            continue
    return out


def _replay(commands):
    tab_window, tab_index, nav, nav_sel, win_sel = {}, {}, {}, {}, {}
    closed_tabs, closed_wins = set(), set()
    for cid, p in commands:
        try:
            if cid == 0:                                   # SetTabWindow
                w, t = struct.unpack_from("<ii", p)
                tab_window[t] = w
            elif cid == 2:                                 # SetTabIndexInWindow
                t, i = struct.unpack_from("<ii", p)
                tab_index[t] = i
            elif cid == 6:                                 # UpdateTabNavigation (pickle)
                pos = 4
                t, i = struct.unpack_from("<ii", p, pos)
                url, pos = _pickle_string(p, pos + 8)
                title, pos = _pickle_string16(p, pos)
                nav.setdefault(t, {})[i] = (url, title)
            elif cid == 7:                                 # SetSelectedNavigationIndex
                t, i = struct.unpack_from("<ii", p)
                nav_sel[t] = i
            elif cid == 8:                                 # SetSelectedTabInIndex
                w, i = struct.unpack_from("<ii", p)
                win_sel[w] = i
            elif cid == 5:                                 # NavigationPathPrunedFromBack
                t, count = struct.unpack_from("<ii", p)
                nav[t] = {k: v for k, v in nav.get(t, {}).items() if k < count}
            elif cid == 11:                                # NavigationPathPrunedFromFront
                t, count = struct.unpack_from("<ii", p)
                nav[t] = {k - count: v for k, v in nav.get(t, {}).items() if k >= count}
                if t in nav_sel:
                    nav_sel[t] = max(0, nav_sel[t] - count)
            elif cid == 24:                                # TabNavigationPathPruned
                t, i, count = struct.unpack_from("<iii", p)
                nav[t] = {(k if k < i else k - count): v for k, v in nav.get(t, {}).items()
                          if not i <= k < i + count}
            elif cid == 16:                                # TabClosed
                closed_tabs.add(struct.unpack_from("<i", p)[0])
            elif cid == 17:                                # WindowClosed
                closed_wins.add(struct.unpack_from("<i", p)[0])
        except struct.error:
            continue
    windows = {}
    for t, w in tab_window.items():
        if t in closed_tabs or w in closed_wins or t not in nav:
            continue
        sel = nav_sel.get(t, max(nav[t]))
        url, title = nav[t].get(sel) or nav[t][max(nav[t])]
        windows.setdefault(w, []).append((tab_index.get(t, 0), url, title))
    out = []
    for w, tabs in windows.items():
        tabs.sort()
        sel = win_sel.get(w, 0)
        web = [i for i, (_, u, _) in enumerate(tabs) if u.startswith(("http", "file"))]
        out.append({"title": tabs[sel][2] if 0 <= sel < len(tabs) else "",
                    "urls": [tabs[i][1] for i in web],
                    # the front tab, as an index into urls (0 if it is not a web page)
                    "active": web.index(sel) if sel in web else 0})
    return out


# ---- matching ----------------------------------------------------------------------
def window_title(caption):
    for s in TITLE_SUFFIXES:
        if caption.endswith(s):
            return caption[:-len(s)]
    return caption


def tabs_of(app, captions, firefox_profiles=()):
    """For each window caption of a browser, {"urls": [...], "active": index of
    the front tab} (or None if not found)."""
    try:
        if app in CHROME_DIRS:
            found = chrome_windows(os.path.expanduser(CHROME_DIRS[app]))
        else:
            found = [w for p in firefox_profiles for w in firefox_windows(p)]
    except (OSError, ValueError):
        return [None] * len(captions)
    out = []
    for c in captions:
        title = window_title(c)
        hit = next((w for w in found if w["title"] and w["title"] == title), None)
        if hit is None:                  # titles can be cut short; try a prefix
            hit = next((w for w in found if w["title"] and title.startswith(w["title"][:40])), None)
        out.append({"urls": hit["urls"], "active": hit.get("active", 0)} if hit else None)
        if hit:
            found.remove(hit)
    return out
