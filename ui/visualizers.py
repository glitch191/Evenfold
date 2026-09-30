"""Visualisations: waveform, spectrum, album loudness bars (pyqtgraph) and the
loudness / true-peak meters (painted directly with QPainter).

Plots render through OpenGL by default. `ThemedPlot.set_hardware_acceleration`
switches every live plot between OpenGL and software rendering at runtime.
Animated elements (playhead, live meter, live spectrum) are driven by
`RenderClock`, which ticks at the refresh rate of the screen the window is on.
"""

from __future__ import annotations

import math
import weakref

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QElapsedTimer, QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QFont, QPainter, QPainterPath, QPen, QScreen
from PySide6.QtWidgets import QFrame, QGraphicsPathItem, QLabel, QSizePolicy, QStackedWidget, QToolTip, QVBoxLayout, QWidget

from audio.analysis import FLOOR_DB, SpectrumProfile, WaveformOverview, band_power_to_grid, format_duration, spectrum_grid

from . import theme
from .tooltips import album_bar_tip


# ---------------------------------------------------------------------------
# Render clock and pyqtgraph configuration
# ---------------------------------------------------------------------------

class RenderClock(QObject):
    """Frame clock synchronised to the refresh rate of the window's screen.

    Runs only while something is animating (for example during playback), so
    an idle window costs nothing.
    """

    tick = Signal(float)            # seconds since the previous frame
    rateChanged = Signal(float)     # screen refresh rate in Hz

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.timeout.connect(self._on_timeout)
        self._elapsed = QElapsedTimer()
        self._owners: set[str] = set()
        self._screen: QScreen | None = None
        self.rate = 60.0
        self._apply_rate(60.0)

    def attach(self, window: QWidget) -> None:
        handle = window.windowHandle()
        if handle is not None:
            handle.screenChanged.connect(self._set_screen)
            self._set_screen(handle.screen())

    def _set_screen(self, screen: QScreen | None) -> None:
        if self._screen is not None:
            try:
                self._screen.refreshRateChanged.disconnect(self._apply_rate)
            except (RuntimeError, TypeError):
                pass
        self._screen = screen
        if screen is not None:
            screen.refreshRateChanged.connect(self._apply_rate)
            self._apply_rate(screen.refreshRate())

    def _apply_rate(self, rate: float) -> None:
        self.rate = float(rate) if rate and rate > 1 else 60.0
        # Rounded down so the timer never runs slower than the display.
        self._timer.setInterval(max(1, int(1000.0 / self.rate)))
        self.rateChanged.emit(self.rate)

    def request(self, owner: str) -> None:
        self._owners.add(owner)
        if not self._timer.isActive():
            self._elapsed.start()
            self._timer.start()

    def release(self, owner: str) -> None:
        self._owners.discard(owner)
        if not self._owners:
            self._timer.stop()

    def _on_timeout(self) -> None:
        dt = self._elapsed.restart() / 1000.0
        self.tick.emit(dt)


_hardware = True


def _tolerate_gl_teardown() -> None:
    """pyqtgraph's per-curve GL state can be deleted before its context says
    goodbye (when a GL viewport is swapped out or the app quits); its cleanup
    then raises on an already-deleted object. Nothing is left to clean, so the
    error is ignored instead of being printed."""
    from pyqtgraph.graphicsItems import PlotCurveItem as curve_module

    state = getattr(curve_module, "OpenGLState", None)
    if state is None or getattr(state, "_tolerant", False):
        return
    original = state.cleanup

    def cleanup(self) -> None:
        try:
            original(self)
        except RuntimeError:
            pass

    state.cleanup = cleanup
    state._tolerant = True


_tolerate_gl_teardown()


def configure_pyqtgraph(hardware_acceleration: bool) -> None:
    global _hardware
    _hardware = hardware_acceleration
    pg.setConfigOptions(
        antialias=True,
        useOpenGL=hardware_acceleration,
        background=theme.COLORS["plot_bg"],
        foreground=theme.COLORS["text_muted"],
    )


def _pen(name: str, width: float = 1.0, alpha: float | None = None, style=Qt.PenStyle.SolidLine) -> QPen:
    pen = QPen(theme.color(name, alpha), width)
    pen.setCosmetic(True)
    pen.setStyle(style)
    return pen


