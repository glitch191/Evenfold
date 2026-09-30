# PyInstaller recipe for dist\Evenfold\Evenfold.exe (a folder build: it starts
# much faster than a single-file build, which would unpack ~300 MB on every launch).
#
#   .venv\Scripts\python -m tools.make_icon
#   .venv\Scripts\pyinstaller tools\evenfold.spec --noconfirm

#
# With EVENFOLD_CHECK=1 the same recipe builds EvenfoldCheck.exe (console, runs
# tools/frozen_check.py), which verifies that every dependency was bundled.

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata

ROOT = Path(SPECPATH).parent
CHECK = os.environ.get("EVENFOLD_CHECK") == "1"
NAME = "EvenfoldCheck" if CHECK else "Evenfold"
SCRIPT = ROOT / "tools" / "frozen_check.py" if CHECK else ROOT / "main.py"

datas = [(str(ROOT / "ui" / "theme.qss"), "ui")]
datas += collect_data_files("qtawesome")          # icon fonts, all loaded at start-up
datas += collect_data_files("fpdf")               # PDF report resources
# Package metadata, so Help > About can show each library's version.
for package in ("PySide6", "pyqtgraph", "numpy", "scipy", "soundfile", "pyloudnorm",
                "pedalboard", "mutagen", "qtawesome", "fpdf2"):
    datas += copy_metadata(package)

a = Analysis(
    [str(SCRIPT)],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["pedalboard_native"],
    excludes=[
        "tkinter", "pytest", "tests", "tools",
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
        "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtQuick3D", "PySide6.QtCharts",
        "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtPdf", "PySide6.QtDesigner",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    icon=str(ROOT / "tools" / "evenfold.ico"),
    version=str(ROOT / "tools" / "version_info.txt"),
    console=CHECK,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
