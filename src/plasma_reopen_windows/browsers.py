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
import urllib.parse
from gi.repository import GLib

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
FIREFOX_APPS = {"org.mozilla.firefox", "firefox", "firefox-esr", "org.mozilla.firefox_esr"}
BROWSER_APPS = set(CHROME_DIRS) | {"com.microsoft.Edge"} | FIREFOX_APPS
# Plasma Browser Integration's tab search (the KRunner "browser tabs" plugin):
# the tabs open now, with their titles and addresses, and it brings one to the front
TABS_RUNNER = ("org.kde.plasma.browser_integration", "/TabsRunner", "org.kde.krunner1")
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


def session_windows(app, firefox_profiles=()):
    """The windows in a browser's session file: [{"title", "urls", "active"}]."""
    try:
        if app in CHROME_DIRS:
            return chrome_windows(os.path.expanduser(CHROME_DIRS[app]))
        return [w for p in firefox_profiles for w in firefox_windows(p)]
    except (OSError, ValueError):
        return []


def match_titles(captions, found):
    """For each caption the session window with that front tab title (or None);
    the matched ones are taken out of found."""
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


def tabs_of(app, captions, firefox_profiles=()):
    """For each window caption of a browser, {"urls": [...], "active": index of
    the front tab} (or None if not found)."""
    return match_titles(captions, session_windows(app, firefox_profiles))

# ---- the browser running now (Plasma Browser Integration) -----------------------
def live_tabs(bus, title):
    """Tabs open now whose title is exactly this: [(tab id, address)]. Empty
    without Plasma Browser Integration. Its search finds titles and addresses
    (2 characters at least); the exact title is picked from what it finds."""
    if bus is None or len(title) < 2:
        return []
    try:
        found = bus.call_sync(TABS_RUNNER[0], TABS_RUNNER[1], TABS_RUNNER[2], "Match",
                              GLib.Variant("(s)", (title,)), None, 0, 3000).unpack()[0]
    except GLib.Error:
        return []
    return [(m[0], (m[5].get("urls") or [m[5].get("subtext", "")])[0]) for m in found if m[1] == title]


def bring_tab_to_front(bus, title):
    """Make the tab with this title the front tab of its window (and that
    window active). False if no such tab is open."""
    tabs = live_tabs(bus, title)
    if not tabs:
        return False
    try:
        bus.call_sync(TABS_RUNNER[0], TABS_RUNNER[1], TABS_RUNNER[2], "Run",
                      GLib.Variant("(ss)", (tabs[0][0], "")), None, 0, 3000)
        return True
    except GLib.Error:
        return False


def page(url):
    """An address without what changes while a page is used (#part)."""
    return urllib.parse.urldefrag(url)[0].rstrip("/")


def tab_overlap(saved, found):
    """How much of a saved window's pages a window has now (0..1)."""
    saved = {page(u) for u in saved if u}
    return len(saved & {page(u) for u in found}) / len(saved) if saved else 0.0


def window_tabs(bus, apps, firefox_profiles=None):
    """{window id: {"urls", "active"}} for the browser windows in apps
    ({app: [windows]}). From the browser's session file; a window not in it
    (the browser writes it with a delay: measured 2026-11-25, a window opened
    in the last half minute was missing) gets at least its front page, which
    the browser integration finds by the window title."""
    out = {}
    for app, ws in apps.items():
        if app not in BROWSER_APPS:
            continue
        found = session_windows(app, (firefox_profiles or {}).get(app, []))
        for w, t in zip(ws, match_titles([w["caption"] for w in ws], found)):
            if t and t["urls"]:
                out[w["id"]] = t
                continue
            live = [u for _, u in live_tabs(bus, window_title(w["caption"])) if u.startswith(("http", "file"))]
            if not live:
                continue
            # the session file has the window, but with an older front tab
            hit = next((f for f in found if page(live[0]) in [page(u) for u in f["urls"]]), None)
            if hit:
                found.remove(hit)
                out[w["id"]] = {"urls": hit["urls"], "active": [page(u) for u in hit["urls"]].index(page(live[0]))}
            else:
                out[w["id"]] = {"urls": live[:1], "active": 0}     # only its front page is known
    return out
