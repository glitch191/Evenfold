"""Secondary dialogs: Mastering Basics, About, presets and the default target."""

from __future__ import annotations

import importlib.metadata
from html import escape

import soundfile as sf
from PySide6.QtCore import QSize, Qt, qVersion
from PySide6.QtWidgets import (
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from audio.processing import TARGET_LUFS_RANGE

from . import theme
from .presets import BUILT_IN, PresetStore
from .tooltips import BASICS, TIPS, preset_tip, tip
from .widgets import button, label

LIBRARIES = (
    ("PySide6", "Qt for Python: the interface"),
    ("pyqtgraph", "fast, hardware-accelerated plots"),
    ("numpy", "sample processing"),
    ("scipy", "filters and oversampling"),
    ("soundfile", "reading and writing WAV files"),
    ("pyloudnorm", "ITU-R BS.1770 loudness measurement"),
    ("pedalboard", "EQ filters"),
    ("mutagen", "file tags"),
    ("qtawesome", "icons (Phosphor)"),
    ("fpdf2", "PDF reports"),
)


def _version(package: str) -> str:
    try:
        return importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        return "?"


def _dialog_buttons(*buttons) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(theme.SPACE["sm"])
    row.addStretch(1)
    for b in buttons:
        row.addWidget(b)
    return row


class BasicsDialog(QDialog):
    """Help > Mastering Basics: every tooltip explanation in one readable place."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Mastering Basics")
        self.resize(880, 600)
        topics = QListWidget()
        topics.setObjectName("TopicList")
        topics.setFixedWidth(260)
        for title, _ in BASICS:
            topics.addItem(title)
        self.article = QTextBrowser()
        self.article.setObjectName("Article")
        self.article.setOpenExternalLinks(False)
        topics.currentRowChanged.connect(self._show)
        body = QHBoxLayout()
        body.setSpacing(theme.SPACE["lg"])
        body.addWidget(topics)
        body.addWidget(self.article, 1)
        close = button("Close")
        close.clicked.connect(self.accept)
        layout = QVBoxLayout(self)
        m = theme.SPACE["lg"]
        layout.setContentsMargins(m, m, m, m)
        layout.setSpacing(theme.SPACE["md"])
        layout.addWidget(label("Mastering Basics", "title"))
        layout.addWidget(label("The ideas behind every setting, in plain language.", "muted"))
        layout.addLayout(body, 1)
        layout.addLayout(_dialog_buttons(close))
        topics.setCurrentRow(0)

    def _show(self, row: int) -> None:
        if row < 0:
            return
        title, paragraphs = BASICS[row]
        self.article.document().setDefaultStyleSheet(theme.ARTICLE_CSS)
        html = f"<h2>{escape(title)}</h2>" + "".join(f"<p>{escape(p)}</p>" for p in paragraphs)
        self.article.setHtml(html)


class AboutDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"About {theme.APP_NAME}")
        self.setFixedWidth(520)
        logo = QLabel()
        logo.setPixmap(theme.app_icon().pixmap(QSize(64, 64)))
        head = QHBoxLayout()
        head.setSpacing(theme.SPACE["md"])
        head.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(2)
        names.addWidget(label(theme.APP_NAME, "title"))
        names.addWidget(label(f"{theme.APP_TAGLINE} · version {theme.APP_VERSION}", "muted"))
        head.addLayout(names, 1)
        layout = QVBoxLayout(self)
        m = theme.SPACE["lg"]
        layout.setContentsMargins(m, m, m, m)
        layout.setSpacing(theme.SPACE["md"])
        layout.addLayout(head)
        layout.addWidget(label(
            "Masters every song of an album to one consistent loudness, with safe peaks, gentle "
            "optional tone matching and even gaps. Each file keeps its own bit depth and sample "
            "rate, and originals are never modified.", "muted", wrap=True))
        layout.addWidget(label("BUILT WITH", "caption"))
        rows = [f"{name} {_version(name)}" for name, _ in LIBRARIES]
        grid = QVBoxLayout()
        grid.setSpacing(4)
        for (name, purpose), text in zip(LIBRARIES, rows):
            line = QHBoxLayout()
            line.addWidget(label(text, "body"))
            line.addStretch(1)
            line.addWidget(label(purpose, "faint"))
            grid.addLayout(line)
        line = QHBoxLayout()
        line.addWidget(label(f"Qt {qVersion()} · libsndfile {sf.__libsndfile_version__}", "faint"))
        line.addStretch(1)
        grid.addLayout(line)
        layout.addLayout(grid)
        close = button("Close")
        close.clicked.connect(self.accept)
        layout.addLayout(_dialog_buttons(close))


class LoadPresetDialog(QDialog):
    def __init__(self, store: PresetStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Load Preset")
        self.resize(460, 420)
        self.store = store
        self.list = QListWidget()
        self.list.setObjectName("PlainList")
        for name in BUILT_IN:
            item = QListWidgetItem(theme.icon("ph.sparkle", "accent"), name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            item.setToolTip(preset_tip(name))
            self.list.addItem(item)
        for name in store.names():
            item = QListWidgetItem(theme.icon("ph.floppy-disk", "text_muted"), name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)
        self.list.setCurrentRow(0)
        self.list.itemDoubleClicked.connect(lambda _item: self.accept())
        cancel = button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        load = button("Load")
        load.clicked.connect(self.accept)
        load.setDefault(True)
        layout = QVBoxLayout(self)
        m = theme.SPACE["lg"]
        layout.setContentsMargins(m, m, m, m)
        layout.setSpacing(theme.SPACE["md"])
        layout.addWidget(label("Load Preset", "title"))
        layout.addWidget(label(TIPS["presets"][1], "muted", wrap=True))
        layout.addWidget(self.list, 1)
        layout.addLayout(_dialog_buttons(cancel, load))

    def selected_name(self) -> str | None:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


class ManagePresetsDialog(QDialog):
    def __init__(self, store: PresetStore, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Manage Presets")
        self.resize(460, 420)
        self.store = store
        self.list = QListWidget()
        self.list.setObjectName("PlainList")
        self.empty = label("You haven’t saved any presets yet. Use Edit › Presets › Save Preset… "
                           "to keep your current settings for the next album.", "muted", wrap=True)
        self.rename = button("Rename…", icon_name="ph.pencil-simple")
        self.delete = button("Delete", icon_name="ph.trash")
        self.rename.clicked.connect(self._rename)
        self.delete.clicked.connect(self._delete)
        close = button("Close")
        close.clicked.connect(self.accept)
        actions = QHBoxLayout()
        actions.addWidget(self.rename)
        actions.addWidget(self.delete)
        actions.addStretch(1)
        actions.addWidget(close)
        layout = QVBoxLayout(self)
        m = theme.SPACE["lg"]
        layout.setContentsMargins(m, m, m, m)
        layout.setSpacing(theme.SPACE["md"])
        layout.addWidget(label("Manage Presets", "title"))
        layout.addWidget(self.empty)
        layout.addWidget(self.list, 1)
        layout.addLayout(actions)
        self._reload()

    def _reload(self) -> None:
        self.list.clear()
        names = self.store.names()
        self.list.addItems(names)
        if names:
            self.list.setCurrentRow(0)
        self.empty.setVisible(not names)
        for w in (self.rename, self.delete):
            w.setEnabled(bool(names))

    def _current(self) -> str | None:
        item = self.list.currentItem()
        return item.text() if item else None

    def _rename(self) -> None:
        old = self._current()
        if not old:
            return
        new, ok = QInputDialog.getText(self, "Rename Preset", "New name:", text=old)
        new = new.strip()
        if ok and new and new != old:
            if self.store.exists(new):
                QMessageBox.warning(self, "Rename Preset", f"A preset called “{new}” already exists.")
                return
            self.store.rename(old, new)
            self._reload()

    def _delete(self) -> None:
        name = self._current()
        if not name:
            return
        answer = QMessageBox.question(self, "Delete Preset", f"Delete the preset “{name}”? "
                                      "This only removes the saved settings, not any audio file.")
        if answer == QMessageBox.StandardButton.Yes:
            self.store.delete(name)
            self._reload()


def ask_preset_name(parent: QWidget, store: PresetStore) -> str | None:
    name, ok = QInputDialog.getText(parent, "Save Preset", "Name this preset so you can reuse it for your next album:")
    name = name.strip()
    if not ok or not name:
        return None
    if name in BUILT_IN:
        QMessageBox.warning(parent, "Save Preset", "That name is used by a built-in preset. Choose another name.")
        return None
    if store.exists(name):
        answer = QMessageBox.question(parent, "Save Preset", f"Replace the existing preset “{name}”?")
        if answer != QMessageBox.StandardButton.Yes:
            return None
    return name


class DefaultTargetDialog(QDialog):
    def __init__(self, value: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Default Loudness Target")
        self.setFixedWidth(460)
        self.spin = QDoubleSpinBox()
        self.spin.setRange(*TARGET_LUFS_RANGE)
        self.spin.setDecimals(1)
        self.spin.setSingleStep(0.5)
        self.spin.setSuffix(" LUFS")
        self.spin.setValue(value)
        self.spin.setToolTip(tip("target_lufs"))
        cancel = button("Cancel", "ghost")
        cancel.clicked.connect(self.reject)
        ok = button("Save")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        standard = button("Use streaming standard (-14)", "link")
        standard.clicked.connect(lambda: self.spin.setValue(-14.0))
        row = QHBoxLayout()
        row.addWidget(self.spin)
        row.addWidget(standard)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        m = theme.SPACE["lg"]
        layout.setContentsMargins(m, m, m, m)
        layout.setSpacing(theme.SPACE["md"])
        layout.addWidget(label("Default loudness target", "title"))
        layout.addWidget(label("Used for every new album. " + TIPS["target_lufs"][1], "muted", wrap=True))
        layout.addLayout(row)
        layout.addLayout(_dialog_buttons(cancel, ok))

    def value(self) -> float:
        return float(self.spin.value())