class TimeAxis(pg.AxisItem):
    def tickStrings(self, values, scale, spacing):
        return [format_duration(v) if v >= 0 else "" for v in values]


class ThemedPlot(pg.PlotWidget):
    """PlotWidget with the app theme, no pyqtgraph chrome, and switchable OpenGL."""

    _instances: "weakref.WeakSet[ThemedPlot]" = weakref.WeakSet()

    def __init__(self, parent: QWidget | None = None, axis_items: dict | None = None) -> None:
        super().__init__(parent, background=theme.COLORS["plot_bg"], axisItems=axis_items or {})
        ThemedPlot._instances.add(self)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.useOpenGL(_hardware)
        item = self.getPlotItem()
        item.hideButtons()
        item.setMenuEnabled(False)
        item.layout.setContentsMargins(4, 4, 8, 4)
        for name in ("left", "bottom", "right", "top"):
            axis = item.getAxis(name)
            axis.setPen(_pen("border_strong"))
            axis.setTextPen(_pen("text_faint"))
            axis.setStyle(tickFont=theme.font("caption"), tickLength=-4, tickTextOffset=6)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(96)

    @classmethod
    def set_hardware_acceleration(cls, enabled: bool) -> bool:
        """Switch all live plots between OpenGL and software rendering. False if it failed."""
        configure_pyqtgraph(enabled)
        ok = True
        for plot in list(cls._instances):
            try:
                plot.useOpenGL(enabled)
            except Exception:
                ok = False
        return ok

    @staticmethod
    def using_opengl() -> bool:
        return _hardware


# ---------------------------------------------------------------------------
# Waveform
# ---------------------------------------------------------------------------

def _envelope_path(times: np.ndarray, high: np.ndarray, low: np.ndarray) -> QPainterPath:
    x = np.concatenate((times, times[::-1])).astype(np.float64)
    y = np.concatenate((high, low[::-1])).astype(np.float64)
    path = pg.arrayToQPath(x, y, connect="all")
    path.closeSubpath()
    return path


