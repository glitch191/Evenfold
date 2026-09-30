"""The album being mastered: tracks, settings, selection and background work.

Views never talk to each other. They observe the Session's signals and call
its methods. Analysis of newly added files and the mastering batch both run on
thread pools, so the window stays responsive.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QThreadPool, Signal

from audio.analysis import SourceFormat, TrackAnalysis, UnsupportedFileError, analyze_file, read_source_format
from audio.batch import (
    MEMORY_BUDGET,
    BatchRunner,
    MemoryBudget,
    TrackJob,
    TrackResult,
    default_workers,
    output_folders,
    plan_outputs,
)
from audio.metadata import TrackMetadata, guess_from_filename, read_metadata
from audio.processing import MasteringSettings
from audio.summary import batch_summary

from .settings import AppSettings

STATUS_LABELS = {
    "analyzing": "Analyzing",
    "waiting": "Waiting",
    "processing": "Processing",
    "done": "Done",
    "previewed": "Previewed",
    "failed": "Needs attention",
}

# Settings that change the exported files (audio, tags or location). Editing any of
# them after mastering makes the existing results outdated.
_OUTPUT_FIELDS = ("target_lufs", "ceiling_dbtp", "eq_match", "eq_reference", "eq_strength",
                  "even_spacing", "gap_s", "output_subtype", "output_dir", "album", "album_artist")


def _natural_key(path: str) -> list:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", Path(path).name)]


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


@dataclass(eq=False)
class Track:
    uid: int
    path: str
    title: str
    track_number: int
    artist: str = ""
    fmt: SourceFormat | None = None
    analysis: TrackAnalysis | None = None
    result: TrackResult | None = None
    status: str = "analyzing"
    progress: float = 0.0
    stage: str = ""
    error: str = ""

    @property
    def filename(self) -> str:
        return Path(self.path).name

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]

    @property
    def usable(self) -> bool:
        return self.analysis is not None


class _AnalysisRelay(QObject):
    finished = Signal(int, object, str, object)   # uid, TrackAnalysis | None, error, TrackMetadata | None


class Session(QObject):
    tracksChanged = Signal()                # added, removed or reordered
    trackChanged = Signal(int)              # uid: status, progress, analysis, result or metadata
    selectionChanged = Signal(int)          # uid, or -1 when nothing is selected
    settingsChanged = Signal()
    aboutToStart = Signal(bool)             # dry run; emitted before any file is touched
    busyChanged = Signal(bool)              # a batch started or stopped
    batchProgress = Signal(float, str)      # overall fraction, stage text
    batchFinished = Signal(bool, bool, str)  # dry run, cancelled, plain-language summary
    notice = Signal(str, str)               # level ("info" | "warn"), plain-language message

    def __init__(self, app_settings: AppSettings, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.app_settings = app_settings
        self.settings = MasteringSettings(
            target_lufs=app_settings.default_target_lufs,
            output_subtype=app_settings.output_subtype_override,
        )
        self.tracks: list[Track] = []
        self.selected_uid = -1
        self.results_stale = False
        self.last_run_dry: bool | None = None
        self.last_run_ok = True
        self.last_summary = ""
        self.batch_settings: MasteringSettings | None = None   # what the latest results were made with
        self.output_dirs: list[str] = []
        self._next_uid = 1
        self._batch_uids: list[int] = []
        self._batch_jobs: list[TrackJob] = []
        self._batch_snapshot: tuple = ()
        self._stale_before_batch = False

        self._analysis_pool = QThreadPool(self)
        self._analysis_pool.setMaxThreadCount(default_workers())
        self._relay = _AnalysisRelay(self)
        self._relay.finished.connect(self._on_analyzed)

        self.runner = BatchRunner(self)
        self.runner.trackProgress.connect(self._on_track_progress)
        self.runner.trackFinished.connect(self._on_track_finished)
        self.runner.progress.connect(self._on_batch_progress)
        self.runner.finished.connect(self._on_batch_finished)
        self._stage = ""

    # ------------------------------------------------------------------ queries
    def track(self, uid: int) -> Track | None:
        return next((t for t in self.tracks if t.uid == uid), None)

    def selected(self) -> Track | None:
        return self.track(self.selected_uid)

    def index_of(self, uid: int) -> int:
        return next((i for i, t in enumerate(self.tracks) if t.uid == uid), -1)

    @property
    def busy(self) -> bool:
        return self.runner.running

    @property
    def analyzing(self) -> bool:
        return any(t.status == "analyzing" for t in self.tracks)

    @property
    def can_master(self) -> bool:
        return bool(self.tracks) and not self.busy and not self.analyzing and any(t.usable for t in self.tracks)

    @property
    def has_results(self) -> bool:
        return any(t.result is not None and t.result.ok for t in self.tracks)

    def reference_track(self) -> Track | None:
        if not self.settings.eq_reference:
            return None
        key = _norm(self.settings.eq_reference)
        return next((t for t in self.tracks if _norm(t.path) == key), None)

    # ------------------------------------------------------------------ tracks
    def add_paths(self, paths: list[str]) -> int:
        """Add WAV files (folders are searched one level deep). Returns how many were added."""
        if self.busy:
            self.notice.emit("warn", "Wait for mastering to finish before adding more tracks.")
            return 0
        found: list[str] = []
        for raw in paths:
            p = Path(raw)
            if p.is_dir():
                found.extend(str(f) for f in p.iterdir() if f.is_file() and f.suffix.lower() == ".wav")
            elif p.is_file() and p.suffix.lower() == ".wav":
                found.append(str(p))
        skipped_other = sum(1 for raw in paths if Path(raw).is_file() and Path(raw).suffix.lower() != ".wav")
        if not self.tracks:
            self._reset_run_state()

        existing = {_norm(t.path) for t in self.tracks}
        new_paths = []
        for path in sorted(found, key=_natural_key):
            if _norm(path) not in existing:
                existing.add(_norm(path))
                new_paths.append(path)

        used_numbers = {t.track_number for t in self.tracks}
        next_number = max(used_numbers, default=0) + 1
        for path in new_paths:
            number, title = guess_from_filename(path)
            if number is None or number in used_numbers:
                number = next_number
            used_numbers.add(number)
            next_number = max(used_numbers) + 1
            track = Track(uid=self._next_uid, path=path, title=title, track_number=number)
            self._next_uid += 1
            self.tracks.append(track)
            self._start_analysis(track)

        if skipped_other:
            noun = "file was" if skipped_other == 1 else "files were"
            self.notice.emit("warn", f"{skipped_other} {noun} skipped because only WAV files can be mastered.")
        if new_paths:
            self._sort()
            self.tracksChanged.emit()
            if self.selected() is None:
                self.select(self.tracks[0].uid)
        return len(new_paths)

    def remove(self, uid: int) -> None:
        if self.busy:
            return
        index = self.index_of(uid)
        if index < 0:
            return
        track = self.tracks.pop(index)
        if self.reference_track() is None and self.settings.eq_reference == track.path:
            self.settings.eq_reference = None
            self.settingsChanged.emit()
        self.tracksChanged.emit()
        if uid == self.selected_uid:
            neighbour = self.tracks[min(index, len(self.tracks) - 1)].uid if self.tracks else -1
            self.select(neighbour)

    def _reset_run_state(self) -> None:
        """Forget the outcome of the previous run (a new album is starting)."""
        self.output_dirs = []
        self.results_stale = False
        self.last_run_dry = None
        self.last_run_ok = True
        self.last_summary = ""
        self.batch_settings = None

    def clear(self) -> None:
        if self.busy:
            return
        self.tracks.clear()
        self._reset_run_state()
        if self.settings.eq_reference:
            self.settings.eq_reference = None
            self.settingsChanged.emit()
        self.tracksChanged.emit()
        self.select(-1)

    def select(self, uid: int) -> None:
        if uid != self.selected_uid:
            self.selected_uid = uid if self.track(uid) else -1
            self.selectionChanged.emit(self.selected_uid)

    def set_track_meta(self, uid: int, *, title: str | None = None, artist: str | None = None,
                       number: int | None = None) -> None:
        track = self.track(uid)
        if track is None:
            return
        before = (track.title, track.artist, track.track_number)
        if title is not None:
            track.title = title.strip()
        if artist is not None:
            track.artist = artist.strip()
        if number is not None and number != track.track_number:
            track.track_number = max(1, number)
            self._sort()
            self.tracksChanged.emit()
        if (track.title, track.artist, track.track_number) != before and self.has_results:
            self.results_stale = True       # the tags in the exported files are now out of date
        self.trackChanged.emit(uid)

    def _sort(self) -> None:
        self.tracks.sort(key=lambda t: t.track_number)

    # ------------------------------------------------------------------ settings
    def update_settings(self, **changes) -> None:
        changed = {k for k, v in changes.items() if getattr(self.settings, k) != v}
        if not changed:
            return
        for key in changed:
            setattr(self.settings, key, changes[key])
        if self.settings.eq_match and not self.settings.eq_reference and self.tracks:
            self.settings.eq_reference = (self.selected() or self.tracks[0]).path
        if changed & set(_OUTPUT_FIELDS) and self.has_results:
            self.results_stale = True
        self.settingsChanged.emit()

    def apply_preset(self, values: dict) -> None:
        before = self.settings.preset_dict()
        self.settings.apply_preset(values)
        if self.settings.eq_match and not self.settings.eq_reference and self.tracks:
            self.settings.eq_reference = (self.selected() or self.tracks[0]).path
        if self.settings.preset_dict() != before and self.has_results:
            self.results_stale = True
        self.settingsChanged.emit()

    def reset_settings(self) -> None:
        defaults = MasteringSettings(target_lufs=self.app_settings.default_target_lufs,
                                     output_subtype=self.app_settings.output_subtype_override)
        self.update_settings(**{k: getattr(defaults, k) for k in (
            "target_lufs", "ceiling_dbtp", "eq_match", "eq_strength", "even_spacing", "gap_s", "output_subtype")})

    # ------------------------------------------------------------------ analysis
    def _start_analysis(self, track: Track) -> None:
        relay, uid, path = self._relay, track.uid, track.path

        def task() -> None:
            analysis, error, meta = None, "", None
            try:
                fmt = read_source_format(path)
                budget = MemoryBudget.estimate(fmt) // 2
                MEMORY_BUDGET.acquire(budget)
                try:
                    analysis = analyze_file(path)
                finally:
                    MEMORY_BUDGET.release(budget)
                meta = read_metadata(path)
            except UnsupportedFileError as exc:
                error = str(exc)
            except MemoryError:
                error = "There wasn't enough memory to analyze this file. Close other apps and add it again."
            except Exception as exc:
                error = f"This file couldn't be read. ({exc})"
            relay.finished.emit(uid, analysis, error, meta)

        self._analysis_pool.start(task)

    def _on_analyzed(self, uid: int, analysis: TrackAnalysis | None, error: str, meta: TrackMetadata | None) -> None:
        track = self.track(uid)
        if track is None:
            return
        if analysis is None:
            track.status, track.error = "failed", error
        else:
            track.analysis, track.fmt, track.status = analysis, analysis.fmt, "waiting"
            if meta is not None:
                if meta.title:
                    track.title = meta.title
                if meta.artist:
                    track.artist = meta.artist
                if meta.album and not self.settings.album:
                    self.settings.album = meta.album
                    self.settingsChanged.emit()
        self.trackChanged.emit(uid)
        if not self.analyzing:
            self.busyChanged.emit(self.busy)

    # ------------------------------------------------------------------ batch
    @property
    def writing(self) -> bool:
        """A real (not preview) batch is running, so output files may be replaced at any moment."""
        return self.busy and not self.runner.dry_run

    def _snapshot(self) -> tuple:
        """Everything that shapes the exported files, to tell whether results are still current."""
        settings = tuple(getattr(self.settings, name) for name in _OUTPUT_FIELDS)
        tracks = tuple((t.uid, t.title, t.artist, t.track_number) for t in self.tracks)
        return settings, tracks

    def start(self, dry_run: bool) -> bool:
        if not self.can_master:
            return False
        self.aboutToStart.emit(dry_run)     # lets the player release files that are about to be replaced
        usable = [t for t in self.tracks if t.usable]
        total = len(usable)
        jobs = [TrackJob(path=t.path, title=t.title, artist=t.artist, track_number=t.track_number,
                         track_total=total, analysis=t.analysis) for t in usable]
        settings = self.settings.copy()
        if settings.eq_match and self.reference_track() is None:
            settings.eq_match = False
        if not dry_run:
            plan_outputs(jobs, settings)
        reference = None
        if settings.eq_match:
            ref = self.reference_track()
            reference = ref.analysis.spectrum if ref and ref.analysis else None
            settings.eq_match = reference is not None
        self._batch_uids = [t.uid for t in usable]
        self._batch_jobs = jobs
        self._batch_snapshot = self._snapshot()
        self._stale_before_batch = self.results_stale
        self.batch_settings = settings
        for t in usable:
            t.status, t.progress, t.stage = "processing", 0.0, "Waiting to start"
        self._stage = "Starting"
        # Start first, so `busy` is already true for everything that reacts to the updates below.
        self.runner.start(jobs, settings, dry_run, reference)
        self.busyChanged.emit(True)
        for t in usable:
            self.trackChanged.emit(t.uid)
        return True

    def cancel(self) -> None:
        self.runner.cancel()

    def _on_track_progress(self, index: int, fraction: float, text: str) -> None:
        track = self.track(self._batch_uids[index])
        if track is not None:
            track.progress, track.stage = fraction, text
            self._stage = f"{track.title}: {text}"
            self.trackChanged.emit(track.uid)

    def _on_track_finished(self, index: int, result: TrackResult | None) -> None:
        track = self.track(self._batch_uids[index])
        if track is None:
            return
        track.progress, track.stage = 1.0, ""
        if result is None:              # cancelled before it finished: back to the previous state
            previous = track.result
            if previous is None:
                track.status = "waiting"
            elif previous.error:
                track.status = "failed"
            else:
                track.status = "previewed" if previous.dry_run else "done"
        else:
            track.result = result
            track.status = "failed" if result.error else ("previewed" if result.dry_run else "done")
            track.error = result.error
        self.trackChanged.emit(track.uid)

    def _on_batch_progress(self, fraction: float) -> None:
        self.batchProgress.emit(fraction, self._stage)

    def _on_batch_finished(self, dry_run: bool, cancelled: bool) -> None:
        results = [r for r in self.runner.results if r is not None]
        self.last_run_dry = dry_run
        changed_meanwhile = self._snapshot() != self._batch_snapshot
        self.results_stale = changed_meanwhile or (cancelled and self._stale_before_batch)
        if not dry_run:
            saved = output_folders([j for j, r in zip(self._batch_jobs, self.runner.results) if r is not None and r.ok])
            self.output_dirs = list(dict.fromkeys(saved + (self.output_dirs if cancelled else [])))
        if cancelled:
            summary = "Stopped. Tracks that were already finished are kept."
        else:
            summary = batch_summary(results, self.batch_settings.target_lufs, dry_run)
        self.last_summary = summary
        self.last_run_ok = not cancelled and all(r.ok for r in results)
        self.busyChanged.emit(False)
        self.batchFinished.emit(dry_run, cancelled, summary)
