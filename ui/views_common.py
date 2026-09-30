"""Pieces shared by the Simple and Advanced layouts: the visualisation cards
and the logic that feeds them from the session and the player."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from .session import Session
from .visualizers import BatchComparisonView, SpectrumView, WaveformView
from .playback import PlaybackEngine
from .widgets import Card, Chip


class VisualPanels(QObject):
    """Waveform, spectrum and album-comparison cards for one layout.

    Each layout owns its own instance; both are fed from the same session and
    player, so switching modes shows the same state.
    """

    visibilityChanged = Signal(str, bool)

    def __init__(self, session: Session, engine: PlaybackEngine, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.session = session
        self.engine = engine

        self.waveform = WaveformView()
        self.waveform.setToolTip("")
        self.waveform_card = Card("Waveform", "waveform")
        self.waveform_card.header.addWidget(Chip("Original", "before"))
        self.waveform_card.header.addWidget(Chip("Mastered", "after"))
        self.waveform_card.body.addWidget(self.waveform)
        self.waveform.seekRequested.connect(self._seek)

        self.spectrum = SpectrumView()
        self.spectrum_card = Card("Tone analyzer", "spectrum")
        self.spectrum_card.header.addWidget(Chip("Original", "before"))
        self.spectrum_card.header.addWidget(Chip("Mastered", "after"))
        self.reference_chip = Chip("Reference", "reference")
        self.spectrum_card.header.addWidget(self.reference_chip)
        self.live_chip = Chip("Live", "live")
        self.spectrum_card.header.addWidget(self.live_chip)
        self.live_chip.hide()
        self.spectrum_card.body.addWidget(self.spectrum)

        self.comparison = BatchComparisonView()
        self.comparison_card = Card("Album loudness", "batch_comparison")
        self.comparison_card.body.addWidget(self.comparison)
        self.comparison.trackClicked.connect(session.select)

        self.cards: dict[str, QWidget] = {
            "waveform": self.waveform_card,
            "spectrum": self.spectrum_card,
            "comparison": self.comparison_card,
        }
        session.selectionChanged.connect(lambda _uid: self.refresh_all())
        session.trackChanged.connect(self._on_track_changed)
        session.tracksChanged.connect(self.refresh_all)
        session.settingsChanged.connect(self.refresh_all)
        engine.stateChanged.connect(lambda playing: self.live_chip.setVisible(playing))
        self.refresh_all()

    def set_visible(self, name: str, visible: bool) -> None:
        self.cards[name].setVisible(visible)
        self.visibilityChanged.emit(name, visible)

    def is_visible(self, name: str) -> bool:
        return not self.cards[name].isHidden()

    def _seek(self, seconds: float) -> None:
        if self.engine.track is not None:
            self.engine.seek(seconds)

    def _on_track_changed(self, uid: int) -> None:
        if uid == self.session.selected_uid:
            self.refresh_track()
        self.refresh_comparison()

    def refresh_all(self) -> None:
        self.refresh_track()
        self.refresh_comparison()

    def refresh_track(self) -> None:
        track = self.session.selected()
        before = track.analysis if track else None
        result = track.result if track and track.result and track.result.ok else None
        offset = 0.0
        if result is not None and result.silence is not None:
            offset = result.silence.lead_before_s - result.silence.lead_after_s
        self.waveform.set_data(
            before.overview if before else None,
            result.overview_after if result else None,
            offset,
            self.session.settings.ceiling_dbtp if result else None,
        )
        ref_track = self.session.reference_track() if self.session.settings.eq_match else None
        reference = ref_track.analysis.spectrum if ref_track and ref_track.analysis and ref_track is not track else None
        self.reference_chip.setVisible(reference is not None)
        self.spectrum.set_profiles(before.spectrum if before else None,
                                   result.spectrum_after if result else None, reference)

    def refresh_comparison(self) -> None:
        rows = []
        for t in self.session.tracks:
            if t.analysis is None:
                continue
            after = t.result.after.lufs if t.result and t.result.ok else None
            rows.append((t.uid, t.track_number, t.title, t.analysis.measurements.lufs, after))
        self.comparison.set_tracks(rows, self.session.settings.target_lufs, self.session.selected_uid)

    def on_frame(self, dt: float) -> None:
        """Called on every display frame while audio plays."""
        if self.waveform_card.isVisible():
            self.waveform.set_playhead(self.engine.position() if self.engine.track else None)
        if self.spectrum_card.isVisible():
            block, sr = self.engine.recent_block()
            self.spectrum.update_live(block, sr, dt)

    def on_stopped(self) -> None:
        self.spectrum.update_live(None, 0, 0.0)
        self.waveform.set_playhead(self.engine.position() if self.engine.track and self.engine.position() > 0 else None)