class _WaveformLane(QWidget):
    """One waveform (original or mastered) with its playhead and empty state."""

    clicked = Signal(float)

    def __init__(self, series: str, placeholder: str, show_time_axis: bool) -> None:
        super().__init__()
        self._series = series
        self.plot = ThemedPlot(axis_items={"bottom": TimeAxis("bottom")})
        item = self.plot.getPlotItem()
        item.hideAxis("left")
        if not show_time_axis:
            item.hideAxis("bottom")
        vb = item.getViewBox()
        vb.setMouseEnabled(x=True, y=False)
        vb.setYRange(-1.08, 1.08, padding=0)
        vb.enableAutoRange(False)
        self.plot.showGrid(x=False, y=False)

        self._peak = QGraphicsPathItem()
        self._peak.setBrush(QBrush(theme.color(series, 0.45)))
        self._peak.setPen(QPen(Qt.PenStyle.NoPen))
        self._rms = QGraphicsPathItem()
        self._rms.setBrush(QBrush(theme.color(series, 0.95)))
        self._rms.setPen(QPen(Qt.PenStyle.NoPen))
        for path_item in (self._peak, self._rms):
            path_item.setCacheMode(QGraphicsPathItem.CacheMode.DeviceCoordinateCache)
            item.addItem(path_item)

        self._centre = pg.InfiniteLine(pos=0, angle=0, pen=_pen("grid"))
        self._full_scale = [pg.InfiniteLine(pos=s, angle=0, pen=_pen("bad", 1, 0.35, Qt.PenStyle.DotLine)) for s in (1.0, -1.0)]
        self._ceiling = [pg.InfiniteLine(pos=0, angle=0, pen=_pen("accent", 1, 0.55, Qt.PenStyle.DashLine)) for _ in range(2)]
        self.playhead = pg.InfiniteLine(pos=0, angle=90, pen=_pen("live", 1.5))
        for line in (self._centre, *self._full_scale, *self._ceiling, self.playhead):
            item.addItem(line)
        for line in self._ceiling:
            line.hide()
        self.playhead.hide()

        self._empty = QLabel(placeholder)
        self._empty.setProperty("role", "faint")
        self._empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty.setWordWrap(True)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.plot)
        self._stack.addWidget(self._empty)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._stack)

        self.plot.scene().sigMouseClicked.connect(self._on_click)
        self._duration = 0.0

    def set_overview(self, overview: WaveformOverview | None, offset_s: float = 0.0,
                     ceiling_db: float | None = None) -> None:
        if overview is None or overview.times.size < 2:
            self._peak.setPath(QPainterPath())
            self._rms.setPath(QPainterPath())
            self._stack.setCurrentWidget(self._empty)
            self._duration = 0.0
            return
        times = overview.times + offset_s
        self._peak.setPath(_envelope_path(times, overview.high, overview.low))
        self._rms.setPath(_envelope_path(times, overview.rms, -overview.rms))
        self._duration = float(times[-1])
        if ceiling_db is not None:
            level = 10 ** (ceiling_db / 20)
            for line, sign in zip(self._ceiling, (1, -1)):
                line.setPos(sign * level)
                line.show()
        self._stack.setCurrentWidget(self.plot)

    def set_x_limits(self, start: float, end: float) -> None:
        vb = self.plot.getPlotItem().getViewBox()
        vb.setLimits(xMin=start - 0.25, xMax=end + 0.25, minXRange=0.05)
        vb.setXRange(start, end, padding=0.005)

    def _on_click(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        vb = self.plot.getPlotItem().getViewBox()
        if event.double():
            self.clicked.emit(-1.0)
            return
        point = vb.mapSceneToView(event.scenePos())
        self.clicked.emit(max(0.0, float(point.x())))


class WaveformView(QWidget):
    """Original (top) and mastered (bottom) waveforms on a shared, zoomable time axis.

    The mastered waveform is shifted so the music lines up with the original
    even when silence at the start was trimmed or extended. Times given to and
    emitted by this view are on the original's timeline.
    """

    seekRequested = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.before = _WaveformLane("before", "Add a track to see its waveform.", show_time_axis=False)
        self.after = _WaveformLane("after", "Master or preview your album to see the result here.", show_time_axis=True)
        self.after.plot.setXLink(self.before.plot)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE["xs"])
        layout.addWidget(self.before, 1)
        layout.addWidget(self.after, 1)
        for lane in (self.before, self.after):
            lane.clicked.connect(self._on_lane_clicked)
        self._span = (0.0, 1.0)

    def set_data(self, before: WaveformOverview | None, after: WaveformOverview | None,
                 after_offset_s: float = 0.0, ceiling_db: float | None = None) -> None:
        self.before.set_overview(before)
        self.after.set_overview(after, after_offset_s, ceiling_db)
        ends = [o.duration_s for o in (before,) if o is not None]
        if after is not None:
            ends.append(after.duration_s + after_offset_s)
        start = min(0.0, after_offset_s) if after is not None else 0.0
        self._span = (start, max(ends) if ends else 1.0)
        self.before.set_x_limits(*self._span)
        self.after.set_x_limits(*self._span)

    def set_playhead(self, seconds: float | None) -> None:
        for lane in (self.before, self.after):
            if seconds is None:
                lane.playhead.hide()
            else:
                lane.playhead.setPos(seconds)
                lane.playhead.show()

    def _on_lane_clicked(self, seconds: float) -> None:
        if seconds < 0:
            self.before.set_x_limits(*self._span)
        else:
            self.seekRequested.emit(seconds)


# ---------------------------------------------------------------------------
# Spectrum
# ---------------------------------------------------------------------------

_FREQ_TICKS = [(20, "20"), (50, "50"), (100, "100"), (200, "200"), (500, "500"), (1000, "1k"),
               (2000, "2k"), (5000, "5k"), (10000, "10k"), (20000, "20k")]


