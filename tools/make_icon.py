"""Render the app logo (ui.theme.app_icon) to tools/evenfold.ico for the .exe.

    python -m tools.make_icon
"""

from __future__ import annotations

import io
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image  # noqa: E402
from PySide6.QtCore import QBuffer, QIODevice, QSize  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
TARGET = Path(__file__).with_name("evenfold.ico")


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from ui.theme import app_icon

    icon = app_icon()
    images = []
    for size in SIZES:
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        icon.pixmap(QSize(size, size)).save(buffer, "PNG")
        images.append(Image.open(io.BytesIO(bytes(buffer.data()))).convert("RGBA"))
    largest = images[-1]
    largest.save(TARGET, format="ICO", sizes=[(s, s) for s in SIZES], append_images=images[:-1])
    print(f"wrote {TARGET}")
    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
