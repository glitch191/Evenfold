"""First-run walkthrough: four short screens, skippable, reopened from Help."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QStackedWidget, QVBoxLayout, QWidget

from . import theme
from .tooltips import ONBOARDING
from .widgets import button, label, set_prop


class OnboardingDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Onboarding")
        self.setWindowTitle("Getting Started")
        self.setModal(True)
        self.setFixedSize(640, 520)

        self.pages = QStackedWidget()
        for step, title, body, icon_name in ONBOARDING:
            page = QWidget()
            v = QVBoxLayout(page)
            v.setContentsMargins(56, 40, 56, 16)
            v.setSpacing(theme.SPACE["md"])
            art = QLabel()
            art.setObjectName("OnboardingArt")
            art.setAlignment(Qt.AlignmentFlag.AlignCenter)
            art.setPixmap(theme.icon(icon_name, "accent").pixmap(QSize(48, 48)))
            v.addWidget(art, 0, Qt.AlignmentFlag.AlignHCenter)
            v.addSpacing(theme.SPACE["sm"])
            for text, name in ((step.upper(), "OnboardingStep"), (title, "OnboardingTitle"), (body, "OnboardingBody")):
                w = label(text, wrap=True, name=name)
                w.setAlignment(Qt.AlignmentFlag.AlignCenter)
                v.addWidget(w)
            v.addStretch(1)
            self.pages.addWidget(page)

        self.dots: list[QLabel] = []
        dots = QHBoxLayout()
        dots.setSpacing(6)
        dots.addStretch(1)
        for _ in ONBOARDING:
            dot = QLabel()
            dot.setProperty("dot", "off")
            self.dots.append(dot)
            dots.addWidget(dot)
        dots.addStretch(1)

        self.skip = button("Skip", "link")
        self.skip.clicked.connect(self.accept)
        self.back = button("Back", "ghost")
        self.back.clicked.connect(lambda: self._go(self.pages.currentIndex() - 1))
        self.next = button("Next", "primary")
        self.next.clicked.connect(self._next)
        self.next.setDefault(True)
        nav = QHBoxLayout()
        nav.setContentsMargins(32, 0, 32, 32)
        nav.setSpacing(theme.SPACE["sm"])
        nav.addWidget(self.skip)
        nav.addStretch(1)
        nav.addWidget(self.back)
        nav.addWidget(self.next)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACE["md"])
        layout.addWidget(self.pages, 1)
        layout.addLayout(dots)
        layout.addLayout(nav)

        QShortcut(QKeySequence(Qt.Key.Key_Right), self, self._next)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._go(self.pages.currentIndex() - 1))
        self._go(0)

    def _next(self) -> None:
        if self.pages.currentIndex() >= self.pages.count() - 1:
            self.accept()
        else:
            self._go(self.pages.currentIndex() + 1)

    def _go(self, index: int) -> None:
        index = max(0, min(self.pages.count() - 1, index))
        self.pages.setCurrentIndex(index)
        last = index == self.pages.count() - 1
        self.back.setVisible(index > 0)
        self.skip.setVisible(not last)
        self.next.setText("Get Started" if last else "Next")
        for i, dot in enumerate(self.dots):
            set_prop(dot, "dot", "on" if i == index else "off")