class SpectrumView(QWidget):
    """Long-term spectrum of the original, mastered and reference tracks, plus a
    live analyzer while audio plays."""

    LIVE_FFT_SECONDS = 0.093          # about 4096 samples at 44.1 kHz
    LIVE_FALL_DB_PER_S = 36.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plot = ThemedPlot()
        item = self.plot.getPlotItem()
        item.setLogMode(x=True, y=False)
        item.getAxis("bottom").setTicks([[(math.log10(f), label) for f, label in _FREQ_TICKS]])
        item.getAxis("left").setWidth(36)
        item.getAxis("left").setLabel("")
        vb = item.getViewBox()
        vb.setMouseEnabled(x=False, y=False)
        vb.setXRange(math.log10(20), math.log10(20000), padding=0)
        vb.enableAutoRange(False)
        item.showGrid(x=True, y=True, alpha=0.08)

        self.live = item.plot(pen=_pen("live", 1.2), fillLevel=-200, brush=theme.color("live", 0.10))
        self.before_curve = item.plot(pen=_pen("before", 1.5))
        self.reference = item.plot(pen=_pen("reference", 1.5, None, Qt.PenStyle.DashLine))
        self.after_curve = item.plot(pen=_pen("after", 2.0))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)
        self._live_db: np.ndarray | None = None
        self._live_grid: np.ndarray | None = None

    def set_profiles(self, before: SpectrumProfile | None, after: SpectrumProfile | None,
                     reference: SpectrumProfile | None) -> None:
        tops = []
        for curve, profile in ((self.before_curve, before), (self.after_curve, after)):
            if profile is None:
                curve.setData([], [])
            else:
                curve.setData(profile.freqs, profile.level_db)
                tops.append(float(np.max(profile.level_db)))
        anchor = after or before
        if reference is not None and anchor is not None:
            # Shift the reference to the same average level so only the tone shapes are compared.
            band = (anchor.freqs > 200) & (anchor.freqs < 5000)
            ref_band = (reference.freqs > 200) & (reference.freqs < 5000)
            offset = float(np.mean(anchor.level_db[band]) - np.mean(reference.level_db[ref_band]))
            self.reference.setData(reference.freqs, reference.level_db + offset)
        else:
            self.reference.setData([], [])
        top = (max(tops) if tops else -10.0) + 6.0
        self.plot.getPlotItem().getViewBox().setYRange(top - 72.0, top, padding=0)

    def update_live(self, block: np.ndarray | None, samplerate: int, dt: float) -> None:
        """Feed the samples just played (mono) to the live analyzer; None clears it."""
        if block is None or block.size < 64:
            self._live_db = None
            self.live.setData([], [])
            return
        n = 1 << int(round(math.log2(max(256, self.LIVE_FFT_SECONDS * samplerate))))
        segment = block[-n:]
        if segment.size < n:
            segment = np.concatenate((np.zeros(n - segment.size, dtype=segment.dtype), segment))
        window = np.hanning(n)
        power = np.square(np.abs(np.fft.rfft(segment * window))) * (4.0 / window.sum() ** 2)
        freqs = np.fft.rfftfreq(n, 1.0 / samplerate)
        grid = spectrum_grid(samplerate)
        level = 10.0 * np.log10(band_power_to_grid(power, freqs, grid) + 1e-20)
        if self._live_db is None or self._live_db.shape != level.shape:
            self._live_db = level
        else:
            self._live_db = np.maximum(level, self._live_db - self.LIVE_FALL_DB_PER_S * dt)
        self._live_grid = grid
        self.live.setData(grid, self._live_db)


# ---------------------------------------------------------------------------
# Album loudness comparison
# ---------------------------------------------------------------------------

def deviation_color(value: float, target: float) -> str:
    off = abs(value - target)
    return "good" if off <= 1.0 else "warn" if off <= 3.0 else "bad"


def _set_bars(item: pg.BarGraphItem, x, y0: float, heights, width: float, brushes: list, pens: list) -> None:
    """BarGraphItem.setOpts that also works with zero bars (pyqtgraph fails on empty pen lists)."""
    if len(x) == 0:
        item.setOpts(x=[], height=[], y0=y0, width=width, pens=None, pen=QPen(Qt.PenStyle.NoPen),
                     brushes=None, brush=QBrush(Qt.BrushStyle.NoBrush))
    else:
        item.setOpts(x=x, height=heights, y0=y0, width=width, brushes=brushes, pens=pens)


