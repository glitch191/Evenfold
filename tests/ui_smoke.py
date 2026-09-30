"""Drive the real window end to end and save screenshots of every state.

Uses throwaway settings (an INI file in a temp folder), so it never touches the
settings of an installed copy.

    python -m tests.ui_smoke OUTPUT_FOLDER [--software] [--size 1920x1080]
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from tests.make_test_album import write_album


def wait_until(app: QApplication, condition, timeout: float = 120.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out")
    parser.add_argument("--software", action="store_true")
    parser.add_argument("--size", default="1920x1080")
    parser.add_argument("--hidden", action="store_true", help="render without showing the window")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    width, height = (int(v) for v in args.size.split("x"))

    surface = QSurfaceFormat()
    surface.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(surface)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("EvenfoldSmokeTest")

    from ui import theme
    from ui.main_window import MainWindow
    from ui.settings import AppSettings
    from ui.visualizers import configure_pyqtgraph

    theme.apply(app)
    temp = Path(tempfile.mkdtemp())
    settings = AppSettings(QSettings(str(temp / "settings.ini"), QSettings.Format.IniFormat))
    settings.onboarding_done = True
    configure_pyqtgraph(not args.software)
    window = MainWindow(settings, not args.software)
    window.resize(width, height)
    if args.hidden:
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
    window.show()
    window.clock.attach(window)
    pump(app, 0.5)

    def shot(name: str, widget=None) -> None:
        pump(app, 0.3)
        (widget or window).grab().save(str(out / f"{name}.png"))
        print("saved", name)

    shot("01_simple_empty")

    album = write_album(temp / "album")
    window.session.add_paths([str(p) for p in album])
    ok = wait_until(app, lambda: not window.session.analyzing)
    print("analysis finished:", ok)
    shot("02_simple_loaded")

    window.session.update_settings(output_dir=str(temp / "out"))
    started = time.monotonic()
    window.session.start(dry_run=False)
    pump(app, 1.2)
    shot("03_simple_processing")
    ok = wait_until(app, lambda: not window.session.busy)
    print(f"mastering finished: {ok} in {time.monotonic() - started:.1f} s")
    shot("04_simple_done")
    for t in window.session.tracks:
        print(f"  {t.track_number}. {t.title:16} {t.status:9} {t.result.description.headline if t.result else t.error}")

    window.session.select(window.session.tracks[3].uid)      # the clipped track
    shot("05_simple_clipped_track")

    window.set_mode("advanced")
    window.session.select(window.session.tracks[0].uid)
    shot("06_advanced")
    window.advanced.settings_panel.set_expanded(True)
    pump(app, 0.3)
    shot("07_advanced_settings_open")

    # Tone matching preview with the album's second track as reference.
    window.session.update_settings(eq_match=True, eq_reference=window.session.tracks[1].path)
    window.session.start(dry_run=True)
    wait_until(app, lambda: not window.session.busy)
    window.session.select(window.session.tracks[2].uid)
    shot("08_advanced_eq_preview")

    # Playback: the frame clock should run at the display rate while playing.
    window.session.update_settings(eq_match=False)
    window.session.start(dry_run=False)
    wait_until(app, lambda: not window.session.busy)
    window.session.select(window.session.tracks[0].uid)
    pump(app, 0.5)
    frames = []
    window.clock.tick.connect(lambda dt: frames.append(dt))
    window.engine.set_version("mastered")
    pump(app, 0.5)
    window.engine.toggle()
    pump(app, 2.0)
    playing = window.engine.playing
    shot("09_advanced_playing")
    window.engine.toggle()
    if frames:
        rate = len(frames) / sum(frames)
        print(f"playing={playing} frames={len(frames)} measured={rate:.0f} Hz screen={window.clock.rate:.0f} Hz")
    else:
        print(f"playing={playing} no frames")

    from ui.visualizers import ThemedPlot
    print("toggle to software:", ThemedPlot.set_hardware_acceleration(False))
    shot("10_advanced_software")
    print("toggle to hardware:", ThemedPlot.set_hardware_acceleration(True))

    from ui.onboarding import OnboardingDialog
    from ui.dialogs import AboutDialog, BasicsDialog
    for name, dialog in (("11_onboarding", OnboardingDialog(window)), ("12_basics", BasicsDialog(window)),
                         ("13_about", AboutDialog(window))):
        dialog.show()
        pump(app, 0.3)
        shot(name, dialog)
        dialog.close()

    from reports.report_export import ReportEntry, export_csv, export_pdf
    entries = [ReportEntry(t.track_number, t.title, t.path, t.fmt, t.analysis.measurements, t.analysis.clipping,
                           t.result, t.error) for t in window.session.tracks]
    export_csv(out / "report.csv", entries, window.session.settings)
    export_pdf(out / "report.pdf", entries, window.session.settings, "6 tracks mastered.")
    print("reports written")

    window.session.results_stale = False
    window.close()
    pump(app, 0.2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
