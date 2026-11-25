"""plasma-reopen-windows: "Save & Shut Down" / "Save & Restart" for KDE Plasma 6.

On a normal log-out, Plasma closes the windows of every app one by one. Apps
that restore their own windows (Chrome, VS Code, Dolphin) then only remember
the window closed last, and on Wayland nothing puts the windows back where
they were. This tool does it the other way round:

  save      record every window (app, screen, position, size, desktop,
            minimized/maximized), the folders of each Konsole tab, then quit
            each app *as a whole* so it saves all its windows, and write
            ~/.local/state/plasma-reopen-windows/session.json
  shutdown  save, then shut down through Plasma
  reboot    save, then restart through Plasma
  update    download the system updates (PackageKit), save, then restart; the
            updates are installed during the restart (offline update)
  restore   start the saved apps again and put their windows back
  login     at login (autostart): restore, ask or load a profile (setting on_login)
  profile   named arrangements of apps and windows: profile list / save / load /
            unite / reload / arrange / show / edit / rename / delete / export /
            import (see: plasma-reopen-windows profile help)
  autosave  the open windows every while (the timer runs this); autosave now,
            autosave list, autosave restore [1|2|ask], autosave delete
  hibernate before [S] / after   minimize the shown windows (frees some graphics
            memory, waits at least S s), show them again after waking (setting
            hibernate_minimize; hibernate-prepare runs these when the image
            would not fit otherwise)
  status    show what is saved, change nothing
  windows   list the open windows as this tool sees them
  config    show the settings; config set NAME VALUE, config reset NAME,
            config template (the explained settings file), config upgrade

Nothing is ever killed: an app that does not close by itself (for example
because it asks to save a document) stops the shutdown, and the apps closed
so far are started again."""

import json
import os
import signal
import subprocess
import sys
import time
from .apps import ignored, power
from .config import CFG, CONF, SETTINGS, STATE, conf_set, conf_template, conf_upgrade, log, notify
from .kwin import KWin
from .session import do_restore, do_save


# ---- config command ----------------------------------------------------------
def do_config(args):
    sub = args[0] if args else "show"
    if sub == "show":
        for st in SETTINGS:
            v = CFG[st["key"]]
            mark = "" if v == st["default"] else "   (default: %s)" % (st["default"] or "empty")
            print("%-17s = %-12s %s%s" % (st["key"], v or "(empty)", st["label"], mark))
        print("\nfile: %s" % CONF)
    elif sub in ("set", "reset") and len(args) >= 2:
        try:
            v = conf_set(CONF, args[1], " ".join(args[2:]), reset=(sub == "reset"))
        except ValueError as e:
            sys.exit("error: %s" % e)
        print("%s = %s" % (args[1], v or "(empty)"))
    elif sub == "template":
        sys.stdout.write(conf_template())
    elif sub == "upgrade":
        print("updated %s (your values kept; previous file: %s.old)" % (CONF, CONF)
              if conf_upgrade(CONF) else "%s is up to date" % CONF)
    elif sub == "markdown":           # settings table for the README
        print("| Setting | Default | Meaning |\n|---|---|---|")
        for st in SETTINGS:
            print("| `%s` | %s | %s. %s |" % (st["key"], st["default"] or "empty", st["label"], st["help"]))
    else:
        sys.exit("usage: plasma-reopen-windows config [show | set NAME VALUE | reset NAME | template | upgrade]")


# ---- profile command ---------------------------------------------------------
PROFILE_USAGE = """usage: plasma-reopen-windows profile COMMAND
  list                     the saved profiles
  save NAME [--force]      save what is open now as profile NAME (closes nothing);
                           asks before replacing an existing one (--force: don't ask)
  load NAME                unite or reload, as the setting profile_load says
  unite NAME               start the profile's missing apps and windows, arrange all windows
  reload NAME              close the apps that are not in the profile, then unite
  arrange NAME             only move the open windows into place
  undo                     undo the last unite, reload, arrange or saved edit (once)
  plan NAME [--reload|--arrange]
                           what loading would do, as JSON (changes nothing)
  (load, unite, reload, arrange: a saved window not clearly found is asked
   about, see the setting when_unsure; --choices FILE gives the answers)
  show NAME                apps and windows of a profile
  edit NAME                open the profile file in your text editor
  rename OLD NEW           (quote names with spaces)
  delete NAME
  clean                    delete automatic profiles made by older versions
  export NAME [FILE] [--with-sessions]
                           copy to a file (default: current folder); with the
                           browsers' sessions (all tabs with their history,
                           private) as a .zip
  import FILE [--overwrite]   a .json or such a .zip"""


