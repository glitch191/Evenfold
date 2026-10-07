"""Advanced mode: every meter, the tone analyzer, the album overview, the
player, editable track details, detailed results and all settings, in
resizable zones."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QScrollArea, QSplitter, QStackedWidget, QVBoxLayout, QWidget

from . import theme
from .playback import PlaybackEngine
from .session import Session
from .settings import AppSettings
from .views_common import VisualPanels
from .widgets import (
    AdvancedSettingsPanel,
    Card,
    DropZone,
    LoudnessCard,
    MasterPanel,
    PlayerCard,
    ResultsTable,
    SummaryCard,
    TrackTable,
    TruePeakCard,
    button,
    wheel_scrolls_past_controls,
)

MODE = "advanced"
DEFAULT_SIZES = {
    "advanced/main": [600, 880, 408],
    "advanced/center": [360, 300, 280],
    "advanced/lower": [600, 440],
}


class AdvancedView(QWidget):
    browseRequested = Signal()
    filesDropped = Signal(list)
    openOutputRequested = Signal()
    exportReportRequested = Signal()

    def __init__(self, session: Session, engine: PlaybackEngine, app_settings: AppSettings,
                 hardware_acceleration: bool) -> None:
        super().__init__()
        self.session = session
        self.app_settings = app_settings
        self.panels = VisualPanels(session, engine, self)
        space = theme.SPACE

        root = QHBoxLayout(self)
        root.setContentsMargins(space["md"], space["md"], space["md"], space["md"])
        root.setSpacing(0)
        self.main_split = QSplitter(Qt.Orientation.Horizontal)
        self.main_split.setChildrenCollapsible(False)
        root.addWidget(self.main_split)

        # Tracks
        tracks = Card("Tracks")
        add = button("Add Files", "ghost", "ph.plus", "add_files")
        add.clicked.connect(self.browseRequested)
        tracks.header.addWidget(add)
        self.table = TrackTable(session)
        tracks.body.addWidget(self.table, 1)
        drop = DropZone(compact=True)
        drop.browseRequested.connect(self.browseRequested)
        drop.filesDropped.connect(self.filesDropped)
        tracks.body.addWidget(drop)
        tracks.setMinimumWidth(360)
        self.main_split.addWidget(tracks)

        # Visualisations and results
        self.center = QStackedWidget()
        big_drop = DropZone()
        big_drop.browseRequested.connect(self.browseRequested)
        big_drop.filesDropped.connect(self.filesDropped)
        self.center.addWidget(big_drop)
        self.center_split = QSplitter(Qt.Orientation.Vertical)
        self.center_split.setChildrenCollapsible(False)
        self.center_split.addWidget(self.panels.waveform_card)
        self.lower_split = QSplitter(Qt.Orientation.Horizontal)
        self.lower_split.setChildrenCollapsible(False)
        self.lower_split.addWidget(self.panels.spectrum_card)
        self.lower_split.addWidget(self.panels.comparison_card)
        self.center_split.addWidget(self.lower_split)
        results = Card("Detailed results", "results_table")
        export = button("Export Report…", "ghost", "ph.export", "export_report")
        export.clicked.connect(self.exportReportRequested)
        results.header.addWidget(export)
        results.body.addWidget(ResultsTable(session))
        results.setMinimumHeight(160)
        self.center_split.addWidget(results)
        self.center.addWidget(self.center_split)
        self.center.setMinimumWidth(480)
        wrapper = QWidget()
        w = QVBoxLayout(wrapper)
        w.setContentsMargins(space["md"], 0, space["md"], 0)
        w.addWidget(self.center)
        self.main_split.addWidget(wrapper)

        # Controls
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(theme.SIDE_PANEL_WIDTH + theme.SPACE["lg"])
        column = QWidget()
        column.setObjectName("ScrollBody")
        c = QVBoxLayout(column)
        c.setContentsMargins(0, 0, space["sm"], 0)
        c.setSpacing(space["md"])
        self.master = MasterPanel(session)
        self.master.openOutputRequested.connect(self.openOutputRequested)
        c.addWidget(self.master)
        self.loudness = LoudnessCard(session)
        c.addWidget(self.loudness)
        c.addWidget(TruePeakCard(session))
        self.player = PlayerCard(engine, session)
        c.addWidget(self.player)
        c.addWidget(SummaryCard(session))
        settings_card = Card()
        self.settings_panel = AdvancedSettingsPanel(session, hardware_acceleration)
        settings_card.body.addWidget(self.settings_panel)
        c.addWidget(settings_card)
        c.addStretch(1)
        scroll.setWidget(column)
        wheel_scrolls_past_controls(scroll)
        self.main_split.addWidget(scroll)
        self.main_split.setStretchFactor(1, 1)

        session.tracksChanged.connect(self._on_tracks)
        self._on_tracks()
        self.restore_layout()

    def _on_tracks(self) -> None:
        self.center.setCurrentIndex(1 if self.session.tracks else 0)

    # --- layout persistence -------------------------------------------------
    def _sync_lower(self) -> None:
        self.lower_split.setVisible(self.panels.is_visible("spectrum") or self.panels.is_visible("comparison"))

    def set_panel_visible(self, name: str, visible: bool) -> None:
        self.panels.set_visible(name, visible)
        self.app_settings.set_panel_visible(MODE, name, visible)
        self._sync_lower()

    def _splitters(self) -> dict[str, QSplitter]:
        return {"advanced/main": self.main_split, "advanced/center": self.center_split,
                "advanced/lower": self.lower_split}

    def restore_layout(self) -> None:
        for name in self.panels.cards:
            self.panels.set_visible(name, self.app_settings.panel_visible(MODE, name))
        self._sync_lower()
        for key, splitter in self._splitters().items():
            state = self.app_settings.splitter_state(key)
            if state is None or not splitter.restoreState(state):
                splitter.setSizes(DEFAULT_SIZES[key])

    def save_layout(self) -> None:
        for key, splitter in self._splitters().items():
            self.app_settings.set_splitter_state(key, splitter.saveState())
