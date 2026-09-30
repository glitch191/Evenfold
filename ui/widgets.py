"""Reusable widgets: cards, drop zone, track list and tables, the mastering
panel, plain-language summaries, the advanced settings panel and the player.

Widgets read and change state only through the Session. All styling comes from
theme.qss (via objectName and dynamic properties) and theme.py.
"""

from __future__ import annotations

import math
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSizePolicy,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from audio.analysis import FLOOR_DB, SUBTYPES, format_duration
from audio.processing import (
    CEILING_RANGE,
    DEFAULT_TARGET_LUFS,
    GAP_RANGE,
    OUTPUT_SUBTYPE_CHOICES,
    TARGET_LUFS_RANGE,
)
from audio.summary import clipping_warning

from . import theme
from .playback import PlaybackEngine
from .session import Session, Track
from .tooltips import RESULT_COLUMN_TIPS, TIPS, tip
from .visualizers import LoudnessMeter, TruePeakIndicator, deviation_color

ICON_SIZE = QSize(18, 18)


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def fmt_lufs(value: float | None, unit: bool = True) -> str:
    if value is None or not math.isfinite(value) or value <= FLOOR_DB:
        return "—"
    return f"{value:.1f} LUFS" if unit else f"{value:.1f}"


def fmt_db(value: float | None, unit: str = "dB", signed: bool = False) -> str:
    if value is None or not math.isfinite(value) or value <= FLOOR_DB:
        return "—"
    return f"{value:+.1f} {unit}" if signed else f"{value:.1f} {unit}"


def track_meta_text(track: Track) -> str:
    if track.fmt is None:
        return track.filename
    return f"{track.fmt.bit_depth_label} · {track.fmt.samplerate_label} · {format_duration(track.fmt.duration_s)}"


def loudness_phrase(value: float | None, target: float) -> str:
    """Plain-language position of a loudness reading relative to the target."""
    if value is None or not math.isfinite(value):
        return "Waiting for a measurement"
    standard = "the streaming standard" if abs(target - DEFAULT_TARGET_LUFS) < 0.05 else "your target"
    off = value - target
    if abs(off) <= 0.5:
        return f"Volume matched to {standard}"
    if abs(off) <= 1.5:
        return f"Close to {standard}"
    return f"Quieter than {standard}" if off < 0 else f"Louder than {standard}"


def set_prop(widget: QWidget, name: str, value) -> None:
    if widget.property(name) != value:
        widget.setProperty(name, value)
        theme.repolish(widget)


def label(text: str = "", role: str | None = None, wrap: bool = False, name: str | None = None) -> QLabel:
    w = QLabel(text)
    if role:
        w.setProperty("role", role)
    if name:
        w.setObjectName(name)
    w.setWordWrap(wrap)
    return w


def button(text: str, variant: str | None = None, icon_name: str | None = None, tip_key: str | None = None,
           icon_color: str = "text_muted") -> QPushButton:
    b = QPushButton(text)
    if variant:
        b.setProperty("variant", variant)
    if icon_name:
        b.setIcon(theme.icon(icon_name, icon_color, active="text"))
        b.setIconSize(ICON_SIZE)
    if tip_key:
        b.setToolTip(tip(tip_key))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def divider() -> QFrame:
    line = QFrame()
    line.setObjectName("Divider")
    line.setFrameShape(QFrame.Shape.NoFrame)
    return line


# ---------------------------------------------------------------------------
# Small building blocks
# ---------------------------------------------------------------------------

class InfoDot(QLabel):
    """A small (i) icon that carries an explanatory tooltip."""

    def __init__(self, tip_key: str) -> None:
        super().__init__()
        self.setPixmap(theme.icon("ph.info", "text_faint").pixmap(QSize(14, 14)))
        self.setToolTip(tip(tip_key))
        self.setCursor(Qt.CursorShape.WhatsThisCursor)


class Card(QFrame):
    """Rounded panel with an optional caption header."""

    def __init__(self, title: str = "", tip_key: str | None = None, emphasis: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        if emphasis:
            self.setProperty("emphasis", True)
        outer = QVBoxLayout(self)
        m = theme.SPACE["md"]
        outer.setContentsMargins(m, m, m, m)
        outer.setSpacing(theme.SPACE["sm"] + 4)
        self.header = QHBoxLayout()
        self.header.setSpacing(theme.SPACE["sm"])
        if title:
            caption = label(title.upper(), "caption")
            self.header.addWidget(caption)
            if tip_key:
                caption.setToolTip(tip(tip_key))
                self.header.addWidget(InfoDot(tip_key))
            self.header.addStretch(1)
            outer.addLayout(self.header)
        self.body = QVBoxLayout()
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(theme.SPACE["sm"])
        outer.addLayout(self.body, 1)


class Chip(QLabel):
    def __init__(self, text: str = "", kind: str = "neutral") -> None:
        super().__init__(text)
        self.setObjectName("Chip")
        self.setProperty("chip", kind)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

    def set_kind(self, kind: str, text: str) -> None:
        self.setText(text)
        set_prop(self, "chip", kind)


class Notice(QFrame):
    """A tinted box with an icon and a plain-language message."""

    ICONS = {"warn": ("ph.warning-fill", "warn"), "bad": ("ph.x-circle-fill", "bad"), "info": ("ph.info-fill", "accent")}

    def __init__(self, text: str, level: str = "warn") -> None:
        super().__init__()
        self.setObjectName("Notice")
        self.setProperty("level", level)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)
        icon_name, color_name = self.ICONS[level]
        glyph = QLabel()
        glyph.setPixmap(theme.icon(icon_name, color_name).pixmap(QSize(16, 16)))
        glyph.setAlignment(Qt.AlignmentFlag.AlignTop)
        text_label = label(text, wrap=True, name="NoticeText")
        text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(text_label, 1)


class Reassurance(QWidget):
    """'Your original files are never modified', with a lock icon."""

    def __init__(self) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        glyph = QLabel()
        glyph.setPixmap(theme.icon("ph.shield-check", "text_faint").pixmap(QSize(14, 14)))
        text = label("Your original files are never modified", name="Reassurance")
        layout.addWidget(glyph)
        layout.addWidget(text)
        layout.addStretch(1)
        self.setToolTip(tip("originals_safe"))