def do_autosave(args):
    from . import autosave
    sub = args[0] if args else "timer"
    if sub == "timer":                   # the systemd timer: quiet, only when due
        try:
            autosave.take()
        except Exception as e:           # the desktop may not be ready yet at login
            log("autosave skipped: %s" % e)
    elif sub == "now":
        p = autosave.take(manual=True) or autosave.read(0)
        if p:
            notify("Windows saved", autosave.describe(p))
    elif sub == "list":
        saved = [{"slot": i + 1, "saved": p.get("saved"), "text": autosave.describe(p)}
                 for i, p in autosave.all_saved()]
        print(json.dumps(saved) if "--json" in args else "\n".join("%d  %s" % (s["slot"], s["text"])
                                                                   for s in saved) or "no autosave")
    elif sub == "restore":
        which = args[1] if len(args) > 1 else "1"
        if which == "ask":               # menu entry / shortcut: choose when there are two
            which = ask_slot()
            if which is None:
                return
        if autosave.restore(int(which) - 1) is False:
            sys.exit("cancelled")
    elif sub == "delete":
        autosave.delete()
    else:
        sys.exit("usage: plasma-reopen-windows autosave [now | list | restore [1|2|ask] | delete]")


def ask_slot():
    """Which autosave to restore: the only one, or chosen in a small menu."""
    from . import autosave
    saved = autosave.all_saved()
    if not saved:
        notify("Restore last autosave", "There is no autosave yet.")
        return None
    if len(saved) == 1:
        return "1"
    menu = ["kdialog", "--title", "Reopen windows", "--menu", "Restore the windows of which autosave?"]
    for i, p in saved:
        menu += [str(i + 1), ("Newest: " if i == 0 else "Earlier: ") + autosave.describe(p)]
    r = subprocess.run(menu, capture_output=True, text=True, check=False)
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def front(show=False, above=None):
    """Started from the settings app: put it on top again (show: also when
    minimized, for a second start of the app; above: keep it above other
    windows or not)."""
    if os.environ.get("PRW_FROM_APP") or show:
        from .kwin import JS_FRONT
        KWin().run(JS_FRONT % (json.dumps(show), json.dumps(above), json.dumps("plasma-reopen-windows-settings")))


