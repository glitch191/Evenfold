"""Batch orchestration: output planning, per-track processing and parallel runs.

`process_track` is the complete pipeline for one file. `run_batch` runs a batch
with a thread pool (command line), and `BatchRunner` does the same on a Qt
QThreadPool with progress signals (GUI). Tracks are independent, so the number
of threads never changes the output.
"""

from __future__ import annotations

import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QThreadPool, Signal

from .analysis import (
    ClippingReport,
    Measurements,
    SourceFormat,
    SpectrumProfile,
    TrackAnalysis,
    WaveformOverview,
    analyze_audio,
    analyze_file,
    load_audio,
    measure,
    momentary_loudness,
    read_source_format,
    spectrum_profile,
    waveform_overview,
)
from .metadata import TrackMetadata, apply_info_strings, write_id3
from .processing import (
    Cancelled,
    EqBand,
    LimiterStats,
    MasteringSettings,
    SilenceReport,
    dither_seed,
    eq_match_bands,
    master_audio,
    needs_dither,
    quantize,
    resolve_output_subtype,
)
from .summary import ResultDescription, describe_result

TrackProgressFn = Callable[[int, float, str], None]

MASTERED_FOLDER = "mastered"

# libsndfile adds a PEAK chunk with a timestamp to float WAV files, which would
# make two identical exports differ. This sf_command value turns it off.
_SFC_SET_ADD_PEAK_CHUNK = 0x1050


def default_workers() -> int:
    """Parallel tracks: half the CPU cores, at most 4."""
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def physical_memory_bytes() -> int:
    try:
        if sys.platform == "win32":
            import ctypes

            class _MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong)] + [
                    (name, ctypes.c_ulonglong) for name in (
                        "ullTotalPhys", "ullAvailPhys", "ullTotalPageFile", "ullAvailPageFile",
                        "ullTotalVirtual", "ullAvailVirtual", "ullAvailExtendedVirtual")
                ]

            status = _MemoryStatus()
            status.dwLength = ctypes.sizeof(status)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
            return int(status.ullTotalPhys)
        return int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except Exception:
        return 8 << 30


class MemoryBudget:
    """Limits how many tracks are in memory at once, whatever the thread count.

    Processing one track peaks at roughly eight float64 copies of its audio, so
    a batch of long high-resolution files could otherwise exhaust RAM. A job
    that is larger than the whole budget still runs, alone.
    """

    def __init__(self, total_bytes: int) -> None:
        self._total = total_bytes
        self._used = 0
        self._cond = threading.Condition()

    @staticmethod
    def estimate(fmt: SourceFormat) -> int:
        return fmt.frames * fmt.channels * 8 * 8

    def acquire(self, amount: int, cancel: Callable[[], bool] | None = None) -> None:
        with self._cond:
            while self._used > 0 and self._used + amount > self._total:
                if cancel and cancel():
                    raise Cancelled()
                self._cond.wait(timeout=0.2)
            self._used += amount

    def release(self, amount: int) -> None:
        with self._cond:
            self._used -= amount
            self._cond.notify_all()