class SegmentedControl(QFrame):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], tip_key: str | None = None) -> None:
        super().__init__()
        self.setObjectName("Segmented")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for key, text in options:
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            if tip_key:
                b.setToolTip(tip(tip_key))
            self._group.addButton(b)
            self._buttons[key] = b
            layout.addWidget(b)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))

    def set_current(self, key: str) -> None:
        if key in self._buttons:
            self._buttons[key].setChecked(True)

    def current(self) -> str:
        return next((k for k, b in self._buttons.items() if b.isChecked()), "")

    def set_enabled_key(self, key: str, enabled: bool) -> None:
        self._buttons[key].setEnabled(enabled)


class CollapsibleSection(QWidget):
    toggled = Signal(bool)

    def __init__(self, title: str, expanded: bool = False) -> None:
        super().__init__()
        self._title = title
        self.toggle = QToolButton()
        self.toggle.setObjectName("SectionToggle")
        self.toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toggle.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.toggle.setCheckable(True)
        self.toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.toggle.setIconSize(QSize(16, 16))
        self.toggle.setText(title)
        self.content = QWidget()
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(8, 4, 8, 8)
        self.content_layout.setSpacing(theme.SPACE["sm"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.toggle)
        layout.addWidget(self.content)
        self.toggle.toggled.connect(self._apply)
        self.toggle.setChecked(expanded)
        self._apply(expanded)

    def _apply(self, expanded: bool) -> None:
        self.toggle.setIcon(theme.icon("ph.caret-down-bold" if expanded else "ph.caret-right-bold", "text_muted"))
        self.content.setVisible(expanded)
        self.toggled.emit(expanded)

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(expanded)


# ---------------------------------------------------------------------------
# Drop zone
# ---------------------------------------------------------------------------

def wav_paths_from_mime(mime) -> list[str]:
    if not mime.hasUrls():
        return []
    paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
    return [p for p in paths if Path(p).is_dir() or p.lower().endswith(".wav")]


class DropZone(QFrame):
    """Where tracks come in: drag files or folders here, or browse."""

    filesDropped = Signal(list)
    browseRequested = Signal()

    def __init__(self, compact: bool = False) -> None:
        super().__init__()
        self.setObjectName("DropZoneCompact" if compact else "DropZone")
        self.setProperty("active", False)
        self.setAcceptDrops(True)
        if compact:
            layout = QHBoxLayout(self)
            layout.setContentsMargins(12, 10, 12, 10)
            layout.setSpacing(8)
            glyph = QLabel()
            glyph.setPixmap(theme.icon("ph.plus", "text_faint").pixmap(QSize(14, 14)))
            layout.addWidget(glyph)
            layout.addWidget(label("Drop more WAV files here, or", "faint"))
            browse = button("browse", "link", tip_key="add_files")
            browse.clicked.connect(self.browseRequested)
            layout.addWidget(browse)
            layout.addStretch(1)
            return

        layout = QVBoxLayout(self)
        layout.setContentsMargins(48, 48, 48, 48)
        layout.setSpacing(theme.SPACE["md"])
        layout.addStretch(1)
        art = QLabel()
        art.setObjectName("DropIcon")
        art.setAlignment(Qt.AlignmentFlag.AlignCenter)
        art.setPixmap(theme.icon("ph.upload-simple", "accent").pixmap(QSize(32, 32)))
        layout.addWidget(art, 0, Qt.AlignmentFlag.AlignHCenter)
        title = label("Drop your album’s WAV files here", "display")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        hint = label("Any mix of bit depths and sample rates works. Each file keeps its own format.", "muted", wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        choose = button("Choose Files…", icon_name="ph.folder-open", tip_key="add_files")
        choose.clicked.connect(self.browseRequested)
        layout.addSpacing(theme.SPACE["sm"])
        layout.addWidget(choose, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(theme.SPACE["sm"])
        safe = Reassurance()
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(safe)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)

    def dragEnterEvent(self, event) -> None:
        if wav_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            set_prop(self, "active", True)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:
        set_prop(self, "active", False)

    def dropEvent(self, event) -> None:
        set_prop(self, "active", False)
        paths = wav_paths_from_mime(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self.filesDropped.emit(paths)


# ---------------------------------------------------------------------------
# Track list (Simple mode) and track table (Advanced mode)
# ---------------------------------------------------------------------------

def status_chip(track: Track) -> tuple[str, str]:
    if track.status == "processing":
        return "processing", f"Processing {int(track.progress * 100)}%"
    return track.status, track.status_label


class TrackRow(QWidget):
    def __init__(self, track: Track) -> None:
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        grid = QGridLayout(self)
        grid.setContentsMargins(10, 10, 10, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        self.number = label("", name="TrackNumber")
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label("", name="TrackTitle")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta = label("", name="TrackMeta")
        self.meta.setToolTip(tip("format"))
        self.warning = QLabel()
        self.warning.setPixmap(theme.icon("ph.warning-fill", "warn").pixmap(QSize(14, 14)))
        self.warning.setToolTip(tip("clipping"))
        self.chip = Chip()
        self.summary = label("", wrap=True, name="TrackSummary")
        self.progress = QProgressBar()
        self.progress.setProperty("size", "thin")
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        meta_row = QHBoxLayout()
        meta_row.setSpacing(6)
        meta_row.addWidget(self.meta)
        meta_row.addWidget(self.warning)
        meta_row.addStretch(1)
        grid.addWidget(self.number, 0, 0, 2, 1, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.title, 0, 1)
        grid.addWidget(self.chip, 0, 2, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        grid.addLayout(meta_row, 1, 1, 1, 2)
        grid.addWidget(self.summary, 2, 1, 1, 2)
        grid.addWidget(self.progress, 3, 1, 1, 2)
        grid.setColumnStretch(1, 1)
        self.update_from(track)

    def update_from(self, track: Track) -> None:
        self.number.setText(str(track.track_number))
        self.title.setText(track.title or track.filename)
        self.title.setToolTip(track.path)
        self.meta.setText(track_meta_text(track))
        kind, text = status_chip(track)
        self.chip.set_kind(kind, text)
        clipped = track.analysis is not None and track.analysis.clipping.clipped
        self.warning.setVisible(clipped)
        summary = ""
        if track.status == "failed":
            summary = track.error
        elif track.status == "processing":
            summary = track.stage
        elif track.result is not None and track.result.description is not None:
            summary = track.result.description.headline
        self.summary.setText(summary)
        self.summary.setVisible(bool(summary))
        self.progress.setVisible(track.status == "processing")
        self.progress.setValue(int(track.progress * 1000))


class TrackList(QListWidget):
    """Simple-mode list of the album's tracks with a status for each."""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        self.setObjectName("TrackList")
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self._rows: dict[int, tuple[QListWidgetItem, TrackRow]] = {}
        self._syncing = False
        session.tracksChanged.connect(self.rebuild)
        session.trackChanged.connect(self.update_track)
        session.selectionChanged.connect(self._select)
        self.itemSelectionChanged.connect(self._on_selection)
        remove = QAction("Remove from Album", self)
        remove.setShortcut(QKeySequence.StandardKey.Delete)
        remove.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove.triggered.connect(self._remove_selected)
        self.addAction(remove)
        self.rebuild()

    def rebuild(self) -> None:
        self._syncing = True
        self.clear()
        self._rows.clear()
        for track in self.session.tracks:
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, track.uid)
            row = TrackRow(track)
            self.addItem(item)
            self.setItemWidget(item, row)
            self._rows[track.uid] = (item, row)
            self._fit(item, row)
        self._syncing = False
        self._select(self.session.selected_uid)

    def _fit(self, item: QListWidgetItem, row: TrackRow) -> None:
        """Size a row for the list's width, so wrapped summaries are never cut off."""
        width = max(120, self.viewport().width())
        layout = row.layout()
        height = layout.heightForWidth(width) if layout.hasHeightForWidth() else row.sizeHint().height()
        item.setSizeHint(QSize(width, max(height, row.minimumSizeHint().height())))

    def update_track(self, uid: int) -> None:
        entry = self._rows.get(uid)
        track = self.session.track(uid)
        if entry is None or track is None:
            return
        item, row = entry
        row.update_from(track)
        self._fit(item, row)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        for uid in self._rows:
            self.update_track(uid)

    def _select(self, uid: int) -> None:
        entry = self._rows.get(uid)
        self._syncing = True
        if entry is None:
            self.clearSelection()
        else:
            self.setCurrentItem(entry[0])
            self.scrollToItem(entry[0])
        self._syncing = False

    def _on_selection(self) -> None:
        if self._syncing:
            return
        items = self.selectedItems()
        if items:
            self.session.select(items[0].data(Qt.ItemDataRole.UserRole))

    def _remove_selected(self) -> None:
        items = self.selectedItems()
        if items:
            self.session.remove(items[0].data(Qt.ItemDataRole.UserRole))

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None or self.session.busy:
            return
        menu = QMenu(self)
        action = menu.addAction(theme.icon("ph.x", "text_muted"), "Remove from Album")
        action.setToolTip(tip("remove_track"))
        if menu.exec(self.viewport().mapToGlobal(pos)) is action:
            self.session.remove(item.data(Qt.ItemDataRole.UserRole))


def _table_item(text: str, editable: bool = False, align=Qt.AlignmentFlag.AlignLeft) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
    if editable:
        flags |= Qt.ItemFlag.ItemIsEditable
    item.setFlags(flags)
    item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
    return item


def _style_table(table: QTableWidget) -> None:
    table.setObjectName("DataTable")
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setAlternatingRowColors(False)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.verticalHeader().setVisible(False)
    table.verticalHeader().setDefaultSectionSize(36)
    table.horizontalHeader().setHighlightSections(False)
    table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
    table.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


STATUS_COLORS = {"analyzing": "info", "waiting": "text_muted", "processing": "info", "done": "good",
                 "previewed": "accent", "failed": "bad"}


class TrackTable(QTableWidget):
    """Advanced-mode track table with editable number, title and artist."""

    COLUMNS = (("#", "track_number"), ("Title", "track_title"), ("Artist", "track_artist"),
               ("Bit depth", "format"), ("Rate", "sample_rate"), ("Length", None), ("Status", None))

    def __init__(self, session: Session) -> None:
        super().__init__(0, len(self.COLUMNS))
        self.session = session
        _style_table(self)
        self.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                             | QAbstractItemView.EditTrigger.SelectedClicked)
        for col, (title, key) in enumerate(self.COLUMNS):
            header = QTableWidgetItem(title)
            if key:
                header.setToolTip(tip(key))
            self.setHorizontalHeaderItem(col, header)
        head = self.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        head.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col, width in ((0, 40), (2, 96), (3, 92), (4, 76), (5, 56), (6, 112)):
            self.setColumnWidth(col, width)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._syncing = False
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        session.tracksChanged.connect(self.rebuild)
        session.trackChanged.connect(self.update_track)
        session.selectionChanged.connect(self._select)
        self.itemChanged.connect(self._on_edit)
        self.itemSelectionChanged.connect(self._on_selection)
        remove = QAction("Remove from Album", self)
        remove.setShortcut(QKeySequence.StandardKey.Delete)
        remove.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        remove.triggered.connect(self._remove_selected)
        self.addAction(remove)
        self.rebuild()

    def _row_values(self, track: Track) -> list[str]:
        fmt = track.fmt
        return [
            str(track.track_number),
            track.title,
            track.artist,
            SUBTYPES[fmt.subtype][0] if fmt else "—",
            fmt.samplerate_label if fmt else "—",
            format_duration(fmt.duration_s) if fmt else "—",
            status_chip(track)[1],
        ]

    def rebuild(self) -> None:
        self._syncing = True
        self.setRowCount(len(self.session.tracks))
        for row, track in enumerate(self.session.tracks):
            for col, text in enumerate(self._row_values(track)):
                item = _table_item(text, editable=col in (0, 1, 2),
                                   align=Qt.AlignmentFlag.AlignRight if col == 0 else Qt.AlignmentFlag.AlignLeft)
                item.setData(Qt.ItemDataRole.UserRole, track.uid)
                if col == 1:
                    item.setToolTip(track.path)
                self.setItem(row, col, item)
            self._color_status(row, track)
        self._syncing = False
        self._select(self.session.selected_uid)

    def _color_status(self, row: int, track: Track) -> None:
        item = self.item(row, 6)
        if item is not None:
            item.setForeground(theme.color(STATUS_COLORS[track.status]))
            item.setToolTip(track.error if track.status == "failed" else "")

    def update_track(self, uid: int) -> None:
        row = self.session.index_of(uid)
        track = self.session.track(uid)
        if track is None or row < 0 or row >= self.rowCount():
            return
        self._syncing = True
        for col, text in enumerate(self._row_values(track)):
            item = self.item(row, col)
            if item is not None and item.text() != text and not (self.state() == QAbstractItemView.State.EditingState
                                                                and self.currentRow() == row and self.currentColumn() == col):
                item.setText(text)
        self._color_status(row, track)
        self._syncing = False

    def _on_edit(self, item: QTableWidgetItem) -> None:
        if self._syncing:
            return
        uid = item.data(Qt.ItemDataRole.UserRole)
        col = item.column()
        track = self.session.track(uid)
        if track is None:
            return
        if col == 0:
            text = item.text().strip()
            if text.isdigit() and int(text) > 0:
                self.session.set_track_meta(uid, number=int(text))
            else:
                self._syncing = True
                item.setText(str(track.track_number))
                self._syncing = False
        elif col == 1:
            self.session.set_track_meta(uid, title=item.text() or track.title)
        elif col == 2:
            self.session.set_track_meta(uid, artist=item.text())

    def _select(self, uid: int) -> None:
        row = self.session.index_of(uid)
        self._syncing = True
        if row < 0:
            self.clearSelection()
        elif self.currentRow() != row:
            self.selectRow(row)
        self._syncing = False

    def _on_selection(self) -> None:
        if self._syncing:
            return
        rows = self.selectionModel().selectedRows()
        if rows:
            item = self.item(rows[0].row(), 0)
            if item is not None:
                self.session.select(item.data(Qt.ItemDataRole.UserRole))

    def _remove_selected(self) -> None:
        if self.state() == QAbstractItemView.State.EditingState:
            return
        rows = self.selectionModel().selectedRows()
        if rows:
            self.session.remove(self.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole))

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None or self.session.busy:
            return
        menu = QMenu(self)
        action = menu.addAction(theme.icon("ph.x", "text_muted"), "Remove from Album")
        if menu.exec(self.viewport().mapToGlobal(pos)) is action:
            self.session.remove(item.data(Qt.ItemDataRole.UserRole))


class ResultsTable(QTableWidget):
    """Before/after measurements for every track (Advanced mode)."""

    COLUMNS = ("#", "Track", "Format", "Loudness before", "Loudness after", "Gain",
               "True peak before", "True peak after", "Limiting", "Clipping", "Result")

    def __init__(self, session: Session) -> None:
        super().__init__(0, len(self.COLUMNS))
        self.session = session
        _style_table(self)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        for col, title in enumerate(self.COLUMNS):
            header = QTableWidgetItem(title)
            key = RESULT_COLUMN_TIPS.get(title)
            if key:
                header.setToolTip(tip(key))
            self.setHorizontalHeaderItem(col, header)
        head = self.horizontalHeader()
        head.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        head.setStretchLastSection(True)
        for col, width in enumerate((40, 200, 150, 112, 112, 72, 118, 112, 80, 80)):
            self.setColumnWidth(col, width)
        self._syncing = False
        session.tracksChanged.connect(self.rebuild)
        session.trackChanged.connect(self._update_row)
        session.selectionChanged.connect(self._select)
        session.settingsChanged.connect(self.rebuild)
        self.itemSelectionChanged.connect(self._on_selection)
        self.rebuild()

    def _cells(self, track: Track) -> list[tuple[str, str | None, str]]:
        """(text, colour name, tooltip) for each column."""
        a, r = track.analysis, track.result
        target = self.session.settings.target_lufs
        fmt = track.fmt
        fmt_text = fmt.summary if fmt else "—"
        if r is not None and r.ok and r.output_subtype and fmt and r.output_subtype != fmt.subtype:
            fmt_text = f"{fmt_text} → {SUBTYPES[r.output_subtype][0]}"
        before_lufs = a.measurements.lufs if a else None
        before_tp = a.measurements.true_peak_db if a else None
        cells = [
            (str(track.track_number), None, ""),
            (track.title, None, track.path),
            (fmt_text, None, ""),
            (fmt_lufs(before_lufs), None, ""),
        ]
        if r is not None and r.ok:
            color = deviation_color(r.after.lufs, target) if math.isfinite(r.after.lufs) else None
            cells += [
                (fmt_lufs(r.after.lufs), color, ""),
                (fmt_db(r.gain_db, signed=True), None, ""),
                (fmt_db(before_tp, "dBTP"), "bad" if before_tp is not None and before_tp > 0 else None, ""),
                (fmt_db(r.after.true_peak_db, "dBTP"), None, ""),
                (f"{r.limiter.max_reduction_db:.1f} dB" if r.limiter.max_reduction_db >= 0.1 else "None",
                 "warn" if r.limiter.max_reduction_db >= 6 else None, ""),
            ]
        else:
            cells += [("—", None, ""), ("—", None, ""),
                      (fmt_db(before_tp, "dBTP"), "bad" if before_tp is not None and before_tp > 0 else None, ""),
                      ("—", None, ""), ("—", None, "")]
        clipped = a is not None and a.clipping.clipped
        cells.append(("Yes" if clipped else ("No" if a else "—"), "bad" if clipped else None,
                      clipping_warning(a.clipping, a.fmt) or "" if a else ""))
        if track.status == "failed":
            cells.append((track.error, "bad", track.error))
        elif r is not None and r.description is not None:
            text = r.description.headline + (" (preview)" if r.dry_run else "")
            cells.append((text, None, "\n".join(r.description.details + r.description.warnings)))
        else:
            cells.append((track.status_label, None, ""))
        return cells

    def rebuild(self) -> None:
        self._syncing = True
        self.setRowCount(len(self.session.tracks))
        for row, track in enumerate(self.session.tracks):
            self._fill(row, track)
        self._syncing = False
        self._select(self.session.selected_uid)

    def _fill(self, row: int, track: Track) -> None:
        for col, (text, color_name, tooltip) in enumerate(self._cells(track)):
            numeric = col in (0, 3, 4, 5, 6, 7, 8)
            item = _table_item(text, align=Qt.AlignmentFlag.AlignRight if numeric else Qt.AlignmentFlag.AlignLeft)
            item.setData(Qt.ItemDataRole.UserRole, track.uid)
            item.setForeground(theme.color(color_name or ("text" if col in (1, 4) else "text_muted")))
            if numeric:
                item.setFont(theme.font("body", tabular=True))
            if tooltip:
                item.setToolTip(tooltip)
            self.setItem(row, col, item)

    def _update_row(self, uid: int) -> None:
        row = self.session.index_of(uid)
        track = self.session.track(uid)
        if track is not None and 0 <= row < self.rowCount():
            self._syncing = True
            self._fill(row, track)
            self._syncing = False

    def _select(self, uid: int) -> None:
        row = self.session.index_of(uid)
        self._syncing = True
        if row < 0:
            self.clearSelection()
        elif self.currentRow() != row:
            self.selectRow(row)
        self._syncing = False

    def _on_selection(self) -> None:
        if self._syncing:
            return
        rows = self.selectionModel().selectedRows()
        if rows:
            item = self.item(rows[0].row(), 0)
            if item is not None:
                self.session.select(item.data(Qt.ItemDataRole.UserRole))


# ---------------------------------------------------------------------------
# Mastering panel and summaries
# ---------------------------------------------------------------------------

class MasterPanel(Card):
    """The one primary action, its secondary preview link, progress and outcome."""

    openOutputRequested = Signal()

    def __init__(self, session: Session) -> None:
        super().__init__(emphasis=True)
        self.session = session
        self.master = button("Master My Album", "primary", tip_key="master")
        self.master.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.master.clicked.connect(lambda: session.start(dry_run=False))
        self.explain = label("", "faint", wrap=True)
        self.explain.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview = button("Preview Results First", "link", tip_key="preview")
        self.preview.clicked.connect(lambda: session.start(dry_run=True))

        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.stage = label("", "faint", wrap=True)
        self.cancel = button("Cancel", "ghost", "ph.x", "cancel")
        self.cancel.clicked.connect(session.cancel)
        busy_row = QHBoxLayout()
        busy_row.setSpacing(8)
        busy_row.addWidget(self.stage, 1)
        busy_row.addWidget(self.cancel)
        self.busy_box = QWidget()
        busy = QVBoxLayout(self.busy_box)
        busy.setContentsMargins(0, 4, 0, 0)
        busy.setSpacing(8)
        busy.addWidget(self.progress)
        busy.addLayout(busy_row)

        self.outcome = label("", "body", wrap=True)
        self.outcome_icon = QLabel()
        outcome_row = QHBoxLayout()
        outcome_row.setSpacing(8)
        outcome_row.addWidget(self.outcome_icon, 0, Qt.AlignmentFlag.AlignTop)
        outcome_row.addWidget(self.outcome, 1)
        self.open_output = button("Open Output Folder", icon_name="ph.folder-open", tip_key="open_output")
        self.open_output.clicked.connect(self.openOutputRequested)
        self.outcome_box = QWidget()
        outcome = QVBoxLayout(self.outcome_box)
        outcome.setContentsMargins(0, 4, 0, 0)
        outcome.setSpacing(8)
        outcome.addLayout(outcome_row)
        outcome.addWidget(self.open_output)
        self.stale = Notice("You changed a setting after mastering. Master again to apply it.", "info")

        self.body.setSpacing(10)
        self.body.addWidget(self.master)
        self.body.addWidget(self.explain)
        self.body.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self.body.addWidget(self.busy_box)
        self.body.addWidget(self.outcome_box)
        self.body.addWidget(self.stale)
        self.body.addWidget(divider())
        self.body.addWidget(Reassurance())

        session.busyChanged.connect(self.refresh)
        session.tracksChanged.connect(self.refresh)
        session.trackChanged.connect(lambda _uid: self.refresh())
        session.settingsChanged.connect(self.refresh)
        session.batchProgress.connect(self._on_progress)
        session.batchFinished.connect(self._on_finished)
        self.refresh()

    def refresh(self) -> None:
        s = self.session
        busy = s.busy
        self.master.setEnabled(s.can_master)
        self.preview.setEnabled(s.can_master)
        self.preview.setVisible(not busy)
        self.busy_box.setVisible(busy)
        target = s.settings.target_lufs
        standard = "the streaming volume standard" if abs(target - DEFAULT_TARGET_LUFS) < 0.05 else "your loudness target"
        if not s.tracks:
            self.explain.setText("Add your songs to get started.")
        elif s.analyzing:
            self.explain.setText("Analyzing your files…")
        else:
            self.explain.setText(
                f"Matches every song to {standard} ({target:g} LUFS) and keeps peaks safe "
                f"({s.settings.ceiling_dbtp:g} dBTP).")
        self.explain.setVisible(not busy)
        show_outcome = bool(s.last_summary) and not busy and s.last_run_dry is not None and bool(s.tracks)
        if show_outcome:
            nothing_saved = " Nothing was saved yet." if s.last_run_dry and s.last_run_ok else ""
            self.outcome.setText(s.last_summary + nothing_saved)
            glyph, color_name = ("ph.check-circle-fill", "good") if s.last_run_ok else ("ph.warning-fill", "warn")
            self.outcome_icon.setPixmap(theme.icon(glyph, color_name).pixmap(QSize(18, 18)))
        self.outcome_box.setVisible(show_outcome)
        self.open_output.setVisible(show_outcome and not s.last_run_dry and bool(s.output_dirs))
        self.stale.setVisible(s.results_stale and not busy)

    def _on_progress(self, fraction: float, stage: str) -> None:
        self.progress.setValue(int(fraction * 1000))
        self.stage.setText(stage)

    def _on_finished(self, _dry_run: bool, _cancelled: bool, _summary: str) -> None:
        self.progress.setValue(0)
        self.refresh()


class SummaryCard(Card):
    """'What changed' for the selected track, in plain language."""

    def __init__(self, session: Session) -> None:
        super().__init__("What changed")
        self.session = session
        self.headline = label("", "heading", wrap=True)
        self.content = QVBoxLayout()
        self.content.setSpacing(8)
        self.body.addWidget(self.headline)
        self.body.addLayout(self.content)
        session.selectionChanged.connect(lambda _uid: self.refresh())
        session.trackChanged.connect(self._on_track_changed)
        session.settingsChanged.connect(self.refresh)
        self.refresh()

    def _on_track_changed(self, uid: int) -> None:
        if uid == self.session.selected_uid:
            self.refresh()

    def _clear(self) -> None:
        while self.content.count():
            item = self.content.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()          # deleteLater only runs on the next event loop pass
                widget.deleteLater()

    def _bullet(self, text: str) -> None:
        row = QHBoxLayout()
        row.setSpacing(8)
        dot = QLabel()
        dot.setPixmap(theme.icon("ph.check", "accent").pixmap(QSize(14, 14)))
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(label(text, wrap=True, name="BulletText"), 1)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self.content.addWidget(holder)

    def refresh(self) -> None:
        self._clear()
        track = self.session.selected()
        if track is None:
            self.headline.setText("Select a track to see what mastering changes.")
            return
        if track.status == "analyzing":
            self.headline.setText("Analyzing this track…")
            return
        if track.status == "failed":
            self.headline.setText("This file can’t be mastered.")
            self.content.addWidget(Notice(track.error, "bad"))
            return
        result = track.result
        if track.status == "processing":
            self.headline.setText(track.stage or "Processing…")
            return
        if result is not None and result.description is not None:
            d = result.description
            self.headline.setText(d.headline)
            for line in d.details:
                self._bullet(line)
            for line in d.warnings:
                self.content.addWidget(Notice(line, "warn"))
            if result.dry_run:
                self.content.addWidget(Notice("This is a preview: no file was saved yet.", "info"))
            return
        a = track.analysis
        target = self.session.settings.target_lufs
        lufs = a.measurements.lufs
        if math.isfinite(lufs):
            gap = target - lufs
            if abs(gap) < 0.5:
                self.headline.setText("Already at the right volume.")
            else:
                self.headline.setText("Mastering will raise its volume." if gap > 0 else "Mastering will lower its volume.")
            self._bullet(f"It currently plays at {lufs:.1f} LUFS. {loudness_phrase(lufs, target)} ({target:g} LUFS).")
        else:
            self.headline.setText("This track is silent or too short to measure.")
        if a.measurements.true_peak_db > 0:
            self._bullet(f"Its peaks reach {a.measurements.true_peak_db:+.1f} dBTP, above the digital maximum. "
                         "The limiter will bring them to a safe level.")
        warning = clipping_warning(a.clipping, a.fmt)
        if warning:
            self.content.addWidget(Notice(warning, "warn"))


class LoudnessCard(Card):
    """Big loudness readout, a plain-language verdict, and the meter."""

    def __init__(self, session: Session) -> None:
        super().__init__("Loudness", "loudness_meter")
        self.session = session
        self.value = label("—", "metric")
        self.value.setFont(theme.font("metric", tabular=True))
        self.unit = label("LUFS", "unit")
        self.verdict = label("", "muted", wrap=True)
        self.compare = label("", "faint", wrap=True)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.value, 0, Qt.AlignmentFlag.AlignBottom)
        top.addWidget(self.unit, 0, Qt.AlignmentFlag.AlignBottom)
        top.addSpacing(8)
        text = QVBoxLayout()
        text.setSpacing(2)
        text.addWidget(self.verdict)
        text.addWidget(self.compare)
        top.addLayout(text, 1)
        self.meter = LoudnessMeter()
        self.meter.setToolTip(tip("loudness_meter"))
        self.body.addLayout(top)
        self.body.addWidget(self.meter)
        session.selectionChanged.connect(lambda _uid: self.refresh())
        session.trackChanged.connect(lambda uid: uid == session.selected_uid and self.refresh())
        session.settingsChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        track = self.session.selected()
        target = self.session.settings.target_lufs
        before = track.analysis.measurements.lufs if track and track.analysis else None
        after = track.result.after.lufs if track and track.result and track.result.ok else None
        shown = after if after is not None else before
        self.value.setText(fmt_lufs(shown, unit=False))
        self.verdict.setText(loudness_phrase(shown, target) if track else "No track selected")
        if after is not None:
            self.compare.setText(f"Original {fmt_lufs(before)}  →  Mastered {fmt_lufs(after)}")
        elif before is not None:
            self.compare.setText(f"Original, before mastering · target {target:g} LUFS")
        else:
            self.compare.setText("")
        self.meter.set_values(before, after, target)


class TruePeakCard(Card):
    def __init__(self, session: Session) -> None:
        super().__init__("True peak", "true_peak_meter")
        self.session = session
        self.indicator = TruePeakIndicator()
        self.indicator.setToolTip(tip("true_peak"))
        self.body.addWidget(self.indicator)
        session.selectionChanged.connect(lambda _uid: self.refresh())
        session.trackChanged.connect(lambda uid: uid == session.selected_uid and self.refresh())
        session.settingsChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        track = self.session.selected()
        before = track.analysis.measurements.true_peak_db if track and track.analysis else None
        after = track.result.after.true_peak_db if track and track.result and track.result.ok else None
        self.indicator.set_values(before, after, self.session.settings.ceiling_dbtp)


class TrackHeader(QWidget):
    """Title line of the selected track: number, title and format chips."""

    def __init__(self, session: Session) -> None:
        super().__init__()
        self.session = session
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4, 0, 4, 0)
        layout.setSpacing(12)
        self.number = label("", name="TrackNumber")
        self.number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title = label("", "title")
        self.title.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.format = Chip("", "neutral")
        self.format.setToolTip(tip("format"))
        self.status = Chip()
        layout.addWidget(self.number)
        layout.addWidget(self.title, 1)
        layout.addWidget(self.format)
        layout.addWidget(self.status)
        session.selectionChanged.connect(lambda _uid: self.refresh())
        session.trackChanged.connect(lambda uid: uid == session.selected_uid and self.refresh())
        self.refresh()

    def refresh(self) -> None:
        track = self.session.selected()
        self.setVisible(track is not None)
        if track is None:
            return
        self.number.setText(str(track.track_number))
        self.title.setText(track.title or track.filename)
        self.title.setToolTip(track.path)
        self.format.setText(track.fmt.summary if track.fmt else "Reading format…")
        kind, text = status_chip(track)
        self.status.set_kind(kind, text)