class BatchComparisonView(QWidget):
    """Loudness of every track side by side, with the target line and a ±1 LU band."""

    trackClicked = Signal(int)   # uid

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.plot = ThemedPlot()
        item = self.plot.getPlotItem()
        vb = item.getViewBox()
        vb.setMouseEnabled(x=False, y=False)
        vb.enableAutoRange(False)
        item.getAxis("left").setWidth(36)
        item.showGrid(x=False, y=True, alpha=0.08)
        self._band = pg.LinearRegionItem(values=(-15, -13), orientation="horizontal", movable=False,
                                         brush=theme.color("accent", 0.07), pen=_pen("accent", 0, 0.0))
        self._target = pg.InfiniteLine(pos=-14, angle=0, pen=_pen("accent", 1.5, 0.8, Qt.PenStyle.DashLine))
        no_pen, no_brush = QPen(Qt.PenStyle.NoPen), QBrush(Qt.BrushStyle.NoBrush)
        self._before = pg.BarGraphItem(x=[], height=[], width=0.62, y0=0, pen=no_pen, brush=no_brush)
        self._after = pg.BarGraphItem(x=[], height=[], width=0.62, y0=0, pen=no_pen, brush=no_brush)
        self._selected = pg.BarGraphItem(x=[], height=[], width=0.8, y0=0, pen=no_pen, brush=no_brush)
        # The original's bars are drawn as outlines above the mastered bars, so they stay
        # visible whether the track got louder or quieter.
        for thing in (self._band, self._after, self._before, self._selected, self._target):
            item.addItem(thing)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plot)
        self._rows: list[tuple[int, int, str, float, float | None]] = []
        self._floor = -30.0
        self.plot.scene().sigMouseClicked.connect(self._on_click)
        self.plot.scene().sigMouseMoved.connect(self._on_hover)

    def set_tracks(self, rows: list[tuple[int, int, str, float, float | None]], target: float, selected_uid: int) -> None:
        """rows: (uid, track number, title, loudness before, loudness after or None)."""
        self._rows = [r for r in rows if math.isfinite(r[3]) and r[3] > FLOOR_DB]
        values = [r[3] for r in self._rows] + [r[4] for r in self._rows if r[4] is not None and math.isfinite(r[4])]
        self._floor = math.floor(min(values + [target - 12]) / 6.0) * 6.0 - 2.0
        top = max(values + [target + 4])
        x = np.arange(1, len(self._rows) + 1, dtype=float)
        floor = self._floor
        before = np.array([r[3] for r in self._rows]) if self._rows else np.zeros(0)
        after = np.array([r[4] if r[4] is not None else np.nan for r in self._rows]) if self._rows else np.zeros(0)
        has_after = ~np.isnan(after) if after.size else np.zeros(0, bool)

        ghost_pen = _pen("before", 1.4, 0.95, Qt.PenStyle.DashLine)
        _set_bars(self._before, x, floor, before - floor, 0.62,
                  [QBrush(Qt.BrushStyle.NoBrush) if done else QBrush(theme.color("before", 0.55)) for done in has_after],
                  [ghost_pen if done else QPen(Qt.PenStyle.NoPen) for done in has_after])
        ax, ah = x[has_after], after[has_after]
        _set_bars(self._after, ax, floor, ah - floor, 0.62,
                  [QBrush(theme.color(deviation_color(v, target), 0.85)) for v in ah],
                  [QPen(Qt.PenStyle.NoPen) for _ in ah])
        sel = [i for i, r in enumerate(self._rows) if r[0] == selected_uid]
        if sel:
            i = sel[0]
            value = after[i] if has_after[i] else before[i]
            _set_bars(self._selected, [x[i]], floor, [value - floor], 0.8,
                      [QBrush(Qt.BrushStyle.NoBrush)], [_pen("text", 1.5, 0.8)])
        else:
            _set_bars(self._selected, [], floor, [], 0.8, [], [])
        self._band.setRegion((target - 1.0, target + 1.0))
        self._target.setPos(target)
        item = self.plot.getPlotItem()
        item.getAxis("bottom").setTicks([[(i + 1, str(r[1])) for i, r in enumerate(self._rows)]])
        vb = item.getViewBox()
        vb.setXRange(0.4, max(1, len(self._rows)) + 0.6, padding=0)
        vb.setYRange(floor, top + 2.0, padding=0)

    def _index_at(self, scene_pos) -> int:
        vb = self.plot.getPlotItem().getViewBox()
        if not vb.sceneBoundingRect().contains(scene_pos):
            return -1
        x = vb.mapSceneToView(scene_pos).x()
        i = int(round(x)) - 1
        return i if 0 <= i < len(self._rows) and abs(x - (i + 1)) <= 0.4 else -1

    def _on_click(self, event) -> None:
        i = self._index_at(event.scenePos())
        if i >= 0:
            self.trackClicked.emit(self._rows[i][0])

    def _on_hover(self, scene_pos) -> None:
        i = self._index_at(scene_pos)
        if i < 0:
            QToolTip.hideText()
            return
        _, number, title, before, after = self._rows[i]
        global_pos = self.plot.mapToGlobal(self.plot.mapFromScene(scene_pos))
        QToolTip.showText(global_pos, album_bar_tip(number, title, before, after), self.plot)