def do_profile(args):
    from . import profiles
    sub, rest = (args[0], args[1:]) if args else ("list", [])
    name = " ".join(rest)
    try:
        if sub == "list":
            ps = profiles.all_profiles()
            for p in ps:
                print("%-24s %2d apps %3d windows  updated %s%s" % (
                    p["name"], len(p["apps"]), len(p["windows"]), p.get("updated", "?"),
                    "  (automatic)" if p.get("auto") else ""))
            if not ps:
                print("no profiles yet: plasma-reopen-windows profile save NAME")
        elif sub == "save" and name:
            force = "--force" in rest
            name = " ".join(r for r in rest if r != "--force")
            old = profiles.find(name)
            if old and not force:
                info = "Profile \"%s\" already exists (saved %s, %d apps, %d windows)." % (
                    old["name"], old.get("updated", "?").replace("T", " "), len(old["apps"]), len(old["windows"]))
                if not sys.stdin.isatty():
                    sys.exit("error: %s Use --force to replace it." % info)
                answer = input("%s\nReplace it with what is open now? Your edits (apps switched off, "
                               "start delays) are kept. [y/N] " % info)
                if answer.strip().lower() not in ("y", "yes"):
                    print("not saved")
                    return
            profiles.save(name)
        elif sub == "plan" and name:
            mode = "unite"
            for m in ("--reload", "--arrange"):
                if m in rest:
                    mode = m[2:]
            if "--from" in rest:             # an edited profile that is not saved yet
                with open(rest[rest.index("--from") + 1]) as f:
                    p = profiles.migrate(json.load(f))
            else:
                p = profiles.need(" ".join(r for r in rest if not r.startswith("--")))
            print(json.dumps(profiles.plan(p, mode)))
        elif sub in ("load", "unite", "reload", "switch", "arrange") and name:
            choices = None
            if "--choices" in rest:          # answers from the settings app's question window
                k = rest.index("--choices")
                with open(rest[k + 1]) as f:
                    choices = json.load(f)
                rest = rest[:k] + rest[k + 2:]
                name = " ".join(rest)
            keep_undo = "--no-undo" not in rest        # the settings app's Discard: keep the undo point
            name = " ".join(r for r in rest if r != "--no-undo")
            if rest[:1] == ["--autosave"]:              # an autosave (1 = newest), as shown in the app
                from . import autosave
                snap = autosave.read(int(rest[1]) - 1)
                if snap is None:
                    sys.exit("error: no such autosave")
                p = profiles.migrate(snap)
                title = "%s autosave (%s)" % (profiles.TITLES.get(sub, "Load"), autosave.describe(p))
                # never written back: Unite here only brings windows back
                if sub in ("reload", "switch"):
                    done = profiles.reload(p, choices=choices, keep_undo=keep_undo, title=title)
                else:
                    done = profiles.apply(p, "arrange" if sub == "arrange" else "unite", choices, title=title)
                if done is False:
                    sys.exit("cancelled")
                return
            p = profiles.need(name)
            if sub in ("reload", "switch"):
                done = profiles.reload(p, choices=choices, keep_undo=keep_undo)
            else:
                done = {"load": profiles.load, "unite": profiles.unite,
                        "arrange": profiles.arrange}[sub](p, choices=choices)
            if done is False:
                sys.exit("cancelled")
        elif sub == "show" and name:
            p = profiles.need(name)
            print("%s  (updated %s)" % (p["name"], p.get("updated")))
            for a in p["apps"]:
                print("  app    %-28s %s%s" % (a["app"][:28], "on " if a["enabled"] else "OFF",
                                              "  delay %ss" % a["start_delay"] if a.get("start_delay") else ""))
            for w in p["windows"]:
                scr = (w.get("screen") or {}).get("model") or w.get("output", "")
                print("  window %-28s %-14s %s%s" % (w["app"][:28], scr[:14], w["caption"][:45],
                                                     "  + %d pages" % len(w["urls"]) if w.get("urls") else ""))
        elif sub == "edit" and name:
            profiles.need(name)
            subprocess.Popen(["xdg-open", profiles.path_for(name)])
        elif sub == "rename" and len(rest) == 2:
            profiles.rename(rest[0], rest[1])
            print("renamed")
        elif sub == "save-edit" and "--from" in rest:
            # from the settings app: the edited profile, saved and then reloaded
            k = rest.index("--from")
            with open(rest[k + 1]) as f:
                p = profiles.migrate(json.load(f))
            choices = None
            if "--choices" in rest:
                with open(rest[rest.index("--choices") + 1]) as f:
                    choices = json.load(f)
            done = profiles.save_edit(p, choices)
            if done is False:
                sys.exit("saved; reloading it was cancelled")
        elif sub == "undo":
            profiles.undo()
        elif sub == "clean":
            gone = profiles.clean()
            print("deleted %s" % (", ".join(gone) if gone else "nothing (no automatic profiles)"))
        elif sub == "delete" and name:
            profiles.delete(name)
            print("deleted %s" % name)
        elif sub == "export" and rest:
            sessions = "--with-sessions" in rest
            rest = [r for r in rest if r != "--with-sessions"]
            print("exported to %s" % profiles.export(rest[0], rest[1] if len(rest) > 1 else os.getcwd(),
                                                     sessions=sessions))
        elif sub == "import" and rest:
            p = profiles.import_file(rest[0], overwrite="--overwrite" in rest)
            print("imported %s" % p["name"])
        else:
            sys.exit(PROFILE_USAGE)
    except ValueError as e:
        sys.exit("error: %s" % e)