# ---------------------------------------------------------------------------
# Advanced settings
# ---------------------------------------------------------------------------

BIT_DEPTH_LABELS = {"PCM_16": "16-bit (CD)", "PCM_24": "24-bit", "PCM_32": "32-bit", "FLOAT": "32-bit float"}


class AdvancedSettingsPanel(CollapsibleSection):
    """Every optional setting, closed by default so beginners aren't confronted with it."""

    hardwareToggled = Signal(bool)
    savePresetRequested = Signal()
    loadPresetRequested = Signal()

    def __init__(self, session: Session, hardware_acceleration: bool) -> None:
        super().__init__("Advanced Settings", expanded=False)
        self.session = session
        self._syncing = False
        c = self.content_layout

        # Loudness
        c.addWidget(label("LOUDNESS", name="GroupTitle"))
        form = self._form()
        self.target = QDoubleSpinBox()
        self.target.setRange(*TARGET_LUFS_RANGE)
        self.target.setSingleStep(0.5)
        self.target.setDecimals(1)
        self.target.setSuffix(" LUFS")
        self.target.setToolTip(tip("target_lufs"))
        self.target.valueChanged.connect(lambda v: self._set(target_lufs=float(v)))
        form.addRow(self._row_label("Target", "target_lufs"), self.target)
        self.ceiling = QDoubleSpinBox()
        self.ceiling.setRange(*CEILING_RANGE)
        self.ceiling.setSingleStep(0.1)
        self.ceiling.setDecimals(1)
        self.ceiling.setSuffix(" dBTP")
        self.ceiling.setToolTip(tip("ceiling"))
        self.ceiling.valueChanged.connect(lambda v: self._set(ceiling_dbtp=float(v)))
        form.addRow(self._row_label("Peak ceiling", "ceiling"), self.ceiling)
        c.addLayout(form)

        # Tone
        c.addWidget(label("TONE MATCHING", name="GroupTitle"))
        self.eq = QCheckBox("Match tone to a reference track")
        self.eq.setToolTip(tip("eq_match"))
        self.eq.toggled.connect(lambda on: self._set(eq_match=on))
        c.addWidget(self.eq)
        form = self._form()
        self.reference = QComboBox()
        self.reference.setToolTip(tip("eq_reference"))
        self.reference.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.reference.setMinimumContentsLength(12)
        self.reference.currentIndexChanged.connect(self._on_reference)
        form.addRow(self._row_label("Reference", "eq_reference"), self.reference)
        strength_row = QHBoxLayout()
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0, 100)
        self.strength.setToolTip(tip("eq_strength"))
        self.strength_value = label("", "muted")
        self.strength_value.setMinimumWidth(40)
        self.strength.valueChanged.connect(self._on_strength)
        strength_row.addWidget(self.strength, 1)
        strength_row.addWidget(self.strength_value)
        form.addRow(self._row_label("Strength", "eq_strength"), strength_row)
        c.addLayout(form)

        # Spacing
        c.addWidget(label("TRACK SPACING", name="GroupTitle"))
        self.spacing = QCheckBox("Even out gaps between tracks")
        self.spacing.setToolTip(tip("even_spacing"))
        self.spacing.toggled.connect(lambda on: self._set(even_spacing=on))
        c.addWidget(self.spacing)
        form = self._form()
        self.gap = QDoubleSpinBox()
        self.gap.setRange(*GAP_RANGE)
        self.gap.setSingleStep(0.1)
        self.gap.setDecimals(1)
        self.gap.setSuffix(" s")
        self.gap.setToolTip(tip("gap"))
        self.gap.valueChanged.connect(lambda v: self._set(gap_s=float(v)))
        form.addRow(self._row_label("Gap", "gap"), self.gap)
        c.addLayout(form)

        # Output
        c.addWidget(label("OUTPUT", name="GroupTitle"))
        self.force_depth = QCheckBox("Use the same bit depth for all files")
        self.force_depth.setToolTip(tip("bit_depth_override"))
        self.force_depth.toggled.connect(self._on_depth)
        c.addWidget(self.force_depth)
        form = self._form()
        self.depth = QComboBox()
        for subtype in OUTPUT_SUBTYPE_CHOICES:
            self.depth.addItem(BIT_DEPTH_LABELS[subtype], subtype)
        self.depth.setToolTip(tip("bit_depth_override"))
        self.depth.currentIndexChanged.connect(self._on_depth)
        form.addRow(self._row_label("Bit depth", "bit_depth_override"), self.depth)
        c.addLayout(form)
        self.beside = QRadioButton("Next to the originals, in a “mastered” folder")
        self.custom = QRadioButton("In a folder I choose")
        for radio in (self.beside, self.custom):
            radio.setToolTip(tip("output_folder"))
        self.beside.toggled.connect(lambda on: on and self._set(output_dir=None))
        self.custom.toggled.connect(self._on_custom_folder)
        c.addWidget(self.beside)
        c.addWidget(self.custom)
        folder_row = QHBoxLayout()
        folder_row.setContentsMargins(26, 0, 0, 0)
        self.folder_label = label("", "faint", wrap=True)
        self.folder_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.choose_folder = button("Choose…", "ghost", "ph.folder-open", "output_folder")
        self.choose_folder.clicked.connect(self._choose_folder)
        folder_row.addWidget(self.folder_label, 1)
        folder_row.addWidget(self.choose_folder)
        c.addLayout(folder_row)

        # Album details
        c.addWidget(label("ALBUM DETAILS", name="GroupTitle"))
        form = self._form()
        self.album = QLineEdit()
        self.album.setPlaceholderText("Album title")
        self.album.setToolTip(tip("album_details"))
        self.album.editingFinished.connect(lambda: self._set(album=self.album.text().strip()))
        form.addRow(self._row_label("Album", "album_details"), self.album)
        self.album_artist = QLineEdit()
        self.album_artist.setPlaceholderText("Artist name")
        self.album_artist.setToolTip(tip("album_details"))
        self.album_artist.editingFinished.connect(lambda: self._set(album_artist=self.album_artist.text().strip()))
        form.addRow(self._row_label("Artist", "album_details"), self.album_artist)
        c.addLayout(form)

        # Presets and display
        c.addWidget(label("PRESETS", name="GroupTitle"))
        preset_row = QHBoxLayout()
        save = button("Save Preset…", icon_name="ph.floppy-disk", tip_key="save_preset")
        load = button("Load Preset…", icon_name="ph.download-simple", tip_key="load_preset")
        save.clicked.connect(self.savePresetRequested)
        load.clicked.connect(self.loadPresetRequested)
        preset_row.addWidget(save)
        preset_row.addWidget(load)
        preset_row.addStretch(1)
        c.addLayout(preset_row)

        c.addWidget(label("DISPLAY", name="GroupTitle"))
        self.hardware = QCheckBox("Hardware acceleration")
        self.hardware.setToolTip(tip("hardware_acceleration"))
        self.hardware.setChecked(hardware_acceleration)
        self.hardware.toggled.connect(self.hardwareToggled)
        c.addWidget(self.hardware)
        reset = button("Reset to defaults", "link", tip_key="reset_settings")
        reset.clicked.connect(session.reset_settings)
        c.addWidget(reset, 0, Qt.AlignmentFlag.AlignLeft)

        session.settingsChanged.connect(self.refresh)
        session.tracksChanged.connect(self.refresh)
        session.trackChanged.connect(self._on_track_changed)
        session.busyChanged.connect(lambda busy: self.content.setEnabled(not busy))
        self.refresh()

    @staticmethod
    def _form() -> QFormLayout:
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        return form

    @staticmethod
    def _row_label(text: str, tip_key: str) -> QLabel:
        w = label(text, "muted")
        w.setToolTip(tip(tip_key))
        w.setMinimumWidth(88)
        return w

    def _set(self, **changes) -> None:
        if not self._syncing:
            self.session.update_settings(**changes)

    def _on_track_changed(self, _uid: int) -> None:
        # Titles can change after analysis (tags); keep the reference list readable.
        if self.reference.count() != len(self.session.tracks):
            self.refresh()
        else:
            for i, track in enumerate(self.session.tracks):
                text = f"{track.track_number}. {track.title}"
                if self.reference.itemText(i) != text:
                    self.reference.setItemText(i, text)

    def _on_reference(self, index: int) -> None:
        path = self.reference.itemData(index)
        if path:
            self._set(eq_reference=path)

    def _on_strength(self, value: int) -> None:
        self.strength_value.setText(f"{value}%")
        self._set(eq_strength=value / 100.0)

    def _on_depth(self, *_args) -> None:
        self.depth.setEnabled(self.force_depth.isChecked())
        self._set(output_subtype=self.depth.currentData() if self.force_depth.isChecked() else None)

    def _on_custom_folder(self, on: bool) -> None:
        self.choose_folder.setEnabled(on)
        if on and not self._syncing and not self.session.settings.output_dir:
            self._choose_folder()

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Choose Output Folder", self.session.settings.output_dir or "")
        if folder:
            self._set(output_dir=folder)
        elif not self.session.settings.output_dir:
            self.beside.setChecked(True)

    def refresh(self) -> None:
        s = self.session.settings
        self._syncing = True
        self.target.setValue(s.target_lufs)
        self.ceiling.setValue(s.ceiling_dbtp)
        self.eq.setChecked(s.eq_match)
        self.reference.clear()
        for track in self.session.tracks:
            self.reference.addItem(f"{track.track_number}. {track.title}", track.path)
        ref = self.session.reference_track()
        if ref is not None:
            self.reference.setCurrentIndex(self.session.index_of(ref.uid))
        self.reference.setEnabled(s.eq_match and bool(self.session.tracks))
        self.strength.setValue(int(round(s.eq_strength * 100)))
        self.strength_value.setText(f"{self.strength.value()}%")
        self.strength.setEnabled(s.eq_match)
        self.spacing.setChecked(s.even_spacing)
        self.gap.setValue(s.gap_s)
        self.gap.setEnabled(s.even_spacing)
        self.force_depth.setChecked(s.output_subtype is not None)
        if s.output_subtype:
            self.depth.setCurrentIndex(max(0, self.depth.findData(s.output_subtype)))
        self.depth.setEnabled(s.output_subtype is not None)
        self.custom.setChecked(bool(s.output_dir))
        self.beside.setChecked(not s.output_dir)
        self.choose_folder.setEnabled(bool(s.output_dir))
        self.folder_label.setText(s.output_dir or "Each file’s own folder › mastered")
        self.folder_label.setToolTip(s.output_dir or "")
        if not self.album.hasFocus():
            self.album.setText(s.album)
        if not self.album_artist.hasFocus():
            self.album_artist.setText(s.album_artist)
        self._syncing = False

    def set_hardware_checked(self, on: bool) -> None:
        self.hardware.blockSignals(True)
        self.hardware.setChecked(on)
        self.hardware.blockSignals(False)