MEMORY_BUDGET = MemoryBudget(physical_memory_bytes() // 2)


def _key(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


@dataclass
class TrackJob:
    path: str
    title: str = ""
    artist: str = ""
    track_number: int | None = None
    track_total: int | None = None
    analysis: TrackAnalysis | None = None
    output_path: str | None = None


@dataclass
class TrackResult:
    path: str
    dry_run: bool
    fmt: SourceFormat | None = None
    output_path: str | None = None
    output_subtype: str = ""
    before: Measurements | None = None
    after: Measurements | None = None
    input_lufs: float = float("nan")
    gain_db: float = 0.0
    makeup_db: float = 0.0
    limiter: LimiterStats = field(default_factory=LimiterStats)
    clipping: ClippingReport = field(default_factory=ClippingReport)
    eq_bands: tuple[EqBand, ...] = ()
    eq_reference: str | None = None
    is_eq_reference: bool = False
    silence: SilenceReport | None = None
    dithered: bool = False
    overview_after: WaveformOverview | None = None
    spectrum_after: SpectrumProfile | None = None
    momentary_after: np.ndarray | None = None
    error: str = ""
    description: ResultDescription | None = None

    @property
    def ok(self) -> bool:
        return not self.error


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

def plan_outputs(jobs: list[TrackJob], settings: MasteringSettings) -> None:
    """Assign each job a unique output path that can never be one of the inputs."""
    inputs = {_key(job.path) for job in jobs}
    taken: set[str] = set()
    for job in jobs:
        source = Path(job.path)
        folder = Path(settings.output_dir) if settings.output_dir else source.parent / MASTERED_FOLDER
        base = f"{source.stem}{settings.suffix}"
        candidate = folder / f"{base}.wav"
        n = 2
        while _key(candidate) in inputs or _key(candidate) in taken:
            candidate = folder / f"{base} ({n}).wav"
            n += 1
        taken.add(_key(candidate))
        job.output_path = str(candidate)


def output_folders(jobs: list[TrackJob]) -> list[str]:
    folders: dict[str, str] = {}
    for job in jobs:
        if job.output_path:
            parent = str(Path(job.output_path).parent)
            folders.setdefault(_key(parent), parent)
    return list(folders.values())


def reference_spectrum(jobs: list[TrackJob], settings: MasteringSettings) -> SpectrumProfile | None:
    if not (settings.eq_match and settings.eq_reference):
        return None
    for job in jobs:
        if _key(job.path) == _key(settings.eq_reference):
            return job.analysis.spectrum if job.analysis else analyze_file(job.path).spectrum
    return analyze_file(settings.eq_reference).spectrum


# ---------------------------------------------------------------------------
# One track
# ---------------------------------------------------------------------------

def write_output(path: str | Path, data: np.ndarray, fmt: SourceFormat, subtype: str, meta: TrackMetadata) -> None:
    """Write atomically: a hidden partial file is renamed into place once complete."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(f".{path.stem}.partial{path.suffix}")
    container = fmt.container
    if data.nbytes > 0xFFFFFFFF - (1 << 20):
        container = "RF64"            # plain WAV cannot hold more than 4 GB
    try:
        with sf.SoundFile(str(partial), "w", fmt.samplerate, data.shape[1], subtype=subtype, format=container) as f:
            sf._snd.sf_command(f._file, _SFC_SET_ADD_PEAK_CHUNK, sf._ffi.NULL, 0)
            apply_info_strings(f, meta)
            f.write(data)
        if container != "RF64":
            write_id3(partial, meta)
        os.replace(partial, path)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise


def _analysis_matches(analysis: TrackAnalysis | None, fmt: SourceFormat) -> bool:
    return analysis is not None and analysis.fmt == fmt


def process_track(
    job: TrackJob,
    settings: MasteringSettings,
    reference: SpectrumProfile | None = None,
    dry_run: bool = False,
    progress: Callable[[float, str], None] | None = None,
    cancel: Callable[[], bool] | None = None,
) -> TrackResult:
    """Run the full pipeline on one file. Never modifies the source file.

    In dry-run mode everything is computed in memory and nothing is written.
    """
    fmt = job.analysis.fmt if job.analysis else read_source_format(job.path)
    budget = MemoryBudget.estimate(fmt)
    if progress:
        progress(0.0, "Waiting for memory")
    MEMORY_BUDGET.acquire(budget, cancel)
    try:
        return _process_track(job, settings, reference, dry_run, progress, cancel)
    finally:
        MEMORY_BUDGET.release(budget)


def _process_track(
    job: TrackJob,
    settings: MasteringSettings,
    reference: SpectrumProfile | None,
    dry_run: bool,
    progress: Callable[[float, str], None] | None,
    cancel: Callable[[], bool] | None,
) -> TrackResult:
    def step(fraction: float, text: str) -> None:
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(fraction, text)

    step(0.0, "Reading file")
    audio, fmt = load_audio(job.path)
    sr = fmt.samplerate
    analysis = job.analysis
    if not _analysis_matches(analysis, fmt):
        analysis = analyze_audio(audio, fmt, lambda f, t: step(0.02 + 0.2 * f, t))

    is_reference = bool(settings.eq_match and settings.eq_reference and _key(settings.eq_reference) == _key(job.path))
    eq_bands: tuple[EqBand, ...] = ()
    if reference is not None and not is_reference:
        eq_bands = eq_match_bands(analysis.spectrum, reference, settings.eq_strength)

    outcome = master_audio(audio, sr, settings, eq_bands, lambda f, t: step(0.22 + 0.55 * f, t), cancel)
    del audio

    out_subtype = resolve_output_subtype(fmt.subtype, settings.output_subtype)
    dither = needs_dither(fmt.subtype, out_subtype)

    step(0.8, "Measuring the result")
    after = measure(outcome.audio, sr)
    overview = waveform_overview(outcome.audio, sr)
    spectrum = spectrum_profile(outcome.audio, sr)
    momentary = momentary_loudness(outcome.audio, sr)

    if not dry_run:
        step(0.9, "Saving")
        data = quantize(outcome.audio, out_subtype, dither, dither_seed(outcome.audio))
        del outcome.audio
        meta = TrackMetadata(
            title=job.title, artist=job.artist, album=settings.album, album_artist=settings.album_artist,
            track_number=job.track_number, track_total=job.track_total,
        )
        write_output(job.output_path, data, fmt, out_subtype, meta)
        del data

    result = TrackResult(
        path=job.path,
        dry_run=dry_run,
        fmt=fmt,
        output_path=None if dry_run else job.output_path,
        output_subtype=out_subtype,
        before=analysis.measurements,
        after=after,
        input_lufs=outcome.input_lufs,
        gain_db=outcome.gain_db,
        makeup_db=outcome.makeup_db,
        limiter=outcome.limiter,
        clipping=analysis.clipping,
        eq_bands=eq_bands,
        eq_reference=settings.eq_reference if settings.eq_match else None,
        is_eq_reference=is_reference,
        silence=outcome.silence,
        dithered=dither,
        overview_after=overview,
        spectrum_after=spectrum,
        momentary_after=momentary,
    )
    result.description = describe_result(result, settings.target_lufs, settings.ceiling_dbtp)
    if progress:            # no cancel check here: the file is already saved, so keep its result
        progress(1.0, "Done")
    return result


def failed_result(job: TrackJob, error: BaseException, dry_run: bool) -> TrackResult:
    message = str(error) or error.__class__.__name__
    if isinstance(error, MemoryError):
        message = "There wasn't enough memory to process this file. Close other apps and try again."
    elif isinstance(error, PermissionError):
        message = ("The mastered file couldn't be saved. Check that the output folder can be written to "
                   "and that the file isn't open in another app.")
    result = TrackResult(path=job.path, dry_run=dry_run, error=message)
    result.description = ResultDescription("This file couldn't be processed.", [], [message])
    return result


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------

def run_batch(
    jobs: list[TrackJob],
    settings: MasteringSettings,
    dry_run: bool = False,
    workers: int | None = None,
    progress: TrackProgressFn | None = None,
) -> list[TrackResult]:
    """Process a batch in parallel threads (command-line use). Results keep the job order."""
    if not dry_run:
        plan_outputs(jobs, settings)
    reference = reference_spectrum(jobs, settings)

    def work(index: int) -> TrackResult:
        job = jobs[index]
        try:
            return process_track(job, settings, reference, dry_run,
                                 (lambda f, t: progress(index, f, t)) if progress else None)
        except Exception as exc:  # reported per track, the batch goes on
            return failed_result(job, exc, dry_run)

    with ThreadPoolExecutor(max_workers=workers or default_workers()) as pool:
        return list(pool.map(work, range(len(jobs))))


class _Relay(QObject):
    progress = Signal(int, float, str)
    finished = Signal(int, object)


class BatchRunner(QObject):
    """Runs a batch on a QThreadPool and reports progress through Qt signals."""

    trackProgress = Signal(int, float, str)     # index, fraction, stage text
    trackFinished = Signal(int, object)         # index, TrackResult (None when cancelled)
    progress = Signal(float)                    # overall fraction
    finished = Signal(bool, bool)               # dry_run, cancelled

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._relay = _Relay(self)
        self._relay.progress.connect(self._on_progress)
        self._relay.finished.connect(self._on_finished)
        self._cancel = threading.Event()
        self._fractions: list[float] = []
        self._remaining = 0
        self._dry_run = False
        self.results: list[TrackResult | None] = []

    @property
    def running(self) -> bool:
        return self._remaining > 0

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    def start(self, jobs: list[TrackJob], settings: MasteringSettings, dry_run: bool,
              reference: SpectrumProfile | None, workers: int | None = None) -> None:
        if self.running:
            raise RuntimeError("A batch is already running.")
        self._cancel.clear()
        self._dry_run = dry_run
        self._fractions = [0.0] * len(jobs)
        self._remaining = len(jobs)
        self.results = [None] * len(jobs)
        self._pool.setMaxThreadCount(workers or default_workers())
        settings = settings.copy()
        for index, job in enumerate(jobs):
            self._pool.start(self._make_task(index, job, settings, reference, dry_run))

    def cancel(self) -> None:
        self._cancel.set()

    def _make_task(self, index: int, job: TrackJob, settings: MasteringSettings,
                   reference: SpectrumProfile | None, dry_run: bool) -> Callable[[], None]:
        relay, cancel = self._relay, self._cancel

        def task() -> None:
            try:
                result = process_track(job, settings, reference, dry_run,
                                       lambda f, t: relay.progress.emit(index, f, t), cancel.is_set)
            except Cancelled:
                result = None
            except Exception as exc:
                result = failed_result(job, exc, dry_run)
            relay.finished.emit(index, result)
        return task

    def _on_progress(self, index: int, fraction: float, text: str) -> None:
        self._fractions[index] = fraction
        self.trackProgress.emit(index, fraction, text)
        self.progress.emit(sum(self._fractions) / max(1, len(self._fractions)))

    def _on_finished(self, index: int, result: TrackResult | None) -> None:
        self._fractions[index] = 1.0
        self.results[index] = result
        self._remaining -= 1
        self.trackFinished.emit(index, result)
        self.progress.emit(sum(self._fractions) / max(1, len(self._fractions)))
        if self._remaining == 0:
            self.finished.emit(self._dry_run, self._cancel.is_set())