# ---- main --------------------------------------------------------------------
def stopped(signum, _frame):
    """Stopped from outside: say so in the log, then stop."""
    log("stopped by %s" % signal.Signals(signum).name)
    sys.exit(128 + signum)


def main():
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        signal.signal(sig, stopped)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    # Started from the settings app for something that opens windows: keep the
    # app above them meanwhile, and in front at the end (also after an error).
    # Not for update: its download takes minutes while you keep working.
    acting = bool(os.environ.get("PRW_FROM_APP")) and cmd in ("profile", "restore", "save", "autosave")
    # commands that make windows appear or close: no autosave meanwhile
    loading = cmd in ("shutdown", "reboot", "update", "save", "restore", "login") or (
        cmd == "profile" and len(sys.argv) > 2 and sys.argv[2] in (
            "load", "unite", "reload", "switch", "arrange", "undo", "save-edit")) or (
        cmd == "autosave" and len(sys.argv) > 2 and sys.argv[2] == "restore")
    if loading:
        from .autosave import mark_busy
        mark_busy()
    if acting:
        try:
            front(above=True)
        except Exception as e:
            log("could not keep the settings app above: %s" % e)
    try:
        if cmd in ("shutdown", "reboot"):
            if do_save() is not None:
                power(cmd)
        elif cmd == "update":
            from .updates import do_update
            do_update()
        elif cmd == "save":
            # Testing: save (optionally only some apps: save chrome konsole ...), no power-off
            only = set(sys.argv[2:]) or None
            if only:
                only = {{"chrome": "google-chrome", "vscode": "code"}.get(o, o) for o in only}
            do_save(only)
        elif cmd == "restore":
            do_restore()
        elif cmd == "login":
            from .login import login
            login()
        elif cmd == "profile":
            do_profile(sys.argv[2:])
        elif cmd == "status":
            if os.path.exists(STATE):
                with open(STATE) as f:
                    s = json.load(f)
                print("saved %s" % time.strftime("%F %T", time.localtime(s["saved"])))
                for l in s["launch"]:
                    print("  start  %s %s" % (l["app"], l.get("konsole", "")))
                for w in s["windows"]:
                    print("  window %-28s %-9s %5d,%-5d %4dx%-4d %s" % (
                        w["app"][:28], w["output"], w["x"], w["y"], w["width"], w["height"], w["caption"][:50]))
            else:
                print("nothing saved")
        elif cmd == "autosave":
            do_autosave(sys.argv[2:])
        elif cmd == "hibernate" and sys.argv[2:3] in (["before"], ["after"]):
            from . import hibernate
            if sys.argv[2] == "before":
                hibernate.before(int(sys.argv[3]) if sys.argv[3:4] and sys.argv[3].isdigit() else None)
            else:
                hibernate.after()
        elif cmd == "front":                 # used by the settings app: show its window
            front(show=True)
        elif cmd == "config":
            do_config(sys.argv[2:])
        elif cmd == "windows" and "--json" in sys.argv:
            from .apps import app_name, find_desktop
            seen = {}
            for w in KWin().windows()["windows"]:
                seen.setdefault(w["app"], app_name(find_desktop(w["app"], w) or w["app"]))
            print(json.dumps([{"id": k, "name": v, "ignored": ignored(k)} for k, v in sorted(seen.items())]))
        elif cmd == "windows":
            for w in KWin().windows()["windows"]:
                print("%-30s pid %-7d %-9s %5d,%-5d %4dx%-4d %s%s" % (
                    w["app"][:30], w["pid"], w["output"], w["x"], w["y"], w["width"], w["height"],
                    "[ignored] " if ignored(w["app"]) else "", w["caption"][:50]))
        else:
            print(__doc__)
            sys.exit(2)
    except Exception as e:
        log("ERROR: %s" % e)
        notify("Reopen windows: error", str(e), critical=True)
        raise
    finally:
        if loading:
            from .autosave import clear_busy
            clear_busy()
        if acting:
            try:
                front(above=False)
            except Exception as e:
                log("could not bring the settings app back to the front: %s" % e)
