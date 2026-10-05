"""Design tokens for the whole app: colours, typography, spacing and radii.

theme.qss refers to these tokens as @name and `stylesheet()` substitutes them.
Custom-painted widgets and pyqtgraph plots read the same tokens through
`color()`, `font()` and `icon()`, so all styling lives in this module and the
QSS file, never in individual widgets.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import qtawesome as qta
from PySide6.QtCore import QSize, QStandardPaths, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPainterPath, QPalette, QPixmap
from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle, QStyleFactory

APP_NAME = "Evenfold"
APP_TAGLINE = "Album Mastering"
APP_VERSION = "1.0.2"
ORG_NAME = "Evenfold"

PALETTE: dict[str, str] = {
    # Surfaces, from the window background up to pressed controls
    "bg": "#0C0E12",
    "surface": "#13161C",
    "surface_raised": "#191D25",
    "surface_hover": "#20252F",
    "surface_pressed": "#282E3A",
    "input": "#0F1216",
    "border": "#222731",
    "border_strong": "#2E3440",
    "border_hover": "#3E4655",
    # Text
    "text": "#E8EAF0",
    "text_muted": "#9AA3B5",
    "text_faint": "#646C7E",
    # Accent (primary action, "mastered" data)
    "accent": "#2FD4B8",
    "accent_hover": "#55E3CB",
    "accent_pressed": "#21B39B",
    "accent_text": "#04241F",
    "accent_disabled": "#1A423D",
    "accent_text_disabled": "#4F7A73",
    # Data series
    "before": "#7D889E",
    "after": "#2FD4B8",
    "reference": "#B794F6",
    "live": "#F6C177",
    # Status
    "good": "#3DDC97",
    "warn": "#F5B84B",
    "bad": "#FF6B6B",
    "info": "#6AA9FF",
    # Plots
    "plot_bg": "#0F1217",
    "grid": "#1B2028",
}

# Translucent variants: name -> (base colour, opacity)
TRANSLUCENT: dict[str, tuple[str, float]] = {
    "accent_soft": ("accent", 0.14),
    "accent_faint": ("accent", 0.07),
    "accent_border": ("accent", 0.55),
    "selection": ("accent", 0.12),
    "good_soft": ("good", 0.14),
    "warn_soft": ("warn", 0.10),
    "warn_border": ("warn", 0.35),
    "bad_soft": ("bad", 0.14),
    "bad_border": ("bad", 0.35),
    "info_soft": ("info", 0.14),
    "neutral_soft": ("text", 0.07),
    "reference_soft": ("reference", 0.14),
    "live_soft": ("live", 0.14),
    "overlay": ("bg", 0.72),
    "focus_ring": ("text", 0.45),
}

# Light palette for printed PDF reports
REPORT_COLORS: dict[str, str] = {
    "ink": "#1B1F27",
    "muted": "#5B6474",
    "faint": "#8A93A3",
    "rule": "#DDE1E7",
    "band": "#F3F5F8",
    "accent": "#0E9C85",
    "good": "#1E9E6A",
    "warn": "#B7791F",
    "bad": "#C53030",
}
REPORT_FONT_FILES = {"": "segoeui.ttf", "B": "segoeuib.ttf"}   # used when present (Windows)

TYPE_SIZES: dict[str, int] = {
    "display": 26,
    "title": 18,
    "heading": 15,
    "body": 13,
    "small": 12,
    "caption": 11,
    "metric": 30,
    "primary_button": 16,
}

SPACE: dict[str, int] = {"xs": 4, "sm": 8, "md": 16, "lg": 24, "xl": 32, "xxl": 48}
RADIUS: dict[str, int] = {"sm": 6, "md": 8, "lg": 12, "xl": 16}

# Fixed layout dimensions (multiples of 8)
SIDEBAR_WIDTH = 344
SIDE_PANEL_WIDTH = 360
HEADER_HEIGHT = 64

FONT_CANDIDATES = ("Segoe UI Variable Text", "Segoe UI", "Inter", "SF Pro Text", "Helvetica Neue", "Arial")

_QSS_PATH = Path(__file__).with_name("theme.qss")


def _hex_with_alpha(base: str, opacity: float) -> str:
    return f"#{round(opacity * 255):02X}{base.lstrip('#')}"


COLORS: dict[str, str] = dict(PALETTE)
COLORS.update({name: _hex_with_alpha(PALETTE[base], a) for name, (base, a) in TRANSLUCENT.items()})


# Rich-text article style (Help > Mastering Basics)
ARTICLE_CSS = (
    f"h2 {{ color: {PALETTE['text']}; font-weight: 600; margin-bottom: 8px; }}"
    f"p {{ color: {PALETTE['text_muted']}; line-height: 150%; }}"
)


def color(name: str, alpha: float | None = None) -> QColor:
    c = QColor(COLORS[name])
    if alpha is not None:
        c.setAlphaF(alpha)
    return c


@lru_cache(maxsize=1)
def font_family() -> str:
    available = set(QFontDatabase.families())
    return next((f for f in FONT_CANDIDATES if f in available), QApplication.font().family())


def font(size: str = "body", weight: QFont.Weight = QFont.Weight.Normal, tabular: bool = False) -> QFont:
    f = QFont(font_family())
    f.setPixelSize(TYPE_SIZES[size])
    f.setWeight(weight)
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    if tabular:
        f.setFeature(QFont.Tag("tnum"), 1)
    return f


def icon(name: str, color_name: str = "text_muted", active: str | None = None, size_hint: int = 0) -> QIcon:
    """qtawesome icon in theme colours (disabled state uses the faint text colour)."""
    options = {"color": COLORS[color_name], "color_disabled": COLORS["text_faint"]}
    if active:
        options["color_active"] = COLORS[active]
        options["color_selected"] = COLORS[active]
    return qta.icon(name, **options)


def _icon_file(name: str, color_name: str, px: int = 24) -> str:
    """Render an icon to a PNG so QSS sub-controls (arrows, check marks) can use it."""
    folder = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.CacheLocation)) / "theme-icons"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name.replace('.', '-')}-{color_name}-{px}.png"
    if not path.exists():
        qta.icon(name, color=COLORS[color_name]).pixmap(QSize(px, px)).save(str(path))
    return path.as_posix()


def _tokens() -> dict[str, str]:
    tokens = dict(COLORS)
    tokens["font_family"] = f'"{font_family()}"'
    tokens.update({f"size_{k}": f"{v}px" for k, v in TYPE_SIZES.items()})
    tokens.update({f"space_{k}": f"{v}px" for k, v in SPACE.items()})
    tokens.update({f"radius_{k}": f"{v}px" for k, v in RADIUS.items()})
    tokens.update({
        "icon_caret_down": _icon_file("ph.caret-down-bold", "text_muted"),
        "icon_caret_up": _icon_file("ph.caret-up-bold", "text_muted"),
        "icon_caret_down_small": _icon_file("ph.caret-down-bold", "text_muted", 16),
        "icon_check": _icon_file("ph.check-bold", "accent_text"),
        "icon_check_disabled": _icon_file("ph.check-bold", "text_faint"),
    })
    return tokens


def stylesheet() -> str:
    tokens = _tokens()
    text = _QSS_PATH.read_text(encoding="utf-8")
    pattern = re.compile(r"@(" + "|".join(sorted(map(re.escape, tokens), key=len, reverse=True)) + r")\b")
    return pattern.sub(lambda m: tokens[m.group(1)], text)


def palette() -> QPalette:
    p = QPalette()
    roles = {
        QPalette.ColorRole.Window: "bg",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "input",
        QPalette.ColorRole.AlternateBase: "surface_raised",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "surface_raised",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.Highlight: "accent",
        QPalette.ColorRole.HighlightedText: "accent_text",
        QPalette.ColorRole.ToolTipBase: "surface_raised",
        QPalette.ColorRole.ToolTipText: "text",
        QPalette.ColorRole.PlaceholderText: "text_faint",
        QPalette.ColorRole.Link: "accent",
        QPalette.ColorRole.Mid: "border",
        QPalette.ColorRole.Dark: "bg",
        QPalette.ColorRole.Light: "border_strong",
    }
    for role, name in roles.items():
        p.setColor(role, color(name))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        p.setColor(QPalette.ColorGroup.Disabled, role, color("text_faint"))
    return p


class AppStyle(QProxyStyle):
    """Fusion with one behaviour change: clicking a slider's groove jumps straight
    to that spot (and keeps dragging from there) instead of stepping a page."""

    def styleHint(self, hint, option=None, widget=None, returnData=None):
        if hint == QStyle.StyleHint.SH_Slider_AbsoluteSetButtons:
            return Qt.MouseButton.LeftButton.value
        return super().styleHint(hint, option, widget, returnData)


def apply(app: QApplication) -> None:
    app.setStyle(AppStyle(QStyleFactory.create("Fusion")))   # neutral base that honours every QSS rule
    app.setPalette(palette())
    app.setFont(font())
    app.setStyleSheet(stylesheet())


def app_icon() -> QIcon:
    """The logo: five bars of different heights settling onto one even line."""
    result = QIcon()
    for px in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(px, px)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tile = QPainterPath()
        tile.addRoundedRect(0, 0, px, px, px * 0.22, px * 0.22)
        painter.fillPath(tile, color("surface_raised"))
        heights = (0.34, 0.58, 0.46, 0.58, 0.34)
        bar_w = px * 0.1
        gap = px * 0.065
        total = bar_w * 5 + gap * 4
        x = (px - total) / 2
        centre = px * 0.5
        for h in heights:
            bar = QPainterPath()
            bar.addRoundedRect(x, centre - px * h / 2, bar_w, px * h, bar_w / 2, bar_w / 2)
            painter.fillPath(bar, color("accent"))
            x += bar_w + gap
        painter.end()
        result.addPixmap(pixmap)
    return result


def repolish(widget) -> None:
    """Re-apply QSS after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()
