"""plasma-reopen-windows: the settings app (needs PySide6).

The app never talks to KWin itself: everything that moves, starts or closes
windows runs as the command-line tool in its own process. The app reads and
edits the settings file and the profile files, and handles "Where to show it".
"""

import json
import os
import platform
import shutil
import sys
import time
import zlib
from PySide6.QtCore import QEvent, QObject, QProcess, QProcessEnvironment, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtGui import QBrush, QColor, QPalette, QCursor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractButton, QProgressBar, QToolButton, QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QFrame,
                               QDialogButtonBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QInputDialog, QKeySequenceEdit, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QSpinBox, QStyle, QStyleOptionButton, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)
from . import config, placement, profiles
from .screens import target_geometry

APP_ID = "plasma-reopen-windows-settings"
SOCKET = "plasma-reopen-windows-settings-%d" % os.getuid()     # one copy of the app per user
UI_STATE = os.path.join(config.STATE_DIR, "settings-app.json")    # page, profile and size when closed


def read_ui_state():
    try:
        with open(UI_STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def cli_path():
    here = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plasma-reopen-windows")
    return shutil.which("plasma-reopen-windows") or (here if os.path.exists(here) else "plasma-reopen-windows")


def hint(text):
    label = QLabel(text)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


class RowHover(QObject):
    """Follows the cursor over a table (also over the checkboxes and number
    boxes in its rows) and tells the page which row it is on (-1 = none)."""
    def __init__(self, page, table):
        super().__init__(page)
        self.page, self.table = page, table
        table.viewport().setMouseTracking(True)
        table.viewport().installEventFilter(self)

    def watch(self, widget):
        """Also follow the cursor over a widget placed in a cell."""
        for w in [widget] + widget.findChildren(QWidget):
            w.setMouseTracking(True)
            w.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.MouseMove, QEvent.Enter, QEvent.Leave, QEvent.HoverMove):
            view = self.table.viewport()
            pos = view.mapFromGlobal(QCursor.pos())
            row = self.table.rowAt(pos.y()) if view.rect().contains(pos) else -1
            self.page.hover_row(self.table, row)
        return False


HUES = {}                            # app id -> hue, the same in the drawing and the tables


