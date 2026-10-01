"""Regenerate the README screenshots (docs/simple-mode.png, docs/advanced-mode.png).

Masters the deterministic test album in a temp folder, then grabs the window in
Simple mode (the clipped track selected) and in Advanced mode (first track,
Mastered version, around 0:10 and playing when there is a sound card).

The plots are drawn in software: QWidget.grab() can't capture OpenGL plots.
Uses throwaway settings (an INI file in the temp folder), so it never touches
the settings of an installed copy.

    python -m tools.make_screenshots [OUTPUT_FOLDER] [--size 1920x1080]
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import time
from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from tests.make_test_album import write_album

DOCS = Path(__file__).resolve().parent.parent / "docs"
SIMPLE_TRACK = 3              # "Paper Planes": already clipped, so the warning shows
ADVANCED_TRACK = 0
PLAY_FROM_S = 8.5             # plays ~1.5 s before the grab, so the player shows about 0:10
SETTLE_S = 0.6                # lets layouts, fades and the live meters settle before a grab


def pump(app: QApplication, seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def wait_until(app: QApplication, condition, timeout: float = 180.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.02)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Regenerate the README screenshots.")
    parser.add_argument("out", nargs="?", default=str(DOCS), help="output folder (default: docs)")
    parser.add_argument("--size", default="1920x1080", help="window size, WIDTHxHEIGHT")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    width, height = (int(v) for v in args.size.split("x"))

    surface = QSurfaceFormat()
    surface.setSwapInterval(1)
    QSurfaceFormat.setDefaultFormat(surface)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    app = QApplication(sys.argv[:1])
    app.setApplicationName("EvenfoldScreenshots")

    from ui import theme
    from ui.main_window import MainWindow
    from ui.settings import AppSettings
    from ui.visualizers import configure_pyqtgraph

    theme.apply(app)
    temp = Path(tempfile.mkdtemp(prefix="evenfold-shots-"))
    try:
        settings = AppSettings(QSettings(str(temp / "settings.ini"), QSettings.Format.IniFormat))
        settings.onboarding_done = True
        configure_pyqtgraph(False)
        window = MainWindow(settings, False)
        window.resize(width, height)
        window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        window.statusBar().hide()         # its "Software rendering" label would be misleading in the README
        window.show()
        window.clock.attach(window)
        pump(app, 0.5)

        def shot(name: str) -> None:
            pump(app, SETTLE_S)
            path = out / f"{name}.png"
            if not window.grab().save(str(path)):
                raise RuntimeError(f"Couldn't save {path}")
            print("saved", path)

        session = window.session
        session.add_paths([str(p) for p in write_album(temp / "album")])
        if not wait_until(app, lambda: not session.analyzing):
            raise RuntimeError("Analysis didn't finish in time.")
        session.update_settings(output_dir=str(temp / "mastered"))
        session.start(dry_run=False)
        if not wait_until(app, lambda: not session.busy):
            raise RuntimeError("Mastering didn't finish in time.")
        if not session.last_run_ok:
            raise RuntimeError(f"Mastering failed: {session.last_summary}")

        window.set_mode("simple", save=False)
        session.select(session.tracks[SIMPLE_TRACK].uid)
        shot("simple-mode")

        window.set_mode("advanced", save=False)
        session.select(session.tracks[ADVANCED_TRACK].uid)
        engine = window.engine
        wait_until(app, lambda: engine.ready and engine.has_mastered(), timeout=30.0)
        engine.set_version("mastered")
        engine.seek(PLAY_FROM_S)
        engine.play()
        pump(app, 1.0)
        if not engine.playing:
            print("no sound card: the Advanced screenshot shows the player paused")
        shot("advanced-mode")
        engine.stop()

        session.results_stale = False      # no "results are outdated" prompt on close
        window.close()
        pump(app, 0.2)
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