# ---------------------------------------------------------------------------
# Painted meters
# ---------------------------------------------------------------------------

class LoudnessMeter(QWidget):
    """Horizontal loudness scale with the target zone, original and mastered
    markers, and a live needle during playback."""

    RANGE = (-30.0, -6.0)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(56)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._before: float | None = None
        self._after: float | None = None
        self._live: float | None = None
        self._target = -14.0

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(320, 56)

    def set_values(self, before: float | None, after: float | None, target: float) -> None:
        self._before = before if before is not None and math.isfinite(before) else None
        self._after = after if after is not None and math.isfinite(after) else None
        self._target = target
        self.update()

    def set_live(self, value: float | None) -> None:
        value = value if value is not None and math.isfinite(value) and value > -70 else None
        if value != self._live:
            self._live = value
            self.update()

    def _x(self, value: float, left: float, width: float) -> float:
        lo, hi = self.RANGE
        return left + (min(hi, max(lo, value)) - lo) / (hi - lo) * width

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        left, right = 8.0, self.width() - 8.0
        width = right - left
        bar_y, bar_h = 12.0, 12.0
        radius = bar_h / 2

        track = QPainterPath()
        track.addRoundedRect(QRectF(left, bar_y, width, bar_h), radius, radius)
        p.fillPath(track, theme.color("surface_hover"))
        p.save()
        p.setClipPath(track)
        for span, name, alpha in ((3.0, "warn", 0.28), (1.0, "good", 0.45)):
            x0 = self._x(self._target - span, left, width)
            x1 = self._x(self._target + span, left, width)
            p.fillRect(QRectF(x0, bar_y, x1 - x0, bar_h), theme.color(name, alpha))
        p.restore()

        tx = self._x(self._target, left, width)
        p.setPen(_pen("text", 1.5, 0.9))
        p.drawLine(QPointF(tx, bar_y - 4), QPointF(tx, bar_y + bar_h + 4))

        centre_y = bar_y + bar_h / 2
        if self._before is not None and self._after is not None:
            bx, ax = self._x(self._before, left, width), self._x(self._after, left, width)
            if abs(ax - bx) > 12:
                p.setPen(_pen("text_muted", 1.2, 0.8, Qt.PenStyle.DotLine))
                p.drawLine(QPointF(bx, centre_y), QPointF(ax, centre_y))
        if self._before is not None:
            bx = self._x(self._before, left, width)
            p.setPen(QPen(theme.color("before"), 2.0))
            p.setBrush(theme.color("surface"))
            p.drawEllipse(QPointF(bx, centre_y), 6.5, 6.5)
        if self._after is not None:
            ax = self._x(self._after, left, width)
            p.setPen(QPen(theme.color("surface"), 2.0))
            p.setBrush(theme.color("after"))
            p.drawEllipse(QPointF(ax, centre_y), 7.5, 7.5)
        if self._live is not None:
            lx = self._x(self._live, left, width)
            p.setPen(_pen("live", 2.0))
            p.drawLine(QPointF(lx, bar_y - 6), QPointF(lx, bar_y + bar_h + 6))

        p.setFont(theme.font("caption", tabular=True))
        p.setPen(theme.color("text_faint"))
        label_y = bar_y + bar_h + 10
        for value in (-30, -24, -18, -12, -6):
            x = self._x(value, left, width)
            rect = QRectF(x - 20, label_y, 40, 16)
            align = Qt.AlignmentFlag.AlignHCenter
            if value == -30:
                rect, align = QRectF(x, label_y, 40, 16), Qt.AlignmentFlag.AlignLeft
            elif value == -6:
                rect, align = QRectF(x - 40, label_y, 40, 16), Qt.AlignmentFlag.AlignRight
            if abs(x - tx) > 64:       # keep clear of the target label
                p.drawText(rect, align | Qt.AlignmentFlag.AlignTop, str(value))
        p.setPen(theme.color("text"))
        p.setFont(theme.font("caption", QFont.Weight.DemiBold, tabular=True))
        p.drawText(QRectF(tx - 40, label_y, 80, 16), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop,
                   f"{self._target:g} target")
        p.end()