# ---------------------------------------------------------------------------
# Player
# ---------------------------------------------------------------------------

class PlayerCard(Card):
    """Transport controls with a seamless Original / Mastered switch."""

    def __init__(self, engine: PlaybackEngine, session: Session) -> None:
        super().__init__("Listen", "player")
        self.engine = engine
        self.session = session
        self.play = button("", "round", tip_key="play")
        self.play.setIconSize(QSize(18, 18))
        self.play.clicked.connect(engine.toggle)
        self.stop = button("", "icon", "ph.stop-fill", "stop")
        self.stop.clicked.connect(engine.stop)
        self.time = label("0:00 / 0:00", "muted")
        self.time.setFont(theme.font("small", tabular=True))
        self.ab = SegmentedControl([("original", "Original"), ("mastered", "Mastered")], "ab_toggle")
        self.ab.changed.connect(engine.set_version)
        self.header.addWidget(self.ab)
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(self.play)
        top.addWidget(self.stop)
        top.addWidget(self.time, 1)
        # A click anywhere on the bar jumps there (see theme.AppStyle), then dragging continues.
        self.position = QSlider(Qt.Orientation.Horizontal)
        self.position.setRange(0, 1000)
        self.position.setToolTip(tip("seek"))
        self.position.sliderPressed.connect(self._seek_from_slider)
        self.position.sliderMoved.connect(lambda _value: self._seek_from_slider())
        self.match = QCheckBox("Compare at equal volume")
        self.match.setChecked(engine.level_match)
        self.match.setToolTip(tip("level_match"))
        self.match.toggled.connect(engine.set_level_match)
        self.match.toggled.connect(lambda _on: self._update_hint())
        speaker = QLabel()
        speaker.setPixmap(theme.icon("ph.speaker-high", "text_faint").pixmap(QSize(16, 16)))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(engine.volume * 100))
        self.volume.setMaximumWidth(112)
        self.volume.setToolTip(tip("volume"))
        self.volume.valueChanged.connect(lambda v: engine.set_volume(v / 100.0))
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        bottom.addWidget(self.match, 1)
        bottom.addWidget(speaker)
        bottom.addWidget(self.volume)
        # Explains why the versions can sound alike once their volume is matched.
        self.hint = label("", "faint", wrap=True)
        self.hint.hide()
        self.body.addLayout(top)
        self.body.addWidget(self.position)
        self.body.addLayout(bottom)
        self.body.addWidget(self.hint)
        engine.stateChanged.connect(self._on_state)
        engine.sourceChanged.connect(self._on_source)
        engine.durationChanged.connect(lambda _d: self.update_position())
        self._on_state(False)
        self._on_source()

    def _on_state(self, playing: bool) -> None:
        self.play.setIcon(theme.icon("ph.pause-fill" if playing else "ph.play-fill", "accent_text"))
        self.update_position()

    def _on_source(self) -> None:
        ready = self.engine.ready
        for w in (self.play, self.stop, self.position):
            w.setEnabled(ready)
        self.ab.set_enabled_key("mastered", self.engine.has_mastered())
        self.ab.set_current(self.engine.version)
        self.ab.setToolTip(tip("ab_toggle" if self.engine.has_mastered() else "ab_unavailable"))
        self._update_hint()
        self.update_position()

    def _update_hint(self) -> None:
        difference = self.engine.matched_difference_db
        key = None
        if self.engine.level_match and difference is not None:
            key = "level_match_identical" if difference < -40 else "level_match_subtle" if difference < -20 else None
        self.hint.setText(TIPS[key][1] if key else "")
        self.hint.setVisible(key is not None)

    def _seek_from_slider(self) -> None:
        duration = self.engine.duration()
        if duration > 0:
            self.engine.seek(self.position.value() / 1000.0 * duration)

    def update_position(self) -> None:
        duration = self.engine.duration()
        position = self.engine.position()
        self.time.setText(f"{format_duration(position)} / {format_duration(duration)}")
        if not self.position.isSliderDown() and duration > 0:
            self.position.setValue(int(min(1.0, position / duration) * 1000))
