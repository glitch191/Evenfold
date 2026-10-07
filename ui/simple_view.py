"""Simple mode: drop files, click one button, read the result in plain words.

Only the track list, the waveform and the loudness meter are shown by default.
The tone analyzer and the album overview can be turned on from the View menu.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from . import theme
from .playback import PlaybackEngine
from .session import Session
from .settings import AppSettings
from .views_common import VisualPanels
from .widgets import (
    DropZone,
    LoudnessCard,
    MasterPanel,
    SummaryCard,
    TrackHeader,
    TrackList,
    button,
    label,
    wheel_scrolls_past_controls,
)

MODE = "simple"


class SimpleView(QWidget):
    browseRequested = Signal()
    filesDropped = Signal(list)
    openOutputRequested = Signal()

    def __init__(self, session: Session, engine: PlaybackEngine, app_settings: AppSettings) -> None:
        super().__init__()
        self.session = session
        self.app_settings = app_settings
        self.panels = VisualPanels(session, engine, self)
        space = theme.SPACE

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Album sidebar
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(theme.SIDEBAR_WIDTH)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(space["md"], space["lg"], space["md"], space["md"])
        side.setSpacing(space["sm"] + 4)
        head = QHBoxLayout()
        head.setSpacing(space["sm"])
        titles = QVBoxLayout()
        titles.setSpacing(2)
        titles.addWidget(label("Your album", "heading"))
        self.count = label("", "faint")
        titles.addWidget(self.count)
        head.addLayout(titles, 1)
        add = button("Add Files", icon_name="ph.plus", tip_key="add_files")
        add.clicked.connect(self.browseRequested)
        head.addWidget(add, 0, Qt.AlignmentFlag.AlignTop)
        side.addLayout(head)
        self.list_stack = QStackedWidget()
        hint_page = QWidget()
        hint = QVBoxLayout(hint_page)
        hint.setContentsMargins(0, space["sm"], 0, 0)
        hint.addWidget(label("Drop your WAV files anywhere in this window, or click Add Files.", "faint", wrap=True))
        hint.addStretch(1)
        self.list_stack.addWidget(hint_page)
        self.tracks = TrackList(session)
        self.list_stack.addWidget(self.tracks)
        side.addWidget(self.list_stack, 1)
        self.more_drop = DropZone(compact=True)
        self.more_drop.browseRequested.connect(self.browseRequested)
        self.more_drop.filesDropped.connect(self.filesDropped)
        side.addWidget(self.more_drop)
        root.addWidget(sidebar)

        # Selected track
        self.center = QStackedWidget()
        big_drop = DropZone()
        big_drop.browseRequested.connect(self.browseRequested)
        big_drop.filesDropped.connect(self.filesDropped)
        empty_page = QWidget()
        empty = QVBoxLayout(empty_page)
        empty.setContentsMargins(space["lg"], space["lg"], space["lg"], space["lg"])
        empty.addWidget(big_drop)
        self.center.addWidget(empty_page)

        detail = QWidget()
        d = QVBoxLayout(detail)
        d.setContentsMargins(space["lg"], space["lg"], space["lg"], space["lg"])
        d.setSpacing(space["md"])
        d.addWidget(TrackHeader(session))
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.setChildrenCollapsible(False)
        for name in ("waveform", "spectrum", "comparison"):
            self.splitter.addWidget(self.panels.cards[name])
        d.addWidget(self.splitter, 1)
        self.loudness = LoudnessCard(session)
        d.addWidget(self.loudness)
        self.center.addWidget(detail)
        root.addWidget(self.center, 1)

        # Action column
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(theme.SIDE_PANEL_WIDTH + space["lg"])
        column = QWidget()
        column.setObjectName("ScrollBody")
        c = QVBoxLayout(column)
        c.setContentsMargins(0, space["lg"], space["lg"], space["lg"])
        c.setSpacing(space["md"])
        self.master = MasterPanel(session)
        self.master.openOutputRequested.connect(self.openOutputRequested)
        c.addWidget(self.master)
        c.addWidget(SummaryCard(session))
        c.addStretch(1)
        scroll.setWidget(column)
        wheel_scrolls_past_controls(scroll)
        root.addWidget(scroll)

        session.tracksChanged.connect(self._on_tracks)
        self._on_tracks()
        self.restore_layout()

    def _on_tracks(self) -> None:
        n = len(self.session.tracks)
        self.count.setText("No tracks yet" if n == 0 else f"{n} track{'s' if n != 1 else ''}")
        self.list_stack.setCurrentIndex(1 if n else 0)
        self.more_drop.setVisible(n > 0)
        self.center.setCurrentIndex(1 if n else 0)

    # --- layout persistence -------------------------------------------------
    def set_panel_visible(self, name: str, visible: bool) -> None:
        self.panels.set_visible(name, visible)
        self.app_settings.set_panel_visible(MODE, name, visible)

    def restore_layout(self) -> None:
        for name in self.panels.cards:
            self.panels.set_visible(name, self.app_settings.panel_visible(MODE, name))
        state = self.app_settings.splitter_state("simple/center")
        if state is None or not self.splitter.restoreState(state):
            self.splitter.setSizes([520, 300, 260])

    def save_layout(self) -> None:
        self.app_settings.set_splitter_state("simple/center", self.splitter.saveState())
