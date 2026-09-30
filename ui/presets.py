"""Mastering presets: built-in starting points plus user presets saved as JSON
files in the app's data folder."""

from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QStandardPaths

from audio.processing import MasteringSettings

from .theme import APP_NAME

PRESET_FORMAT = 1

BUILT_IN: dict[str, dict] = {
    "Streaming Standard (-14 LUFS)": {"target_lufs": -14.0, "ceiling_dbtp": -1.0},
    "Gentle & Dynamic (-16 LUFS)": {"target_lufs": -16.0, "ceiling_dbtp": -1.0},
    "Loud (-10 LUFS)": {"target_lufs": -10.0, "ceiling_dbtp": -1.0},
}

_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class PresetStore:
    def __init__(self, folder: Path | None = None) -> None:
        base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
        self.folder = folder or Path(base) / "presets"

    def _path(self, name: str) -> Path:
        safe = _UNSAFE.sub("_", name).strip(" .") or "Preset"
        return self.folder / f"{safe}.json"

    def names(self) -> list[str]:
        if not self.folder.exists():
            return []
        names = []
        for path in sorted(self.folder.glob("*.json"), key=lambda p: p.stem.lower()):
            try:
                names.append(json.loads(path.read_text(encoding="utf-8")).get("name") or path.stem)
            except (OSError, ValueError):
                continue
        return names

    def exists(self, name: str) -> bool:
        return name in BUILT_IN or self._path(name).exists()

    def save(self, name: str, settings: MasteringSettings) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        data = {"app": APP_NAME, "format": PRESET_FORMAT, "name": name, "settings": settings.preset_dict()}
        self._path(name).write_text(json.dumps(data, indent=2), encoding="utf-8")

    def load(self, name: str) -> dict:
        if name in BUILT_IN:
            return dict(BUILT_IN[name])
        data = json.loads(self._path(name).read_text(encoding="utf-8"))
        settings = data.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("This preset file is damaged.")
        return settings

    def delete(self, name: str) -> None:
        self._path(name).unlink(missing_ok=True)

    def rename(self, old: str, new: str) -> None:
        data = json.loads(self._path(old).read_text(encoding="utf-8"))
        data["name"] = new
        self.folder.mkdir(parents=True, exist_ok=True)
        self._path(new).write_text(json.dumps(data, indent=2), encoding="utf-8")
        if self._path(new) != self._path(old):
            self._path(old).unlink(missing_ok=True)
