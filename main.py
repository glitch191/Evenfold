"""Evenfold: album mastering for people who have never mastered an album.

    python main.py [files ...] [--software-rendering] [--reset]
"""

from __future__ import annotations

import argparse
import os
import sys

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtWidgets import QApplication

from ui import theme
from ui.main_window import MainWindow
from ui.settings import AppSettings
from ui.visualizers import configure_pyqtgraph


def main() -> int:
    parser = argparse.ArgumentParser(description=f"{theme.APP_NAME}: consistent, safe album mastering.")
    parser.add_argument("files", nargs="*", help="WAV files or folders to add on startup")
    parser.add_argument("--software-rendering", action="store_true",
                        help="draw visualisations without the GPU for this session")
    parser.add_argument("--reset", action="store_true", help="forget saved settings, layout and window state")
    args, qt_args = parser.parse_known_args()
    # Qt's FFmpeg backend prints a dump of every file it opens; keep the console quiet.
    os.environ.setdefault("QT_LOGGING_RULES", "qt.multimedia.ffmpeg*=false")

    if sys.platform == "win32":
        import ctypes
        # Own taskbar identity, so Windows shows the app icon rather than Python's.
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Evenfold.AlbumMastering")

    surface = QSurfaceFormat()
    surface.setSwapInterval(1)        # sync buffer swaps to the display refresh
    surface.setSamples(4)
    QSurfaceFormat.setDefaultFormat(surface)
    QApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    app = QApplication([sys.argv[0], *qt_args])
    app.setOrganizationName(theme.ORG_NAME)
    app.setApplicationName(theme.APP_NAME)
    app.setApplicationVersion(theme.APP_VERSION)
    app.setWindowIcon(theme.app_icon())
    theme.apply(app)

    if args.reset:
        QSettings(theme.ORG_NAME, theme.APP_NAME).clear()
    settings = AppSettings()
    hardware = settings.hardware_acceleration and not args.software_rendering
    configure_pyqtgraph(hardware)

    window = MainWindow(settings, hardware)
    window.show_initial()
    if args.files:
        window.session.add_paths(args.files)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