def set_hues(apps):
    """Spread the colors evenly over the apps of a profile, so no two look alike."""
    HUES.clear()
    apps = sorted(set(apps))
    for i, app in enumerate(apps):
        HUES[app] = (i * 360 // max(1, len(apps)) + 15) % 360


def app_hue(app):
    return HUES.get(app, zlib.crc32(app.encode()) % 360)


def swatch(app):
    """A small square in the app's color in the drawing."""
    pix = QPixmap(12, 12)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setPen(QPen(QColor.fromHsv(app_hue(app), 170, 130), 1))
    p.setBrush(QColor.fromHsv(app_hue(app), 110, 225))
    p.drawRect(0, 0, 11, 11)
    p.end()
    return QIcon(pix)


class CheckHeader(QHeaderView):
    """Column titles where the first one is a checkbox ("all")."""
    clicked_box = Signal()

    def __init__(self, parent):
        super().__init__(Qt.Horizontal, parent)
        self.state = Qt.Checked

    def set_state(self, state):
        self.state = state
        self.viewport().update()

    def paintSection(self, painter, rect, index):
        painter.save()
        super().paintSection(painter, rect, index)
        painter.restore()
        if index == 0:
            opt = QStyleOptionButton()
            size = self.style().pixelMetric(QStyle.PM_IndicatorWidth)
            opt.rect = QRect(rect.center().x() - size // 2, rect.center().y() - size // 2, size, size)
            opt.state = QStyle.State_Enabled | {Qt.Checked: QStyle.State_On, Qt.Unchecked: QStyle.State_Off}.get(
                self.state, QStyle.State_NoChange)
            self.style().drawPrimitive(QStyle.PE_IndicatorCheckBox, opt, painter)

    def mousePressEvent(self, event):
        if self.logicalIndexAt(event.position().toPoint()) == 0:
            self.clicked_box.emit()


def button(label, slot, tip):
    b = QPushButton(label)
    b.setToolTip(tip)
    b.clicked.connect(slot)
    return b


def quiet(table):
    """A table whose only clickable parts are its own controls: no selecting,
    no editing, no clickable column titles."""
    table.setSelectionMode(QAbstractItemView.NoSelection)
    table.setEditTriggers(QAbstractItemView.NoEditTriggers)
    table.setFocusPolicy(Qt.NoFocus)
    table.horizontalHeader().setSectionsClickable(False)
    table.horizontalHeader().setHighlightSections(False)


def centered(widget):
    """A table cell with the widget in its middle."""
    cell = QWidget()
    lay = QHBoxLayout(cell)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(widget, 0, Qt.AlignCenter)
    return cell


def read_session():
    for path, what in ((config.STATE, "saved, comes back at the next login"),
                       (config.STATE + ".restored", "last restored")):
        if os.path.exists(path):
            try:
                with open(path) as f:
                    return json.load(f), what
            except (OSError, ValueError):
                pass
    return None, None


# ---- a static drawing of the screens with the windows ------------------------------
class Preview(QWidget):
    def __init__(self):
        super().__init__()
        self.focus = None            # highlighted: ("app", id) or ("window", index)
        self.setMinimumHeight(260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.data = None
        self.hidden = set()          # apps switched off: not drawn

    def show_layout(self, data, hidden=()):
        self.data = data
        self.hidden = set(hidden)
        self.update()

    def highlight(self, focus):
        if focus != self.focus:
            self.focus = focus
            self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        if not self.data or not self.data.get("screens"):
            p.setPen(pal.placeholderText().color())
            p.drawText(self.rect(), Qt.AlignCenter, "No preview")
            return
        screens = self.data["screens"]
        x0 = min(s["x"] for s in screens)
        y0 = min(s["y"] for s in screens)
        x1 = max(s["x"] + s["width"] for s in screens)
        y1 = max(s["y"] + s["height"] for s in screens)
        scale = min((self.width() - 12) / max(1, x1 - x0), (self.height() - 12) / max(1, y1 - y0))
        dx = (self.width() - (x1 - x0) * scale) / 2         # centred
        dy = (self.height() - (y1 - y0) * scale) / 2

        def rect(x, y, w, h):
            return QRectF(dx + (x - x0) * scale, dy + (y - y0) * scale, w * scale, h * scale)
        small = QFont(self.font().family(), 8)
        p.setFont(small)
        for s in screens:
            r = rect(s["x"], s["y"], s["width"], s["height"])
            p.setPen(QPen(pal.mid().color(), 1))
            p.setBrush(pal.base().color())
            p.drawRoundedRect(r, 3, 3)
            p.setPen(pal.placeholderText().color())
            p.drawText(r.adjusted(4, 2, -4, -2), Qt.AlignBottom | Qt.AlignRight, s.get("model") or s.get("name", ""))
        shown = [(w, target_geometry(w, screens)) for w in self.data.get("windows", [])
                 if not w.get("minimized") and w["app"] not in self.hidden]
        shown = [(w, g) for w, g in shown if g]
        # layer number per screen: 1 = on top
        layer = {}
        for s in screens:
            on = [w for w, g in shown if s["x"] <= g["x"] + g["width"] / 2 < s["x"] + s["width"]
                  and s["y"] <= g["y"] + g["height"] / 2 < s["y"] + s["height"]]
            for n, w in enumerate(sorted(on, key=lambda w: -w.get("stack", 0))):
                layer[id(w)] = n + 1
        boxes = []
        for w, g in sorted(shown, key=lambda wg: wg[0].get("stack", 0)):
            hue = app_hue(w["app"])
            r = rect(g["x"], g["y"], g["width"], g["height"])
            p.setPen(QPen(QColor.fromHsv(hue, 170, 130), 1.5))
            p.setBrush(QColor.fromHsv(hue, 90, 235, 150))          # see-through: lower windows show
            p.drawRect(r)
            p.setPen(QColor(25, 25, 25))
            p.drawText(r.adjusted(4, 2, -4, -2), Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap,
                       w["app"].split(".")[-1])
            boxes.append((w, r, hue))
        # the highlighted window(s): drawn again on top, opaque, thick border
        windows = self.data.get("windows", [])
        lit = [w for w, r, hue in boxes
               if self.focus in (("app", w["app"]), ("window", next(i for i, x in enumerate(windows) if x is w)))]
        for w, r, hue in boxes:
            if w in lit:
                p.setPen(QPen(QColor.fromHsv(hue, 230, 90), 3))
                p.setBrush(QColor.fromHsv(hue, 110, 245))
                p.drawRect(r)
                p.setPen(QColor(15, 15, 15))
                p.drawText(r.adjusted(4, 2, -4, -2), Qt.AlignTop | Qt.AlignLeft | Qt.TextWordWrap,
                           w["app"].split(".")[-1] + "\n" + w["caption"])
        # numbers last, so no window covers them, each on a free spot of its window
        taken = []
        d = 16
        for w, r, hue in boxes:
            if id(w) not in layer or (self.focus and w not in lit):     # only the highlighted ones' numbers
                continue
            spots = [QRectF(r.right() - d - 2, r.top() + 2, d, d), QRectF(r.left() + 2, r.bottom() - d - 2, d, d),
                     QRectF(r.right() - d - 2, r.bottom() - d - 2, d, d), QRectF(r.center().x() - d / 2,
                     r.center().y() - d / 2, d, d), QRectF(r.left() + 2, r.top() + 16, d, d)]
            spot = next((sp for sp in spots if not any(sp.intersects(t) for t in taken)), spots[0])
            taken.append(spot)
            p.setPen(QPen(QColor(255, 255, 255), 1.5))
            p.setBrush(QColor.fromHsv(hue, 210, 110))
            p.drawEllipse(spot)
            p.setFont(QFont(self.font().family(), 8, QFont.Bold))
            p.drawText(spot, Qt.AlignCenter, str(layer[id(w)]))
            p.setFont(small)


# ---- the question about saved windows that are not clearly found ------------------
def choose(parent, pl, profile=None):
    """{saved index: "open" | "use:ID" | "skip"}, or None if cancelled."""
    d = QDialog(parent)
    d.setWindowTitle("Reopen Windows: %s" % pl["profile"])
    v = QVBoxLayout(d)
    v.addWidget(hint("These saved windows were not found by their title, but their app has other "
                     "windows open (for example a browser window with another tab in front). "
                     "Choose what to do with each."))
    names = {a["app"]: a.get("name") or a["app"] for a in (profile or {}).get("apps", [])}
    boxes = {}
    for q in pl["questions"]:
        box = QGroupBox()
        bl = QVBoxLayout(box)
        label = QLabel()
        label.setTextFormat(Qt.RichText)
        label.setText("%s <b>%s</b>: %s<br><small>saved on %s%s</small>" % (
            '<span style="color:%s">&#9632;</span>' % QColor.fromHsv(app_hue(q["app"]), 170, 180).name(),
            names.get(q["app"], q["app"]), q["caption"][:90], q["screen"],
            ", " + q["what"] if q.get("what") else ""))
        label.setWordWrap(True)
        bl.addWidget(label)
        combo = QComboBox()
        if q["can_open"]:
            combo.addItem("Open the saved window again", "open")
        for sp in q["spare"]:
            combo.addItem("Use the open window: %s (on %s)" % (sp["caption"][:60], sp["screen"]), "use:" + sp["id"])
        combo.addItem("Leave it out", "skip")
        bl.addWidget(combo)
        v.addWidget(box)
        boxes[str(q["saved"])] = combo
    bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    bb.button(QDialogButtonBox.Ok).setText("Go on")
    bb.accepted.connect(d.accept)
    bb.rejected.connect(d.reject)
    v.addWidget(bb)
    d.resize(760, d.sizeHint().height())
    if d.exec() != QDialog.Accepted:
        return None
    return {k: c.currentData() for k, c in boxes.items()}


# ---- pages ----------------------------------------------------------------------
class Page(QWidget):
    title = ""

    def __init__(self, app):
        super().__init__()
        self.app = app

    def refresh(self):
        pass


class Overview(Page):
    title = "Overview"

    def __init__(self, app):
        super().__init__(app)
        v = QVBoxLayout(self)
        head = QLabel("Reopen Windows")
        f = head.font()
        f.setPointSize(f.pointSize() + 6)
        head.setFont(f)
        v.addWidget(head)
        v.addWidget(hint("Save & Shut Down / Save & Restart close every app so it keeps all its windows, "
                         "and bring them back at the next login, each window in its place."))
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box = QGroupBox("Session")
        QVBoxLayout(box).addWidget(self.info)
        v.addWidget(box)
        row = QHBoxLayout()
        for label, args, ask in (("Save && Shut Down", ["shutdown"], False),
                                 ("Save && Restart", ["reboot"], False),
                                 ("Save without restarting", ["save"], True),
                                 ("Restore now", ["restore"], False)):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, a=args, q=ask: self.run(a, q))
            row.addWidget(b)
        v.addLayout(row)
        v.addWidget(hint("\"Save without restarting\" closes the apps and saves them; \"Restore now\" opens a "
                         "saved session again. Useful to try it out."))
        v.addStretch()

    def run(self, args, ask):
        if ask and QMessageBox.question(self, "Save without restarting",
                                        "Close all apps and save the session?") != QMessageBox.Yes:
            return
        self.app.run_cli(args, detached=args[0] in ("shutdown", "reboot"))

    def refresh(self):
        data, what = read_session()
        if not data:
            self.info.setText("Nothing saved yet.")
            return
        apps = sorted({l["app"].split(".")[-1] for l in data.get("launch", [])})
        self.info.setText("<b>%s</b> (%s)<br>%d apps, %d windows: %s" % (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(data.get("saved", 0))), what,
            len(data.get("launch", [])), len(data.get("windows", [])), ", ".join(apps)))


class Profiles(Page):
    title = "Profiles"

    def __init__(self, app):
        super().__init__(app)
        self.current = None
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel("Profile:"))
        self.picker = QComboBox()
        self.picker.setMinimumWidth(260)
        self.picker.currentIndexChanged.connect(self.picked)
        self.shown = -1                      # picker index of the profile shown
        top.addWidget(self.picker, 1)
        top.addWidget(button("New from screen…", self.new, "Save the apps and windows open now as a new profile."))
        top.addWidget(button("Write over from screen", self.take_over,
                             "Replace this profile with the apps and windows open now. Ticks and start "
                             "delays are kept."))
        v.addLayout(top)

        self.preview = Preview()
        v.addWidget(self.preview, 4)
        v.addWidget(hint("Numbers: layer on each screen, 1 = on top. Switched-off apps are not shown. "
                         "Point at a row below to highlight its windows."))
        tables = QHBoxLayout()
        self.apps = self.table(["", "App", "Start delay (s)", "Remove"], 1, CheckHeader)
        for col, width in ((0, 44), (2, 110), (3, 70)):
            self.apps.horizontalHeader().setSectionResizeMode(col, QHeaderView.Fixed)
            self.apps.setColumnWidth(col, width)
        self.all = self.apps.horizontalHeader()
        self.all.clicked_box.connect(self.tick_all)
        self.apps.horizontalHeaderItem(0).setToolTip("Tick all apps, or untick all when all are ticked")
        tables.addWidget(self.framed("Apps", self.apps), 1)
        self.browsers = self.table(["Window", "Pages"], 0)
        self.browsers.cellDoubleClicked.connect(self.edit_pages)
        tables.addWidget(self.framed("Browser windows (double-click to edit the pages)", self.browsers), 1)
        v.addLayout(tables, 4)
        self.hover = {t: RowHover(self, t) for t in (self.apps, self.browsers)}
        v.addWidget(hint("Changes to apps and pages are kept when the profile is saved again."))
        line = QHBoxLayout()
        for label, sub, tip in (
                ("Unite Screen and Existing", "unite",
                 "Both ways: start the profile's apps and windows that are missing and put all its "
                 "windows in place; windows open now that are not in the profile are added to it, "
                 "where they are. Nothing is closed."),
                ("Reload Existing", "reload",
                 "Close the apps that are not in the profile, then start what is missing and put all "
                 "its windows in place."),
                ("Arrange Existing on Screen", "arrange",
                 "Only put the profile's open windows back in place and order (also minimized ones). "
                 "Nothing is started or closed.")):
            line.addWidget(button(label, lambda _=False, s=sub: self.act(s), tip))
        self.undo_btn = button("Undo", self.undo,
                               "Undo the last thing done: a Unite, Reload or Arrange (apps it started close, "
                               "apps it closed start again, windows go back), or the last saved change to a "
                               "profile (removed apps come back).")
        line.addWidget(self.undo_btn)
        v.addLayout(line)
        line = QHBoxLayout()
        self.save_btn = button("Save changes", lambda: self.save_changes(),
                               "Save the ticks, start delays, edited pages and removed apps, then reload the "
                               "profile onto the screens: switched-off and removed apps close, ticked ones "
                               "start, windows go to their places. Undo last takes it all back.")
        line.addWidget(self.save_btn)
        self.discard_btn = button("Discard changes", lambda: self.discard(),
                                  "Forget the edits that are not saved yet and show the saved profile. If "
                                  "the screens show the unsaved version (after an Undo), the saved one is "
                                  "reloaded onto them.")
        line.addWidget(self.discard_btn)
        for label, slot, tip in (
                ("Use at login", self.use_at_login, "Load this profile every time you log in."),
                ("Rename…", self.rename, "Give the profile another name."),
                ("Import…", self.import_, "Add a profile from an exported file."),
                ("Export…", self.export, "Save the profile to a file, for example to copy it to "
                                         "another computer.")):
            line.addWidget(button(label, slot, tip))
        delete = button("Delete", self.delete, "Delete this profile.")
        delete.setStyleSheet("QPushButton { background: #da4453; color: white; border: 1px solid #b83240;"
                             " border-radius: 3px; padding: 5px 12px; }"
                             "QPushButton:hover { background: #e05a68; }"
                             "QPushButton:disabled { background: #eab3b9; border-color: #e0a0a7; }")
        line.addWidget(delete)
        v.addLayout(line)
        self.dirty(False)

    @staticmethod
    def table(headers, stretch, header=None):
        t = QTableWidget(0, len(headers))
        if header:
            t.setHorizontalHeader(header(t))
        t.setHorizontalHeaderLabels(headers)
        t.horizontalHeader().setSectionResizeMode(stretch, QHeaderView.Stretch)
        t.verticalHeader().hide()
        t.setFrameShape(QFrame.NoFrame)
        t.setShowGrid(False)
        t.setAlternatingRowColors(True)
        quiet(t)
        return t

    @staticmethod
    def framed(title, widget):
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(widget)
        return box

    def refresh(self):
        keep = self.current["name"] if self.current else self.app.ui_state.get("profile")
        found = sorted((p for p in profiles.all_profiles() if not p.get("auto")), key=lambda p: p["name"].lower())
        names = [p["name"] for p in found]
        self.show_undo()
        # nothing changed (the usual case after Arrange, Reload ...): no rebuild, no flicker
        if (not self.save_btn.isEnabled() and self.current and keep in names and
                names == [self.picker.itemData(i) for i in range(self.picker.count())] and
                next(p for p in found if p["name"] == keep) == self.current):
            return
        self.setUpdatesEnabled(False)           # rebuild in one go, without visible steps
        try:
            self.picker.blockSignals(True)
            self.picker.clear()
            for name in names:
                self.picker.addItem(name, name)
                if name == keep:
                    self.picker.setCurrentIndex(self.picker.count() - 1)
            self.picker.blockSignals(False)
            self.select()
        finally:
            self.setUpdatesEnabled(True)

    def hover_row(self, table, row):
        """Highlight in the drawing the window(s) of the table row under the cursor."""
        self.light_row(table, row)
        p = self.current
        if not p or row < 0:
            self.preview.highlight(None)
            return
        if table is self.apps and table.item(row, 1):
            self.preview.highlight(("app", table.item(row, 1).data(Qt.UserRole)))
        elif table is self.browsers and table.item(row, 0):
            self.preview.highlight(("window", table.item(row, 0).data(Qt.UserRole)))

    def light_row(self, table, row):
        """Color the table row under the cursor (selection is off, so it is painted here)."""
        old = table.property("lit_row")
        if old == row:
            return
        color = QColor(self.palette().highlight().color())
        color.setAlpha(70)
        for r, brush in ((old, QBrush()), (row, QBrush(color))):
            if r is None or r < 0 or r >= table.rowCount():
                continue
            for c in range(table.columnCount()):
                item = table.item(r, c)
                if item is None:                 # a cell with a checkbox or number box
                    item = QTableWidgetItem()
                    item.setFlags(Qt.ItemIsEnabled)
                    table.setItem(r, c, item)
                item.setBackground(brush)
        table.setProperty("lit_row", row)

    def app_rows(self):
        """(app id, its tick box, its delay box) per row of the apps table."""
        return [(self.apps.item(r, 1).data(Qt.UserRole), self.apps.cellWidget(r, 0).findChild(QCheckBox),
                 self.apps.cellWidget(r, 2)) for r in range(self.apps.rowCount())]

    def hidden_apps(self):
        return {app for app, box, _ in self.app_rows() if not box.isChecked()} | self.removed

    def grey(self):
        """Names of switched-off apps in grey."""
        for r in range(self.apps.rowCount()):
            on = self.apps.cellWidget(r, 0).findChild(QCheckBox).isChecked()
            self.apps.item(r, 1).setForeground(self.palette().text() if on else self.palette().placeholderText())

    def remove_app(self, app):
        """Take an app (and its saved windows) out of the profile; saved with Save changes."""
        for r in range(self.apps.rowCount()):
            if self.apps.item(r, 1).data(Qt.UserRole) == app:
                self.apps.removeRow(r)
                break
        for r in reversed(range(self.browsers.rowCount())):
            if self.current["windows"][self.browsers.item(r, 0).data(Qt.UserRole)]["app"] == app:
                self.browsers.removeRow(r)
        self.removed.add(app)
        self.ticked()
        self.dirty()

    def ticked(self):
        """Redraw without the switched-off apps; keep the "All" box in step."""
        self.grey()
        on = [box.isChecked() for _, box, _ in self.app_rows()]
        self.all.set_state(Qt.Checked if on and all(on) else Qt.Unchecked if not any(on) else Qt.PartiallyChecked)
        self.preview.show_layout(self.current, self.hidden_apps())

    def dirty(self, on=True):
        """Save changes is active, and blue, only while there are unsaved edits."""
        self.save_btn.setEnabled(on)
        self.discard_btn.setEnabled(on)
        self.save_btn.setStyleSheet(
            "QPushButton { background: palette(highlight); color: palette(highlighted-text);"
            " border: 1px solid palette(highlight); border-radius: 3px; padding: 5px 12px; }" if on else "")
        if not on:
            self.screen_unsaved = False   # the screens show the unsaved version (after an undo)
            self.edited = set()      # windows whose pages were edited and not saved yet
            self.removed = set()     # apps taken out with the trash can, not saved yet

    def tick_all(self):
        rows = self.app_rows()
        on = not all(box.isChecked() for _, box, _ in rows)
        for _, box, _ in rows:
            box.blockSignals(True)
            box.setChecked(on)
            box.blockSignals(False)
        self.ticked()
        self.dirty()

    def select(self, profile=None):
        """Show the chosen profile as saved, or the given one (then as unsaved edits)."""
        self.shown = self.picker.currentIndex()
        name = self.picker.currentData()
        self.current = profile if profile else profiles.find(name) if name else None
        p = self.current
        self.removed = set()
        for t in (self.apps, self.browsers):
            t.setRowCount(0)
            t.setProperty("lit_row", None)
        if not p:
            self.preview.show_layout(None)
            return
        set_hues([a["app"] for a in p["apps"]] + [w["app"] for w in p["windows"]])
        for a in sorted(p["apps"], key=lambda a: (a.get("name") or a["app"]).lower()):
            r = self.apps.rowCount()
            self.apps.insertRow(r)
            box = QCheckBox()
            box.setChecked(a.get("enabled", True))
            box.toggled.connect(lambda _on: (self.ticked(), self.dirty()))
            cell = centered(box)
            self.apps.setCellWidget(r, 0, cell)
            self.hover[self.apps].watch(cell)
            item = QTableWidgetItem(a.get("name") or a["app"])
            item.setFlags(Qt.ItemIsEnabled)
            item.setIcon(swatch(a["app"]))
            item.setData(Qt.UserRole, a["app"])
            self.apps.setItem(r, 1, item)
            spin = QSpinBox()
            spin.setFrame(False)
            clear = spin.palette()
            clear.setColor(clear.ColorRole.Base, Qt.transparent)       # the row color shows through
            spin.setPalette(clear)
            spin.setRange(0, 300)
            spin.setValue(int(a.get("start_delay", 0)))
            spin.valueChanged.connect(lambda _v: self.dirty())
            self.apps.setCellWidget(r, 2, spin)
            self.hover[self.apps].watch(spin)
            trash = QToolButton()
            trash.setIcon(QIcon.fromTheme("edit-delete", QIcon.fromTheme("user-trash")))
            trash.setAutoRaise(True)
            trash.setToolTip("Remove %s and its windows from the profile (saved with Save changes; "
                             "Undo last brings it back)" % (a.get("name") or a["app"]))
            trash.clicked.connect(lambda _=False, app=a["app"]: self.remove_app(app))
            cell = centered(trash)
            self.apps.setCellWidget(r, 3, cell)
            self.hover[self.apps].watch(cell)
        for i, w in sorted(enumerate(p["windows"]), key=lambda iw: iw[1]["caption"].lower()):
            if "urls" not in w:
                continue
            r = self.browsers.rowCount()
            self.browsers.insertRow(r)
            t = QTableWidgetItem(w["caption"])
            t.setIcon(swatch(w["app"]))
            t.setData(Qt.UserRole, i)
            self.browsers.setItem(r, 0, t)
            pages = QTableWidgetItem(str(len(w["urls"])))
            pages.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.browsers.setItem(r, 1, pages)
        self.ticked()
        self.dirty(False)

    def unsaved_ok(self):
        """With unsaved edits: save them, drop them, or stop (returns False)."""
        if not self.save_btn.isEnabled():
            return True
        answer = QMessageBox.question(
            self, "Unsaved changes", "Save the changes to \"%s\" first?" % self.current["name"],
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Save:
            self.save_changes()    # saves and reloads the profile onto the screens
            return False
        if self.screen_unsaved:
            self.discard()         # the screens are reloaded to the saved profile first
            return False
        self.select()              # back to the saved profile
        return True

    def discard(self, detached=False):
        """Back to the saved profile, in this window and, if they show the
        unsaved version, on the screens: the two always show the same."""
        p, reload_screens = self.current, self.screen_unsaved
        self.select()
        if reload_screens and p:
            self.app.run_cli(["profile", "reload", p["name"], "--no-undo"], done=self.refresh, detached=detached)

    def picked(self, index):
        if index != self.shown and self.current and not self.unsaved_ok():
            self.picker.blockSignals(True)
            self.picker.setCurrentIndex(self.shown)      # stay on the edited profile
            self.picker.blockSignals(False)
            return
        self.select()

    def need(self):
        if not self.current:
            QMessageBox.information(self, "Profiles", "Choose a profile first.")
        return self.current

    def save_changes(self, detached=False):
        p = self.need()
        if not p:
            return
        rows = {app: (box, spin) for app, box, spin in self.app_rows()}   # rows are sorted by name
        p["apps"] = [a for a in p["apps"] if a["app"] not in self.removed]
        p["windows"] = [w for w in p["windows"] if w["app"] not in self.removed]
        p["media"] = [m for m in p.get("media", []) if m.get("app") not in self.removed]
        for a in p["apps"]:
            box, spin = rows[a["app"]]
            a["enabled"] = box.isChecked()
            a["start_delay"] = spin.value()
        # the command saves it and reloads it onto the screens, as one step for Undo last
        extra = self.choices_for(p, "reload", plan_from=True)
        if extra is None:
            return
        path = os.path.join(config.STATE_DIR, "edited-profile.json")
        with open(path, "w") as f:
            json.dump(p, f)
        self.dirty(False)
        self.save_btn.setEnabled(False)
        self.app.run_cli(["profile", "save-edit", p["name"], "--from", path] + extra, done=self.refresh,
                         detached=detached)

    def edit_pages(self, row, _col):
        p = self.need()
        if not p:
            return
        index = self.browsers.item(row, 0).data(Qt.UserRole)
        w = p["windows"][index]
        self.browsers.setSelectionMode(QAbstractItemView.SingleSelection)
        self.browsers.selectRow(row)
        d = QDialog(self)
        d.setWindowTitle("Pages of this window")
        v = QVBoxLayout(d)
        v.addWidget(hint("One address per line. They open as a new window when the profile is loaded "
                         "and this window is not already there."))
        k = w.get("active") or 0
        if 0 < k < len(w["urls"]):
            v.addWidget(hint("In front: line %d, %s" % (k + 1, w["urls"][k][:80])))
        edit = QPlainTextEdit("\n".join(w["urls"]))
        v.addWidget(edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        done = bb.button(QDialogButtonBox.Ok)
        done.setText("Done")
        done.setToolTip("Take the pages over; \"Save changes\" then saves them with the profile.")
        done.setEnabled(index in self.edited)        # edited before and not saved yet
        edit.textChanged.connect(lambda: done.setEnabled(True))       # editing started = changed
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        d.resize(640, 400)
        ok = d.exec() == QDialog.Accepted
        self.browsers.clearSelection()
        self.browsers.setSelectionMode(QAbstractItemView.NoSelection)
        if ok:                     # kept in memory until "Save changes", like the ticks
            k = w.get("active") or 0
            front = w["urls"][k] if 0 <= k < len(w["urls"]) else None
            w["urls"] = [l.strip() for l in edit.toPlainText().splitlines() if l.strip()]
            # the page in front stays in front, wherever it moved to (else the first)
            w["active"] = w["urls"].index(front) if front in w["urls"] else 0
            self.browsers.item(row, 1).setText(str(len(w["urls"])))
            self.dirty()
            self.edited.add(index)

    def choices_for(self, p, mode, plan_from=False):
        """Ask about saved windows that are not clearly found; returns extra
        arguments for the command, or None if cancelled."""
        if config.load_conf().get("when_unsure") != "ask":
            return []
        args = ["profile", "plan", p["name"]] + (["--" + mode] if mode != "unite" else [])
        if plan_from:                       # plan for the edited, not yet saved profile
            path = os.path.join(config.STATE_DIR, "plan-profile.json")
            with open(path, "w") as f:
                json.dump(p, f)
            args += ["--from", path]
        proc = QProcess()
        proc.start(cli_path(), args)
        if not proc.waitForFinished(20000) or proc.exitCode() != 0:
            return []                       # the command itself decides then
        try:
            pl = json.loads(bytes(proc.readAllStandardOutput()).decode().strip().splitlines()[-1])
        except (ValueError, IndexError):
            return []
        if not pl["questions"]:
            return []
        choices = choose(self, pl, p)
        if choices is None:
            return None
        path = os.path.join(config.STATE_DIR, "choices.json")
        with open(path, "w") as f:
            json.dump(choices, f)
        return ["--choices", path]

    def act(self, sub):
        p = self.need()
        if p and not self.unsaved_ok():
            return
        if not p:
            return
        extra = self.choices_for(p, "arrange" if sub == "arrange" else "reload" if sub == "reload" else "unite")
        if extra is not None:
            self.app.run_cli(["profile", sub, p["name"]] + extra, done=self.refresh)

    UNDO_TEXT = {
        "save": ("Undo Save changes", "Reload the profile as it was before the last Save changes onto the "
                                      "screens and show it here as unsaved edits: Save changes keeps it, "
                                      "Discard changes goes back to the saved one."),
        "unite": ("Undo Unite", "Put the windows back as they were before the last Unite and show the "
                                "profile as it was before here, as unsaved edits (windows Unite added to it "
                                "are out): Save changes keeps it, Discard changes goes back to the saved one."),
        "reload": ("Undo Reload", "Put the screens back as before the last Reload: apps it closed start "
                                  "again, apps it started close, windows go back. The profile is not changed."),
        "arrange": ("Undo Arrange", "Put the windows back where they were before the last Arrange. The "
                                    "profile is not changed."),
        "load": ("Undo last", "Put the screens back as before the last load. The profile is not changed."),
    }

    def show_undo(self):
        """The undo button names what it takes back."""
        action = profiles.undo_action()
        text, tip = self.UNDO_TEXT.get(action, ("Undo", "Nothing to undo yet."))
        self.undo_btn.setText(text)
        self.undo_btn.setToolTip(tip)
        self.undo_btn.setEnabled(action is not None)

    def undo(self):
        if not self.unsaved_ok():
            return
        last = profiles.undo_point() or {}
        before = last.get("profile") if last.get("undo_kind") in ("edit", "both") else None
        self.app.run_cli(["profile", "undo"], done=lambda: self.undone(before))

    def undone(self, before):
        """After an undo: the profile as it was before the undone action, as
        unsaved edits (Save changes keeps it, Discard changes drops it)."""
        self.refresh()
        if not before:
            return
        i = self.picker.findData(before["name"])
        if i < 0:
            return
        self.picker.blockSignals(True)
        self.picker.setCurrentIndex(i)
        self.picker.blockSignals(False)
        self.select(profiles.migrate(before))
        self.dirty()
        self.screen_unsaved = True     # the undo reloaded this version onto the screens

    def new(self):
        name, ok = QInputDialog.getText(self, "New profile", "Name for what is open now:")
        if ok and name.strip():
            self.save_as(name.strip())

    def take_over(self):
        p = self.need()
        if p and not self.unsaved_ok():
            return
        if p:
            self.save_as(p["name"])

    def save_as(self, name):
        old = profiles.find(name)
        if old and QMessageBox.question(
                self, "Replace profile",
                "Profile \"%s\" already exists (saved %s, %d apps, %d windows).\nReplace it with what is open "
                "now? Your edits (apps switched off, start delays) are kept." % (
                    old["name"], old.get("updated", "?").replace("T", " "), len(old["apps"]),
                    len(old["windows"]))) != QMessageBox.Yes:
            return
        self.current = {"name": name}
        self.app.run_cli(["profile", "save", name, "--force"], done=self.refresh)

    def use_at_login(self):
        p = self.need()
        if p:
            config.conf_set(config.CONF, "on_login", "profile:" + p["name"])
            self.app.status("At login: profile %s" % p["name"])

    def rename(self):
        p = self.need()
        if not p:
            return
        new, ok = QInputDialog.getText(self, "Rename profile", "New name:", text=p["name"])
        if ok and new.strip() and new.strip() != p["name"]:
            try:
                profiles.rename(p["name"], new.strip())
            except ValueError as e:
                QMessageBox.warning(self, "Rename", str(e))
            self.current = profiles.find(new.strip())
            self.refresh()

    def delete(self):
        p = self.need()
        if p and QMessageBox.question(self, "Delete profile", "Delete profile \"%s\"?" % p["name"]) == QMessageBox.Yes:
            profiles.delete(p["name"])
            self.current = None
            self.refresh()

    def export(self):
        p = self.need()
        if p:
            path, _ = QFileDialog.getSaveFileName(self, "Export profile", profiles.slug(p["name"]) + ".json",
                                                  "Profiles (*.json)")
            if path:
                profiles.export(p["name"], path)

    def import_(self):
        path, _ = QFileDialog.getOpenFileName(self, "Import profile", "", "Profiles (*.json)")
        if not path:
            return
        try:
            self.current = profiles.import_file(path)
        except ValueError as e:
            if "already exists" in str(e) and QMessageBox.question(self, "Import", str(e).split(" (")[0] +
                                                                   ". Replace it?") == QMessageBox.Yes:
                self.current = profiles.import_file(path, overwrite=True)
            else:
                QMessageBox.warning(self, "Import", str(e))
        self.refresh()


class Settings(Page):
    title = "Settings"

    def __init__(self, app):
        super().__init__(app)
        outer = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        self.form = QVBoxLayout(inner)
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        self.widgets = {}
        groups = {}
        for st in config.SETTINGS:
            if st["section"] not in groups:
                g = QGroupBox(st["section"])
                fl = QFormLayout(g)
                fl.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
                fl.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
                fl.setLabelAlignment(Qt.AlignLeft)
                groups[st["section"]] = fl
                self.form.addWidget(g)
            w = self.make(st)
            self.widgets[st["key"]] = w
            form = groups[st["section"]]
            if st["key"] == "exclude":
                pick = QPushButton("Choose from open apps…")
                pick.clicked.connect(self.pick_apps)
                line = QHBoxLayout()
                line.addWidget(w, 1)
                line.addWidget(pick)
                form.addRow(st["label"], line)
            else:
                form.addRow(st["label"], w)
            text = st["help"] + (" Example: %s" % st["example"] if st.get("example") else "")
            form.addRow("", hint(text))
        self.form.addStretch()
        # like KDE's System Settings: Defaults on the left, Reset and Apply on the right,
        # each active only when it would change something
        self.saved = {}
        row = QHBoxLayout()
        self.defaults_btn = button("Defaults", lambda: self.fill(config.DEFAULTS),
                                   "Put every setting to its default value (still to be applied).")
        row.addWidget(self.defaults_btn)
        row.addWidget(hint("Saved to %s; the comments in the file are kept." % config.CONF), 1)
        self.reset_btn = button("Reset", self.refresh, "Take back the changes that are not applied yet.")
        self.apply_btn = button("Apply", self.apply, "Save the changed settings.")
        row.addWidget(self.reset_btn)
        row.addWidget(self.apply_btn)
        outer.addLayout(row)
        for st in config.SETTINGS:
            w = self.widgets[st["key"]]
            for sig in ("toggled", "valueChanged", "currentTextChanged", "textChanged"):
                if hasattr(w, sig):
                    getattr(w, sig).connect(lambda *_: self.update_buttons())
                    break
        self.update_buttons()

    def pick_apps(self):
        """Choose apps to leave alone from the apps open now (ids shown with names)."""
        proc = QProcess()
        proc.start(cli_path(), ["windows", "--json"])
        if not proc.waitForFinished(15000):
            QMessageBox.warning(self, "Apps", "Could not list the open apps.")
            return
        try:
            apps = json.loads(bytes(proc.readAllStandardOutput()).decode())
        except ValueError:
            QMessageBox.warning(self, "Apps", "Could not list the open apps.")
            return
        current = set(self.widgets["exclude"].text().split())
        d = QDialog(self)
        d.setWindowTitle("Apps to leave alone")
        v = QVBoxLayout(d)
        v.addWidget(hint("Ticked apps are neither closed by Save & Shut Down nor started again. "
                         "Shown: the open apps, with their id in brackets."))
        boxes = []
        for a in sorted(apps, key=lambda a: a["name"].lower()):
            if a["ignored"] and a["id"] not in current:
                continue                 # desktop parts that are always left alone
            b = QCheckBox("%s  (%s)" % (a["name"], a["id"]))
            b.setChecked(a["id"] in current)
            b.setProperty("app_id", a["id"])
            v.addWidget(b)
            boxes.append(b)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        v.addWidget(bb)
        if d.exec() == QDialog.Accepted:
            shown = {b.property("app_id") for b in boxes}
            keep = [x for x in current if x not in shown]          # ids of apps not open now
            chosen = [b.property("app_id") for b in boxes if b.isChecked()]
            self.widgets["exclude"].setText(" ".join(keep + chosen))

    def make(self, st):
        if st["type"] == "bool":
            return QCheckBox()
        if st["type"] == "int":
            s = QSpinBox()
            s.setRange(st["min"], st["max"])
            return s
        if st["type"] in ("choice", "login"):
            c = QComboBox()
            c.setEditable(st["type"] == "login")
            c.addItems(st.get("choices") or ["last", "ask", "nothing"])
            return c
        return QLineEdit()

    def value(self, st, w):
        if st["type"] == "bool":
            return "yes" if w.isChecked() else "no"
        if st["type"] == "int":
            return str(w.value())
        if st["type"] in ("choice", "login"):
            return w.currentText()
        return w.text()

    def current(self):
        return {st["key"]: self.value(st, self.widgets[st["key"]]) for st in config.SETTINGS}

    def dirty(self):
        return bool(self.saved) and self.current() != self.saved

    def update_buttons(self):
        """Apply and Reset only with changes not applied yet (a value changed and
        changed back is no change); Defaults only when something is not default."""
        if not hasattr(self, "apply_btn"):
            return
        changed = self.dirty()
        self.apply_btn.setEnabled(changed)
        self.reset_btn.setEnabled(changed)
        self.defaults_btn.setEnabled(self.current() != dict(config.DEFAULTS))
        self.apply_btn.setStyleSheet(
            "QPushButton { background: palette(highlight); color: palette(highlighted-text);"
            " border: 1px solid palette(highlight); border-radius: 3px; padding: 5px 12px; }" if changed else "")

    def refresh(self):
        self.saved = config.load_conf()
        self.fill(self.saved)

    def fill(self, cfg):
        """Show these values in the fields (saved ones, or the defaults)."""
        for st in config.SETTINGS:
            w, v = self.widgets[st["key"]], cfg[st["key"]]
            if st["type"] == "bool":
                w.setChecked(v == "yes")
            elif st["type"] == "int":
                w.setValue(int(v))
            elif st["type"] == "login":
                w.clear()
                w.addItems(["last", "ask", "nothing"] + ["profile:" + p["name"] for p in profiles.all_profiles()
                                                        if not p.get("auto")])
                w.setCurrentText(v)
            elif st["type"] == "choice":
                w.setCurrentText(v)
            else:
                w.setText(v)
        self.update_buttons()

    def apply(self):
        """Save the changed settings; False if one was refused."""
        cfg = config.load_conf()
        changed = []
        for st in config.SETTINGS:
            v = self.value(st, self.widgets[st["key"]])
            if v != cfg[st["key"]]:
                try:
                    config.conf_set(config.CONF, st["key"], v)
                    changed.append(st["key"])
                except ValueError as e:
                    QMessageBox.warning(self, "Settings", str(e))
                    return False
        self.app.status("Saved: %s" % (", ".join(changed) or "nothing changed"))
        self.refresh()
        return True

    def unsaved_ok(self):
        """With changes not applied: apply, discard or stay (False)."""
        if not self.dirty():
            return True
        answer = QMessageBox.question(self, "Settings not applied", "Apply the changed settings?",
                                      QMessageBox.Apply | QMessageBox.Discard | QMessageBox.Cancel,
                                      QMessageBox.Apply)
        if answer == QMessageBox.Cancel:
            return False
        if answer == QMessageBox.Apply:
            return self.apply()
        self.refresh()
        return True


class Showing(Page):
    title = "Where to show it"

    def __init__(self, app):
        super().__init__(app)
        v = QVBoxLayout(self)
        v.addWidget(hint("Everything is always in the app menu (and found by search). Here you can also "
                         "pin it to the Application Launcher's favorites, put a button on a panel or an icon "
                         "on the desktop, and give it a keyboard shortcut. The fixed power row of the "
                         "Application Launcher (Sleep, Restart, Shut Down) is built into Plasma and cannot "
                         "be extended."))
        v.addWidget(hint("<b>Favorite</b>: the favorites are the same in every Application Launcher, on all "
                         "screens. <b>Desktop</b>: Plasma shows the desktop folder on the main screen; drag "
                         "an icon to another screen if you want it there. <b>Panel</b>: a button on the "
                         "panel chosen below."))
        row = QHBoxLayout()
        row.addWidget(QLabel("Panel for buttons:"))
        self.panel = QComboBox()
        row.addWidget(self.panel, 1)
        v.addLayout(row)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["", "Favorite", "Desktop", "Panel", "Shortcut"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.verticalHeader().hide()
        quiet(self.table)
        v.addWidget(self.table)
        v.addWidget(hint("Click the shortcut field and press the keys; Escape or Backspace removes it. A "
                         "combination that is already used elsewhere is refused, with its owner."))

    def refresh(self):
        self.panel.clear()
        try:
            for i, label in placement.panels():
                self.panel.addItem(label, i)
        except Exception as e:                    # Plasma not reachable
            self.app.status("Panels: %s" % e)
        slugs = [(profiles.slug(p["name"]), p["name"]) for p in
                 sorted(profiles.all_profiles(), key=lambda p: p["name"].lower()) if not p.get("auto")]
        rows = placement.entries(slugs)
        self.table.setRowCount(len(rows))
        for r, (desktop, label) in enumerate(rows):
            item = QTableWidgetItem(label)
            item.setFlags(Qt.ItemIsEnabled)
            self.table.setItem(r, 0, item)
            for col, getter, setter in ((1, placement.is_favorite, placement.set_favorite),
                                        (2, placement.has_desktop_icon, placement.set_desktop_icon)):
                box = QCheckBox()
                try:
                    box.setChecked(getter(desktop))
                except Exception:
                    box.setEnabled(False)
                box.toggled.connect(lambda on, d=desktop, s=setter: self.safe(s, d, on))
                self.table.setCellWidget(r, col, centered(box))
            pbox = QCheckBox()
            try:
                pbox.setChecked(bool(placement.panel_buttons(desktop)))
            except Exception:
                pbox.setEnabled(False)
            pbox.toggled.connect(lambda on, d=desktop: self.panel_button(d, on))
            self.table.setCellWidget(r, 3, centered(pbox))
            keys = QKeySequenceEdit()
            keys.setMaximumSequenceLength(1)
            keys.setClearButtonEnabled(True)
            try:
                code = placement.shortcut(desktop, label)
                if code:
                    keys.setKeySequence(QKeySequence(code))
            except Exception:
                pass
            keys.editingFinished.connect(lambda d=desktop, l=label, k=keys: self.set_keys(d, l, k))
            keys.keySequenceChanged.connect(lambda seq, d=desktop, l=label, k=keys:
                                            seq.isEmpty() and self.set_keys(d, l, k))
            self.table.setCellWidget(r, 4, keys)
        self.table.resizeColumnsToContents()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)

    def safe(self, fn, *args):
        try:
            fn(*args)
        except Exception as e:
            QMessageBox.warning(self, "Where to show it", str(e))

    def panel_button(self, desktop, on):
        if on:
            if self.panel.currentData() is None:
                return
            self.safe(placement.add_panel_button, desktop, self.panel.currentData())
        else:
            self.safe(placement.remove_panel_buttons, desktop)

    def set_keys(self, desktop, label, edit):
        seq = edit.keySequence()
        code = seq[0].toCombined() if not seq.isEmpty() else 0
        try:
            owner = placement.set_shortcut(desktop, label, code)
        except Exception as e:
            QMessageBox.warning(self, "Shortcut", str(e))
            return
        if owner:
            QMessageBox.warning(self, "Shortcut", "%s is already used by %s." % (seq.toString(), owner))
            edit.clear()
        else:
            self.app.status("%s: %s" % (label, seq.toString() or "no shortcut"))


class Log(Page):
    title = "Log"

    def __init__(self, app):
        super().__init__(app)
        v = QVBoxLayout(self)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(QFont("monospace"))
        v.addWidget(self.text)
        row = QHBoxLayout()
        row.addStretch()
        for label, slot in (("Refresh", self.refresh), ("Copy for an issue report", self.copy)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            row.addWidget(b)
        v.addLayout(row)

    def lines(self, n=400):
        try:
            with open(config.LOG) as f:
                return f.read().splitlines()[-n:]
        except OSError:
            return ["(no log yet)"]

    def refresh(self):
        self.text.setPlainText("\n".join(self.lines()))
        self.text.verticalScrollBar().setValue(self.text.verticalScrollBar().maximum())

    def copy(self):
        info = "plasma-reopen-windows; %s; Python %s" % (platform.platform(), platform.python_version())
        QApplication.clipboard().setText(info + "\n\n" + "\n".join(self.lines(150)))
        self.app.status("Copied the last 150 log lines")


# ---- main window ------------------------------------------------------------------
class Main(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Reopen Windows")
        self.setWindowIcon(QIcon.fromTheme("view-restore"))
        self.ui_state = read_ui_state()
        self.resize(*self.ui_state.get("size", [1000, 700]))
        body = QWidget()
        h = QHBoxLayout(body)
        self.nav = QListWidget()
        self.nav.setMaximumWidth(165)
        self.stack = QStackedWidget()
        self.pages = [Overview(self), Profiles(self), Settings(self), Showing(self), Log(self)]
        for page, icon in zip(self.pages, ("go-home", "view-restore", "configure", "favorite", "text-x-log")):
            self.nav.addItem(QListWidgetItem(QIcon.fromTheme(icon), page.title))
            self.stack.addWidget(page)
        self.nav.currentRowChanged.connect(self.show_page)
        h.addWidget(self.nav)
        h.addWidget(self.stack, 1)
        self.setCentralWidget(body)
        self.procs = []
        # progress of the command running now: step text with count and countdown, and a bar
        self.busy, self.cur, self.saved_buttons = False, {}, {}
        self.step = QLabel()
        self.bar = QProgressBar()
        self.bar.setMaximumWidth(220)
        self.bar.setTextVisible(False)
        # fixed colours (the active window's): the windows a command opens take
        # the focus, and the desktop draws an inactive window's bar faded
        pal = self.palette()
        self.bar.setFixedHeight(10)
        self.bar.setStyleSheet(
            "QProgressBar { background: %s; border: 1px solid %s; border-radius: 4px; }"
            "QProgressBar::chunk { background: %s; border-radius: 3px; }" % (
                pal.color(QPalette.Active, QPalette.Button).darker(108).name(),
                pal.color(QPalette.Active, QPalette.Mid).name(),
                pal.color(QPalette.Active, QPalette.Highlight).name()))
        self.statusBar().addPermanentWidget(self.step, 1)
        self.statusBar().addPermanentWidget(self.bar)
        self.bar.hide()
        self.msg_timer = QTimer(self)            # clears a message after a while
        self.msg_timer.setSingleShot(True)
        self.msg_timer.timeout.connect(lambda: self.busy or self.step.setText(""))
        self.tick = QTimer(self)
        self.tick.setInterval(100)               # smooth bar; the countdown text changes once a second
        self.tick.timeout.connect(self.show_step)
        self.nav.setCurrentRow(min(max(int(self.ui_state.get("page", 0)), 0), len(self.pages) - 1))

    def come_back(self):
        """Started a second time: this window to the front (on Wayland only
        KWin may do that, so the command-line tool asks it)."""
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.run_cli(["front"], quiet=True)

    def closeEvent(self, event):
        """Unsaved profile edits: save, discard or stay; then remember where we were."""
        if not self.pages[2].unsaved_ok():
            event.ignore()
            return
        prof = self.pages[1]
        if prof.save_btn.isEnabled():
            answer = QMessageBox.question(
                self, "Unsaved changes", "Save the changes to \"%s\" before closing?" % prof.current["name"],
                QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Save)
            if answer == QMessageBox.Cancel:
                event.ignore()
                return
            if answer == QMessageBox.Save:
                prof.save_changes(detached=True)     # finishes on the screens after the window is gone
            else:
                prof.discard(detached=True)
        try:
            os.makedirs(config.STATE_DIR, exist_ok=True)
            with open(UI_STATE, "w") as f:
                json.dump({"page": self.stack.currentIndex(), "size": [self.width(), self.height()],
                           "profile": prof.current["name"] if prof.current else None}, f)
        except OSError:
            pass
        event.accept()

    def show_page(self, i):
        here = self.stack.currentIndex()
        if here != i and isinstance(self.pages[here], Settings) and not self.pages[here].unsaved_ok():
            self.nav.blockSignals(True)
            self.nav.setCurrentRow(here)          # stay on the settings page
            self.nav.blockSignals(False)
            return
        self.stack.setCurrentIndex(i)
        self.pages[i].refresh()

    def status(self, text, ms=8000):
        """A message at the bottom (in the step field: the step field fills the
        status bar, Qt's own messages would get no room); not while busy."""
        if self.busy:
            return
        self.step.setText(text)
        self.msg_timer.start(ms)

    def run_cli(self, args, detached=False, done=None, quiet=False):
        """Run the command-line tool; the app stays responsive and shows its
        progress (quiet: a helper call, no progress, never refused)."""
        if detached:
            QProcess.startDetached(cli_path(), args)
            self.status("Started: %s" % " ".join(args))
            return
        if self.busy and not quiet:
            self.status("Still working on the last command…")
            return
        proc = QProcess(self)
        proc.setProcessChannelMode(QProcess.MergedChannels)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PRW_FROM_APP", "1")          # progress lines, and this window on top again when done
        proc.setProcessEnvironment(env)
        self.procs.append(proc)
        buf, out, notices = [""], [], []

        def read():
            buf[0] += bytes(proc.readAllStandardOutput()).decode(errors="replace")
            *lines, buf[0] = buf[0].split("\n")
            for line in lines:
                if line.startswith(config.PROGRESS_PREFIX):
                    try:
                        msg = json.loads(line[len(config.PROGRESS_PREFIX):])
                    except ValueError:
                        continue
                    if "notice" in msg:              # instead of a desktop notification
                        notices.append(msg)
                    elif not quiet:
                        if msg.get("step") != self.cur.get("step"):
                            msg["since"] = time.time()    # for the bar of a step with a time limit
                        else:
                            msg["since"] = self.cur.get("since", time.time())
                        self.cur = msg
                        self.show_step()
                elif line.strip():
                    out.append(line.strip())

        def finished(code, _status):
            buf[0] += "\n"
            read()
            if not quiet:
                self.set_busy(False)
            text = (out[-1] if out else "Done") if code == 0 else "Failed: %s" % (out[-1] if out else code)
            for n in notices:
                if n.get("critical"):
                    QMessageBox.warning(self, n["notice"], n.get("text") or n["notice"])
            normal = [n for n in notices if not n.get("critical")]
            if normal and code == 0:
                text = "%s: %s" % (normal[-1]["notice"], normal[-1]["text"]) if normal[-1].get("text") else normal[-1]["notice"]
            self.status(text, 15000)
            self.procs.remove(proc)
            if done:
                done()
        proc.readyReadStandardOutput.connect(read)
        proc.finished.connect(finished)
        if not quiet:
            self.set_busy(True)
            self.cur = {"step": "starting: " + " ".join(args[:2])}
            self.show_step()
        proc.start(cli_path(), args)

    def set_busy(self, on):
        """While a command runs: progress shown, the buttons that start
        commands (Overview, Profiles) off, their earlier state kept."""
        self.busy = on
        if on:
            self.msg_timer.stop()
            self.saved_buttons = {b: b.isEnabled() for page in self.pages[:2]
                                  for b in page.findChildren(QAbstractButton)}
            for b in self.saved_buttons:
                b.setEnabled(False)
            self.bar.show()
            self.tick.start()
        else:
            for b, was in self.saved_buttons.items():
                b.setEnabled(was)
            self.saved_buttons = {}
            self.tick.stop()
            self.bar.hide()
            self.step.setText("")

    def show_step(self):
        """The current step: text, "3 of 11", seconds left; the bar."""
        if not self.busy:
            return
        c = self.cur
        text = c.get("step") or "working"
        # always the solid bar (Breeze draws the "busy" animation faded):
        # a count fills it, a time limit fills it as the time passes, else it fills over and over
        now = time.time()
        if c.get("total"):
            self.bar.setRange(0, c["total"])
            self.bar.setValue(c.get("done") or 0)
        elif c.get("until") and c["until"] > c.get("since", now):
            span = c["until"] - c.get("since", now)
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(1000 * min(1, (now - c.get("since", now)) / span)))
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int((now * 400) % 1000))
        if c.get("until"):
            left = int(c["until"] - time.time())
            if left > 0:
                text += "  –  up to %d s" % left
        self.step.setText(text[0].upper() + text[1:] + " …")


def show_running():
    """If the app is open already, bring that window to the front; True then."""
    sock = QLocalSocket()
    sock.connectToServer(SOCKET)
    if not sock.waitForConnected(500):
        return False
    sock.write(b"show\n")
    sock.waitForBytesWritten(1000)
    sock.disconnectFromServer()
    return True


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("Reopen Windows")
    app.setDesktopFileName(APP_ID)
    if len(sys.argv) > 2 and sys.argv[1] == "--choose":
        # asked by the command-line tool (menu entry, login): answer on stdout
        with open(sys.argv[2]) as f:
            pl = json.load(f)
        choices = choose(None, pl, profiles.find(pl["profile"]))
        if choices is None:
            sys.exit(1)
        print(json.dumps(choices))
        sys.exit(0)
    if show_running():
        sys.exit(0)
    win = Main()
    win.show()
    QLocalServer.removeServer(SOCKET)        # left over if a copy crashed
    server = QLocalServer(win)
    server.newConnection.connect(lambda: (server.nextPendingConnection().deleteLater(), win.come_back()))
    server.listen(SOCKET)
    QTimer.singleShot(0, lambda: win.pages[win.stack.currentIndex()].refresh())
    sys.exit(app.exec())