class TruePeakIndicator(QWidget):
    """True peak of the original and the mastered track against 0 dBTP and the ceiling."""

    RANGE = (-18.0, 3.0)
    ROWS = (("Original", "before"), ("Mastered", "after"))

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(88)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._values: dict[str, float | None] = {"before": None, "after": None}
        self._ceiling = -1.0

    def sizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(320, 88)

    def set_values(self, before: float | None, after: float | None, ceiling: float) -> None:
        self._values = {"before": before, "after": after}
        self._ceiling = ceiling
        self.update()

    def _x(self, value: float, left: float, width: float) -> float:
        lo, hi = self.RANGE
        return left + (min(hi, max(lo, value)) - lo) / (hi - lo) * width

    def paintEvent(self, event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label_w, value_w = 72.0, 128.0
        left = label_w
        width = max(40.0, self.width() - label_w - value_w - 8)
        zero_x = self._x(0.0, left, width)
        ceiling_x = self._x(self._ceiling, left, width)
        row_h = 30.0
        for row, (label, key) in enumerate(self.ROWS):
            y = 6 + row * row_h
            value = self._values[key]
            p.setFont(theme.font("small", QFont.Weight.DemiBold))
            p.setPen(theme.color("text_muted"))
            p.drawText(QRectF(0, y, label_w - 8, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, label)
            bar = QRectF(left, y + 4, width, 8)
            track = QPainterPath()
            track.addRoundedRect(bar, 4, 4)
            p.fillPath(track, theme.color("surface_hover"))
            if value is not None:
                p.save()
                p.setClipPath(track)
                x = self._x(value, left, width)
                p.fillRect(QRectF(left, bar.top(), min(x, zero_x) - left, bar.height()), theme.color(key, 0.85))
                if x > zero_x:
                    p.fillRect(QRectF(zero_x, bar.top(), x - zero_x, bar.height()), theme.color("bad"))
                p.restore()
                over = value > 0.0
                p.setFont(theme.font("small", QFont.Weight.DemiBold, tabular=True))
                p.setPen(theme.color("bad" if over else "text"))
                p.drawText(QRectF(left + width + 12, y - 2, value_w, 14), Qt.AlignmentFlag.AlignLeft, f"{value:+.1f} dBTP")
                p.setFont(theme.font("caption", tabular=True))
                p.setPen(theme.color("bad" if over else "text_faint"))
                note = f"{value:.1f} dB over the maximum" if over else f"{-value:.1f} dB headroom"
                p.drawText(QRectF(left + width + 12, y + 12, value_w, 14), Qt.AlignmentFlag.AlignLeft, note)
            else:
                p.setFont(theme.font("caption"))
                p.setPen(theme.color("text_faint"))
                p.drawText(QRectF(left + width + 12, y, value_w, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Not measured yet")
        top, bottom = 4.0, 6 + len(self.ROWS) * row_h - 12
        p.setPen(_pen("bad", 1.5, 0.9))
        p.drawLine(QPointF(zero_x, top), QPointF(zero_x, bottom))
        p.setPen(_pen("accent", 1.2, 0.9, Qt.PenStyle.DashLine))
        p.drawLine(QPointF(ceiling_x, top), QPointF(ceiling_x, bottom))
        p.setFont(theme.font("caption", tabular=True))
        p.setPen(theme.color("text_faint"))
        p.drawText(QRectF(zero_x - 2, bottom + 2, 60, 14), Qt.AlignmentFlag.AlignLeft, "0 dBTP")
        p.setPen(theme.color("accent"))
        p.drawText(QRectF(ceiling_x - 62, bottom + 2, 60, 14), Qt.AlignmentFlag.AlignRight, f"{self._ceiling:g} ceiling")
        p.end()
