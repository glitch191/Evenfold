"""Persistent application settings (QSettings): display mode, window state,
hardware acceleration, default mastering values, layout and panel visibility."""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QSettings

from audio.processing import DEFAULT_TARGET_LUFS, OUTPUT_SUBTYPE_CHOICES, TARGET_LUFS_RANGE

from .theme import APP_NAME, ORG_NAME

MODES = ("simple", "advanced")
PANELS = ("waveform", "spectrum", "comparison")
DEFAULT_PANELS = {
    "simple": {"waveform": True, "spectrum": False, "comparison": False},
    "advanced": {"waveform": True, "spectrum": True, "comparison": True},
}
WINDOW_STATES = ("maximized", "fullscreen", "normal")


class AppSettings:
    def __init__(self, settings: QSettings | None = None) -> None:
        self._q = settings or QSettings(ORG_NAME, APP_NAME)

    def _get(self, key: str, default, kind):
        try:
            return self._q.value(key, default, type=kind)
        except (TypeError, ValueError):
            return default

    # --- Interface ------------------------------------------------------------
    @property
    def mode(self) -> str:
        value = self._get("ui/mode", "simple", str)
        return value if value in MODES else "simple"

    @mode.setter
    def mode(self, value: str) -> None:
        self._q.setValue("ui/mode", value if value in MODES else "simple")

    @property
    def onboarding_done(self) -> bool:
        return bool(self._get("ui/onboardingDone", False, bool))

    @onboarding_done.setter
    def onboarding_done(self, value: bool) -> None:
        self._q.setValue("ui/onboardingDone", bool(value))

    @property
    def hardware_acceleration(self) -> bool:
        return bool(self._get("display/hardwareAcceleration", True, bool))

    @hardware_acceleration.setter
    def hardware_acceleration(self, value: bool) -> None:
        self._q.setValue("display/hardwareAcceleration", bool(value))

    def panel_visible(self, mode: str, panel: str) -> bool:
        return bool(self._get(f"layout/{mode}/{panel}", DEFAULT_PANELS[mode][panel], bool))

    def set_panel_visible(self, mode: str, panel: str, visible: bool) -> None:
        self._q.setValue(f"layout/{mode}/{panel}", bool(visible))

    def splitter_state(self, key: str) -> QByteArray | None:
        value = self._q.value(f"layout/splitters/{key}")
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    def set_splitter_state(self, key: str, state: QByteArray) -> None:
        self._q.setValue(f"layout/splitters/{key}", state)

    def reset_layout(self) -> None:
        self._q.remove("layout")

    # --- Window -----------------------------------------------------------------
    @property
    def window_state(self) -> str:
        value = self._get("window/state", "maximized", str)
        return value if value in WINDOW_STATES else "maximized"

    @window_state.setter
    def window_state(self, value: str) -> None:
        self._q.setValue("window/state", value)

    @property
    def window_geometry(self) -> QByteArray | None:
        value = self._q.value("window/geometry")
        return value if isinstance(value, QByteArray) and not value.isEmpty() else None

    @window_geometry.setter
    def window_geometry(self, value: QByteArray) -> None:
        self._q.setValue("window/geometry", value)

    # --- Mastering defaults ---------------------------------------------------------
    @property
    def default_target_lufs(self) -> float:
        value = float(self._get("mastering/defaultTargetLufs", DEFAULT_TARGET_LUFS, float))
        lo, hi = TARGET_LUFS_RANGE
        return min(hi, max(lo, value))

    @default_target_lufs.setter
    def default_target_lufs(self, value: float) -> None:
        self._q.setValue("mastering/defaultTargetLufs", float(value))

    @property
    def output_subtype_override(self) -> str | None:
        value = self._get("mastering/outputSubtype", "", str)
        return value if value in OUTPUT_SUBTYPE_CHOICES else None

    @output_subtype_override.setter
    def output_subtype_override(self, value: str | None) -> None:
        self._q.setValue("mastering/outputSubtype", value or "")

    @property
    def last_folder(self) -> str:
        return str(self._get("files/lastFolder", "", str))

    @last_folder.setter
    def last_folder(self, value: str) -> None:
        self._q.setValue("files/lastFolder", value)

    def sync(self) -> None:
        self._q.sync()
