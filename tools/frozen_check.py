"""End-to-end check of a frozen build: every bundled dependency is exercised.

Built from the same recipe as the app (EVENFOLD_CHECK=1, see evenfold.spec):
theme and icon fonts, the whole window, analysis, mastering, playback,
reports and library metadata. Prints ALL OK and exits 0 on success.

    EvenfoldCheck.exe <folder with WAV files>
"""

from __future__ import annotations

import importlib.metadata
import os
import sys
import tempfile
import time
import traceback
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.ffmpeg*=false")


def main() -> int:
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    app = QApplication(sys.argv[:1])
    from audio.analysis import integrated_loudness, load_audio, to_db, true_peak
    from reports.report_export import ReportEntry, export_csv, export_pdf
    from ui import theme
    from ui.dialogs import LIBRARIES
    from ui.main_window import MainWindow
    from ui.settings import AppSettings
    from ui.visualizers import configure_pyqtgraph

    def wait(condition, timeout=300.0) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            app.processEvents()
            if condition():
                return True
            time.sleep(0.01)
        return False

    album = Path(sys.argv[1])
    temp = Path(tempfile.mkdtemp())
    theme.apply(app)
    print("theme + icon fonts: ok")
    configure_pyqtgraph(False)
    settings = AppSettings(QSettings(str(temp / "s.ini"), QSettings.Format.IniFormat))
    settings.onboarding_done = True
    window = MainWindow(settings, False)
    window.show()
    print("window: ok")

    session = window.session
    session.add_paths([str(album)])
    assert wait(lambda: not session.analyzing), "analysis timed out"
    assert all(t.analysis for t in session.tracks), [t.error for t in session.tracks]
    print(f"analysis: {len(session.tracks)} tracks ok")

    session.update_settings(output_dir=str(temp / "out"), eq_match=True, eq_reference=session.tracks[0].path)
    session.start(dry_run=False)
    assert wait(lambda: not session.busy), "mastering timed out"
    for track in session.tracks:
        assert track.status == "done", (track.title, track.error)
        audio, fmt = load_audio(track.result.output_path)
        assert fmt.subtype == track.fmt.subtype and fmt.samplerate == track.fmt.samplerate
        assert to_db(true_peak(audio)) <= -0.999
        lufs = integrated_loudness(audio, fmt.samplerate)
        print(f"  {track.title:16} {fmt.summary:32} {lufs:6.2f} LUFS")
    print("mastering (with EQ matching via pedalboard): ok")

    from PySide6.QtMultimedia import QMediaDevices
    if QMediaDevices.defaultAudioOutput().isNull():
        # Build machines such as GitHub's have no sound card; everything else is still checked.
        print("playback: skipped (no audio output device on this machine)")
    else:
        engine = window.engine
        engine.set_volume(0.0)
        session.select(session.tracks[0].uid)
        assert wait(lambda: engine.ready and engine.has_mastered(), 20), "player buffers did not load"
        engine.set_version("original")
        engine.play()
        assert wait(lambda: engine.position() > 0.3, 20), "playback did not advance"
        start_frame, before = engine._start_frame, engine.position()
        engine.set_version("mastered")
        wait(lambda: engine.position() > before + 0.4, 5)
        assert engine.playing and engine._start_frame == start_frame, "the A/B switch interrupted playback"
        engine.pause()
        print("playback with seamless A/B switch (QAudioSink): ok")

    entries = [ReportEntry(t.track_number, t.title, t.path, t.fmt, t.analysis.measurements, t.analysis.clipping,
                           t.result, t.error) for t in session.tracks]
    export_pdf(temp / "report.pdf", entries, session.settings, session.last_summary)
    export_csv(temp / "report.csv", entries, session.settings)
    assert (temp / "report.pdf").stat().st_size > 5000
    print("reports (PDF + CSV): ok")

    missing = [name for name, _ in LIBRARIES if importlib.metadata.version(name) in ("", None)]
    assert not missing, missing
    print("library metadata: ok")

    session.results_stale = False
    window.close()
    print("ALL OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        print("CHECK FAILED")
        sys.exit(1)
