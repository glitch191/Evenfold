"""Track metadata for exported WAV files.

Two tag formats are written so the information shows up in as many players and
DAWs as possible: a RIFF INFO list (written by libsndfile while the file is
created) and an ID3 chunk (written by mutagen afterwards).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import soundfile as sf
from mutagen.id3 import TALB, TIT2, TPE1, TPE2, TRCK, TSSE
from mutagen.wave import WAVE

SOFTWARE_TAG = "Evenfold"

_LEADING_NUMBER = re.compile(r"^\s*(\d{1,3})(?:\s*[-_.)]\s*|\s+)(.+)$")


@dataclass
class TrackMetadata:
    title: str = ""
    artist: str = ""
    album: str = ""
    album_artist: str = ""
    track_number: int | None = None
    track_total: int | None = None

    def is_empty(self) -> bool:
        return not (self.title or self.artist or self.album or self.album_artist or self.track_number)

    @property
    def track_text(self) -> str:
        if not self.track_number:
            return ""
        return f"{self.track_number}/{self.track_total}" if self.track_total else str(self.track_number)


def apply_info_strings(sound_file: sf.SoundFile, meta: TrackMetadata) -> None:
    """Set RIFF INFO strings on a SoundFile opened for writing (before any audio is written)."""
    sound_file.software = SOFTWARE_TAG
    if meta.title:
        sound_file.title = meta.title
    if meta.artist or meta.album_artist:
        sound_file.artist = meta.artist or meta.album_artist
    if meta.album:
        sound_file.album = meta.album
    if meta.track_number:
        sound_file.tracknumber = str(meta.track_number)


def write_id3(path: str | Path, meta: TrackMetadata) -> None:
    """Add an ID3 chunk with the track's metadata to a finished WAV file."""
    wave = WAVE(str(path))
    if wave.tags is None:
        wave.add_tags()
    tags = wave.tags
    tags.add(TSSE(encoding=3, text=SOFTWARE_TAG))
    if meta.title:
        tags.add(TIT2(encoding=3, text=meta.title))
    if meta.artist or meta.album_artist:
        tags.add(TPE1(encoding=3, text=meta.artist or meta.album_artist))
    if meta.album_artist:
        tags.add(TPE2(encoding=3, text=meta.album_artist))
    if meta.album:
        tags.add(TALB(encoding=3, text=meta.album))
    if meta.track_number:
        tags.add(TRCK(encoding=3, text=meta.track_text))
    wave.save()


def _first_text(tags, key: str) -> str:
    frame = tags.get(key) if tags is not None else None
    return str(frame.text[0]).strip() if frame is not None and frame.text else ""


def read_metadata(path: str | Path) -> TrackMetadata:
    """Best-effort read of existing tags (ID3 first, then RIFF INFO)."""
    meta = TrackMetadata()
    try:
        tags = WAVE(str(path)).tags
        meta.title = _first_text(tags, "TIT2")
        meta.artist = _first_text(tags, "TPE1")
        meta.album = _first_text(tags, "TALB")
        meta.album_artist = _first_text(tags, "TPE2")
        number = _first_text(tags, "TRCK").split("/")[0]
        meta.track_number = int(number) if number.isdigit() else None
    except Exception:
        pass
    try:
        with sf.SoundFile(str(path)) as f:
            meta.title = meta.title or (f.title or "").strip()
            meta.artist = meta.artist or (f.artist or "").strip()
            meta.album = meta.album or (f.album or "").strip()
            number = (f.tracknumber or "").split("/")[0].strip()
            if meta.track_number is None and number.isdigit():
                meta.track_number = int(number)
    except Exception:
        pass
    return meta


def guess_from_filename(path: str | Path) -> tuple[int | None, str]:
    """'03 - Northern Lights.wav' -> (3, 'Northern Lights')."""
    stem = Path(path).stem.replace("_", " ").strip()
    match = _LEADING_NUMBER.match(stem)
    if match:
        return int(match.group(1)), match.group(2).strip()
    return None, stem
