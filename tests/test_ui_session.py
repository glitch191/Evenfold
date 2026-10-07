"""Regression tests for the window's state handling (runs headless).

    python -m pytest tests/test_ui_session.py -q
"""

from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.ffmpeg*=false")

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from tests.make_test_album import ALBUM, synth  # noqa: E402


def _pump(app, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def _wait(app, condition, timeout: float = 120.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return
        time.sleep(0.005)
    raise TimeoutError


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import soundfile as sf

    from audio.processing import quantize
    from ui import theme
    from ui.main_window import MainWindow
    from ui.settings import AppSettings
    from ui.visualizers import configure_pyqtgraph

    app = QApplication.instance() or QApplication([])
    theme.apply(app)
    configure_pyqtgraph(False)
    root = tmp_path_factory.mktemp("ui")
    album = root / "album"
    album.mkdir()
    for i in (0, 3):
        spec = ALBUM[i]
        sf.write(str(album / spec.name), quantize(synth(spec, 1000 + i), spec.subtype), spec.samplerate,
                 subtype=spec.subtype)
    settings = AppSettings(QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat))
    settings.onboarding_done = True
    window = MainWindow(settings, False)
    window.show()
    session = window.session
    session.add_paths([str(album)])
    _wait(app, lambda: not session.analyzing)
    session.update_settings(output_dir=str(root / "out"))
    yield app, window, root
    session.results_stale = False
    window.close()


def test_remaster_while_mastered_version_is_loaded(env):
    app, window, _ = env
    s, engine = window.session, window.engine
    s.start(dry_run=False)
    _wait(app, lambda: not s.busy)
    track = s.tracks[0]
    s.select(track.uid)
    _wait(app, lambda: engine.has_mastered())
    engine.set_version("mastered")
    assert engine.version == "mastered"

    s.update_settings(even_spacing=False)
    s.start(dry_run=False)
    _wait(app, lambda: not s.busy)
    assert all(t.status == "done" for t in s.tracks), [t.error for t in s.tracks]
    # The player follows the new result: no silence edit any more, so no timeline offset.
    assert track.result.silence is None
    _wait(app, lambda: engine.has_mastered())
    assert engine._offset_s == 0.0 and engine._source.offset == 0
    assert not s.results_stale


def test_settings_menus_are_locked_during_a_batch(env):
    app, window, _ = env
    s = window.session
    s.start(dry_run=True)
    assert s.busy
    assert not window.act_depths["PCM_16"].isEnabled()
    assert not window.act_load_preset.isEnabled()
    _wait(app, lambda: not s.busy)
    assert window.act_depths["PCM_16"].isEnabled()


def test_metadata_edit_marks_results_stale(env):
    app, window, _ = env
    s = window.session
    s.results_stale = False
    s.set_track_meta(s.tracks[0].uid, title="New Title")
    assert s.results_stale
    s.start(dry_run=False)
    _wait(app, lambda: not s.busy)
    assert not s.results_stale


def test_seek_before_play_is_kept(env):
    app, window, _ = env
    s, engine = window.session, window.engine
    s.select(s.tracks[1].uid)
    _wait(app, lambda: engine.ready)
    engine.set_version("original")
    engine.seek(3.0)
    _pump(app, 0.3)
    engine.toggle()
    _pump(app, 0.6)
    position = engine.position()
    engine.toggle()
    assert position > 2.5


def test_ab_switch_keeps_playing_without_a_jump(env):
    app, window, _ = env
    s, engine = window.session, window.engine
    s.select(s.tracks[0].uid)
    _wait(app, lambda: engine.ready and engine.has_mastered())
    engine.set_version("original")
    engine.seek(1.0)
    engine.play()
    _pump(app, 0.5)
    before, restarts = engine.position(), engine._start_frame
    engine.set_version("mastered")
    _pump(app, 0.5)
    after = engine.position()
    playing = engine.playing
    engine.pause()
    assert playing
    assert engine._start_frame == restarts          # the stream never restarted
    assert 0.35 < after - before < 0.75            # time kept flowing through the switch
    assert engine._source.mix == 1.0               # the crossfade finished on the mastered version


def test_equal_volume_is_off_at_first_and_explains_identical_versions(env):
    app, window, _ = env
    s, engine, card = window.session, window.engine, window.advanced.player
    assert not engine.level_match and not card.match.isChecked()
    gain_only = next(t for t in s.tracks if t.result.limiter.max_reduction_db < 0.05)   # Paper Planes
    limited = next(t for t in s.tracks if t.result.limiter.max_reduction_db > 1.0)      # Opening
    for track, expected in ((gain_only, "level_match_identical"), (limited, "level_match_subtle")):
        s.select(track.uid)
        _wait(app, lambda: engine.ready and engine.has_mastered())
        assert card.hint.isHidden()                  # nothing to explain while volumes differ
        card.match.setChecked(True)
        from ui.tooltips import TIPS
        assert not card.hint.isHidden() and card.hint.text() == TIPS[expected][1], engine.matched_difference_db
        card.match.setChecked(False)


def test_playback_stops_at_the_end(env):
    app, window, _ = env
    engine = window.engine
    _wait(app, lambda: engine.ready)
    engine.seek(engine.duration() - 0.3)
    engine.play()
    _wait(app, lambda: not engine.playing, timeout=5)
    assert engine.position() == 0.0


def test_click_on_progress_bar_jumps_there(env):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest

    app, window, _ = env
    window.set_mode("advanced")
    _pump(app, 0.3)
    slider = window.advanced.player.position
    engine = window.engine
    _wait(app, lambda: engine.ready)
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(int(slider.width() * 0.75), slider.height() // 2))
    _pump(app, 0.1)
    assert abs(engine.position() / engine.duration() - 0.75) < 0.05
    window.set_mode("simple")


def test_mix_source_crossfades_on_one_shared_timeline():
    import numpy as np

    from ui.playback import FRAME_BYTES, _MixSource

    src = _MixSource()
    src.original = np.full((1000, 2), 0.5, np.float32)
    src.mastered = np.full((800, 2), -0.25, np.float32)
    src.offset = 200                                  # mastered frame 0 plays at original frame 200
    src.end, src.volume, src.fade_step = 1000, 1.0, 1 / 50
    first = src.render(300)
    assert np.allclose(first, 0.5)
    src.target = 1.0
    fade = src.render(100)[:, 0]
    assert src.index == 400 and src.mix == 1.0
    assert fade[0] > 0.4 and np.allclose(fade[60:], -0.25)
    assert np.all(np.abs(np.diff(fade)) < 0.05)      # smooth: no click
    rest = np.frombuffer(src.readData(10_000), np.float32).reshape(-1, 2)
    assert rest.shape[0] == 600 and np.allclose(rest, -0.25)
    assert src.readData(FRAME_BYTES * 10) == b""


def test_clear_forgets_previous_outcome(env):
    app, window, root = env
    s = window.session
    assert s.last_summary
    window.engine.release_files()
    s.clear()
    assert s.last_summary == "" and s.output_dirs == [] and s.last_run_dry is None
    s.add_paths([str(root / "album")])
    _wait(app, lambda: not s.analyzing)
    assert window.simple.master.outcome_box.isHidden()


def test_built_in_presets_are_complete_and_explained(env):
    from audio.summary import LOUDER_PRESET_LUFS
    from ui.presets import BUILT_IN
    from ui.tooltips import PRESET_TIPS

    _app, window, _root = env
    assert set(PRESET_TIPS) == set(BUILT_IN)
    assert all("even_spacing" in values for values in BUILT_IN.values())
    assert any(values["target_lufs"] == LOUDER_PRESET_LUFS for values in BUILT_IN.values())
    session = window.session
    before = session.settings.copy()
    try:
        session.apply_preset(BUILT_IN["Continuous Mix (-14 LUFS, no gaps)"])
        assert not session.settings.even_spacing
        session.apply_preset(BUILT_IN["Rock, Pop & Electronic (-11 LUFS)"])
        assert session.settings.even_spacing and session.settings.target_lufs == -11.0
    finally:
        session.apply_preset(before.preset_dict())
        session.results_stale = False


def test_mouse_wheel_scrolls_the_side_column_without_changing_settings(env):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent
    from PySide6.QtWidgets import QScrollArea

    app, window, _root = env
    window.set_mode("advanced", save=False)
    panel = window.advanced.settings_panel
    panel.set_expanded(True)
    window.resize(1280, 700)          # short enough for the column to scroll
    _pump(app, 0.3)
    scroll = next(w for w in window.advanced.findChildren(QScrollArea) if w.widget().isAncestorOf(panel))
    bar = scroll.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    controls = (panel.target, panel.ceiling, panel.gap, panel.strength, panel.reference, panel.depth,
                window.advanced.player.volume)
    before = [c.value() if hasattr(c, "value") else c.currentIndex() for c in controls]
    start = bar.value()
    for control in controls:
        centre = QPointF(control.rect().center())
        event = QWheelEvent(centre, QPointF(control.mapToGlobal(centre.toPoint())), QPoint(0, 0), QPoint(0, -120),
                            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        app.sendEvent(control, event)
    after = [c.value() if hasattr(c, "value") else c.currentIndex() for c in controls]
    assert after == before
    assert bar.value() > start              # the wheel scrolled the column instead
    panel.set_expanded(False)
    window.set_mode("simple", save=False)
