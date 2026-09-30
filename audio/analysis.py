"""Audio analysis: source format, loudness, true peak, clipping detection, and
the display data (waveform overview, spectrum, momentary loudness) used by the UI.

Every function works on float64 sample buffers shaped (frames, channels).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import resample_poly

ProgressFn = Callable[[float, str], None]
CancelFn = Callable[[], bool]

TRUE_PEAK_OVERSAMPLING = 4          # ITU-R BS.1770-4 annex 2: 4x oversampling
LOUDNESS_BLOCK_S = 0.4
MOMENTARY_WINDOW_S = 0.4
MOMENTARY_HOP_S = 0.01
FLOOR_DB = -120.0                   # stands in for -inf in displays and reports
CLIP_MIN_RUN = 3                    # consecutive full-scale samples that count as clipping
SPECTRUM_POINTS = 256
SPECTRUM_SMOOTHING_OCT = 1.0 / 6.0

_CHUNK_FRAMES = 1 << 18
_CHUNK_CONTEXT = 64                 # frames of context around each oversampling chunk
_MAX_SPECTRUM_SEGMENTS = 1200

# subtype -> (label, bits, is_float)
SUBTYPES: dict[str, tuple[str, int, bool]] = {
    "PCM_U8": ("8-bit", 8, False),
    "PCM_S8": ("8-bit", 8, False),
    "PCM_16": ("16-bit", 16, False),
    "PCM_24": ("24-bit", 24, False),
    "PCM_32": ("32-bit", 32, False),
    "FLOAT": ("32-bit float", 32, True),
    "DOUBLE": ("64-bit float", 64, True),
}
SUPPORTED_CONTAINERS = ("WAV", "WAVEX", "RF64")

# Frequency bands (Hz) that describe a track's tonal balance for EQ matching.
TONE_BANDS: tuple[tuple[float, float], ...] = (
    (25.0, 150.0),
    (150.0, 500.0),
    (500.0, 2000.0),
    (2000.0, 6000.0),
    (6000.0, 16000.0),
)


class UnsupportedFileError(Exception):
    """A file that cannot be mastered without changing its format."""


def to_db(value: float) -> float:
    """Linear amplitude to dB, floored at FLOOR_DB."""
    if not value > 1e-6:
        return FLOOR_DB
    return 20.0 * math.log10(value)


def format_samplerate(samplerate: int) -> str:
    return f"{samplerate / 1000:g} kHz"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


# ---------------------------------------------------------------------------
# Source format
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceFormat:
    path: str
    samplerate: int
    channels: int
    frames: int
    subtype: str
    container: str

    @property
    def duration_s(self) -> float:
        return self.frames / self.samplerate if self.samplerate else 0.0

    @property
    def bits(self) -> int:
        return SUBTYPES[self.subtype][1]

    @property
    def is_float(self) -> bool:
        return SUBTYPES[self.subtype][2]

    @property
    def bit_depth_label(self) -> str:
        return SUBTYPES[self.subtype][0]

    @property
    def samplerate_label(self) -> str:
        return format_samplerate(self.samplerate)

    @property
    def channels_label(self) -> str:
        return {1: "Mono", 2: "Stereo"}.get(self.channels, f"{self.channels} channels")

    @property
    def summary(self) -> str:
        return f"{self.bit_depth_label} · {self.samplerate_label} · {self.channels_label}"


def read_source_format(path: str | Path) -> SourceFormat:
    try:
        info = sf.info(str(path))
    except (RuntimeError, OSError) as exc:
        raise UnsupportedFileError(
            "This file couldn't be opened as a WAV file. It may be damaged, "
            "or it may be a different kind of audio file renamed to .wav."
        ) from exc
    if info.format not in SUPPORTED_CONTAINERS:
        raise UnsupportedFileError(
            f"This is a {info.format_info} file, not a WAV file. Export it as WAV and add it again."
        )
    if info.subtype not in SUBTYPES:
        raise UnsupportedFileError(
            f"This WAV file uses a compressed encoding ({info.subtype_info}) that can't be "
            "mastered without changing its format. Export it as standard PCM or float WAV."
        )
    if info.frames <= 0:
        raise UnsupportedFileError("This file doesn't contain any audio.")
    return SourceFormat(
        path=str(path),
        samplerate=int(info.samplerate),
        channels=int(info.channels),
        frames=int(info.frames),
        subtype=info.subtype,
        container=info.format,
    )


def load_audio(path: str | Path) -> tuple[np.ndarray, SourceFormat]:
    """Read a WAV file as float64 (frames, channels), keeping its native scale."""
    fmt = read_source_format(path)
    data, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return data, fmt


# ---------------------------------------------------------------------------
# Loudness
# ---------------------------------------------------------------------------

_CHANNEL_WEIGHTS = (1.0, 1.0, 1.0, 1.41, 1.41)   # BS.1770 channel weighting (L, R, C, Ls, Rs)


def integrated_loudness(audio: np.ndarray, samplerate: int) -> float:
    """Integrated gated loudness in LUFS (ITU-R BS.1770-4 / EBU R128).

    Returns -inf for silence and nan when the audio is shorter than one
    400 ms measurement block.
    """
    if audio.shape[0] <= math.ceil(LOUDNESS_BLOCK_S * samplerate):
        return float("nan")
    meter = pyln.Meter(samplerate)
    with np.errstate(divide="ignore", invalid="ignore"):
        if audio.shape[1] <= 5:
            value = float(meter.integrated_loudness(audio))
        else:
            # pyloudnorm supports up to 5 channels; power-sum per-channel readings beyond that.
            powers = [10 ** (float(meter.integrated_loudness(audio[:, c])) / 10) for c in range(audio.shape[1])]
            total = sum(p for p in powers if math.isfinite(p))
            value = 10 * math.log10(total) if total > 0 else float("-inf")
    return value if math.isfinite(value) else float("-inf")


def momentary_loudness(audio: np.ndarray, samplerate: int) -> np.ndarray:
    """Momentary loudness (400 ms window) every MOMENTARY_HOP_S seconds, in LUFS.

    Element k is the loudness of the window ending at (k + 1) * MOMENTARY_HOP_S.
    Used by the live loudness meter during playback.
    """
    frames = audio.shape[0]
    meter = pyln.Meter(samplerate)
    power = np.zeros(frames)
    for c in range(audio.shape[1]):
        weighted = audio[:, c]
        for stage in meter._filters.values():   # K-weighting (shelf + high-pass)
            weighted = stage.apply_filter(weighted)
        weight = _CHANNEL_WEIGHTS[c] if c < len(_CHANNEL_WEIGHTS) else 1.0
        power += weight * np.square(weighted)
    csum = np.concatenate(([0.0], np.cumsum(power)))
    window = max(1, int(round(MOMENTARY_WINDOW_S * samplerate)))
    hop = max(1, int(round(MOMENTARY_HOP_S * samplerate)))
    ends = np.arange(hop, frames + 1, hop)
    if ends.size == 0:
        return np.full(1, FLOOR_DB, dtype=np.float32)
    starts = np.maximum(0, ends - window)
    mean = (csum[ends] - csum[starts]) / window
    with np.errstate(divide="ignore"):
        lufs = -0.691 + 10.0 * np.log10(np.maximum(mean, 1e-20))
    return np.maximum(lufs, FLOOR_DB).astype(np.float32)


# ---------------------------------------------------------------------------
# Peaks
# ---------------------------------------------------------------------------

def _max_abs_columns(block: np.ndarray, width: int) -> np.ndarray:
    """Max of |x| over each run of `width` consecutive values of a C-contiguous block.

    Written as element-wise maxima over strided views: numpy reductions along a
    very short axis are several times slower.
    """
    flat = block.reshape(-1)
    out = np.abs(flat[0::width])
    for k in range(1, width):
        np.maximum(out, np.abs(flat[k::width]), out=out)
    return out


def _oversampled_chunks(audio: np.ndarray) -> Iterator[tuple[int, int, np.ndarray]]:
    """Yield (start, stop, upsampled) for each chunk, with context trimmed away.

    `upsampled` has TRUE_PEAK_OVERSAMPLING rows per frame; the rows for frame k
    cover the time between k and k + 1.
    """
    frames = audio.shape[0]
    factor = TRUE_PEAK_OVERSAMPLING
    for start in range(0, frames, _CHUNK_FRAMES):
        stop = min(frames, start + _CHUNK_FRAMES)
        lo = max(0, start - _CHUNK_CONTEXT)
        hi = min(frames, stop + _CHUNK_CONTEXT)
        upsampled = resample_poly(audio[lo:hi], factor, 1, axis=0)
        yield start, stop, np.ascontiguousarray(upsampled[(start - lo) * factor:(stop - lo) * factor])


def true_peak_envelope(audio: np.ndarray) -> np.ndarray:
    """Per-frame linear true peak across all channels (4x oversampled)."""
    envelope = np.empty(audio.shape[0])
    width = TRUE_PEAK_OVERSAMPLING * audio.shape[1]
    for start, stop, upsampled in _oversampled_chunks(audio):
        peaks = _max_abs_columns(upsampled, width)
        np.maximum(peaks, _max_abs_columns(np.ascontiguousarray(audio[start:stop]), audio.shape[1]), out=peaks)
        envelope[start:stop] = peaks
    return envelope


def true_peak(audio: np.ndarray) -> float:
    """Linear true peak (4x oversampled) of the whole buffer."""
    peak = sample_peak(audio)
    for _, _, upsampled in _oversampled_chunks(audio):
        peak = max(peak, float(np.max(np.abs(upsampled))))
    return peak


def sample_peak(audio: np.ndarray) -> float:
    return float(np.max(np.abs(audio))) if audio.size else 0.0


# ---------------------------------------------------------------------------
# Clipping
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ClippingReport:
    events: int = 0             # separate places where the waveform is flattened
    clipped_samples: int = 0
    over_full_scale: int = 0    # float files only: samples beyond digital full scale

    @property
    def clipped(self) -> bool:
        return self.events > 0


def _runs(mask: np.ndarray) -> np.ndarray:
    """Lengths of consecutive True runs in a boolean array."""
    edges = np.diff(np.concatenate(([0], mask.view(np.int8), [0])))
    return np.flatnonzero(edges == -1) - np.flatnonzero(edges == 1)


def detect_clipping(audio: np.ndarray, fmt: SourceFormat) -> ClippingReport:
    """Find flattened peaks (clipping) that already exist in the source file.

    Integer files: runs of CLIP_MIN_RUN or more samples at digital full scale.
    Float files: runs of identical samples at or above full scale (flat tops),
    plus a separate count of samples above full scale, which float files can
    hold without damage.
    """
    events = 0
    clipped = 0
    over = 0
    if fmt.is_float:
        for c in range(audio.shape[1]):
            channel = audio[:, c]
            magnitude = np.abs(channel)
            over += int(np.count_nonzero(magnitude > 1.0))
            flat = (np.diff(channel) == 0.0) & (magnitude[1:] >= 0.999)
            if flat.any():
                lengths = _runs(flat) + 1      # n equal neighbours -> n + 1 samples
                long_runs = lengths >= CLIP_MIN_RUN
                events += int(np.count_nonzero(long_runs))
                clipped += int(lengths[long_runs].sum())
    else:
        full_scale = float(2 ** (fmt.bits - 1))
        threshold = (full_scale - 1.5) / full_scale     # within one step of full scale
        for c in range(audio.shape[1]):
            mask = np.abs(audio[:, c]) >= threshold
            if mask.any():
                lengths = _runs(mask)
                long_runs = lengths >= CLIP_MIN_RUN
                events += int(np.count_nonzero(long_runs))
                clipped += int(lengths[long_runs].sum())
    return ClippingReport(events=events, clipped_samples=clipped, over_full_scale=over)


# ---------------------------------------------------------------------------
# Display data
# ---------------------------------------------------------------------------

@dataclass
class WaveformOverview:
    duration_s: float
    times: np.ndarray     # bin centres in seconds
    low: np.ndarray       # per-bin minimum across channels
    high: np.ndarray      # per-bin maximum across channels
    rms: np.ndarray       # per-bin RMS across channels


def waveform_overview(audio: np.ndarray, samplerate: int, max_bins: int = 20000) -> WaveformOverview:
    frames, channels = audio.shape
    per_bin = max(1, math.ceil(frames / max_bins))
    bins = math.ceil(frames / per_bin)
    low = np.empty(bins, np.float32)
    high = np.empty(bins, np.float32)
    rms = np.empty(bins, np.float32)
    block = 4096
    for b0 in range(0, bins, block):
        b1 = min(bins, b0 + block)
        f0, f1 = b0 * per_bin, min(frames, b1 * per_bin)
        segment = audio[f0:f1]
        whole = (f1 - f0) // per_bin
        if whole:
            body = segment[: whole * per_bin].reshape(whole, per_bin, channels)
            low[b0:b0 + whole] = body.min(axis=(1, 2))
            high[b0:b0 + whole] = body.max(axis=(1, 2))
            rms[b0:b0 + whole] = np.sqrt(np.mean(np.square(body), axis=(1, 2)))
        if b0 + whole < b1:
            rest = segment[whole * per_bin:]
            low[b0 + whole] = rest.min()
            high[b0 + whole] = rest.max()
            rms[b0 + whole] = math.sqrt(float(np.mean(np.square(rest))))
    times = (np.arange(bins) * per_bin + per_bin / 2.0) / samplerate
    return WaveformOverview(frames / samplerate, times, low, high, rms)


@dataclass
class SpectrumProfile:
    freqs: np.ndarray      # log-spaced display frequencies (Hz)
    level_db: np.ndarray   # long-term level per 1/6 octave (dB, full-scale sine = 0 dB)
    band_db: np.ndarray    # mean level in each TONE_BANDS band (dB)


def spectrum_grid(samplerate: int) -> np.ndarray:
    return np.geomspace(20.0, min(20000.0, samplerate * 0.49), SPECTRUM_POINTS)


def band_power_to_grid(power: np.ndarray, bin_freqs: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Sum linear power into fractional-octave bands centred on `grid`."""
    half = 2.0 ** (SPECTRUM_SMOOTHING_OCT / 2.0)
    csum = np.concatenate(([0.0], np.cumsum(power)))
    i0 = np.searchsorted(bin_freqs, grid / half)
    i1 = np.maximum(np.searchsorted(bin_freqs, grid * half), i0 + 1)
    i1 = np.minimum(i1, power.size)
    i0 = np.minimum(i0, i1 - 1)
    return csum[i1] - csum[i0]


def spectrum_profile(audio: np.ndarray, samplerate: int) -> SpectrumProfile:
    """Long-term average spectrum of the mono downmix (Hann-windowed, 50 % overlap)."""
    mono = audio.mean(axis=1) if audio.shape[1] > 1 else audio[:, 0]
    seg_len = 1 << int(round(math.log2(samplerate / 5.4)))     # about 5 Hz resolution
    if mono.size < seg_len:
        mono = np.concatenate((mono, np.zeros(seg_len - mono.size)))
    hop = seg_len // 2
    starts = np.arange(1 + (mono.size - seg_len) // hop) * hop
    if starts.size > _MAX_SPECTRUM_SEGMENTS:
        starts = starts[np.linspace(0, starts.size - 1, _MAX_SPECTRUM_SEGMENTS).astype(np.int64)]
    window = np.hanning(seg_len)
    accumulated = np.zeros(seg_len // 2 + 1)
    for i in range(0, starts.size, 32):
        batch = np.stack([mono[s:s + seg_len] for s in starts[i:i + 32]]) * window
        accumulated += np.sum(np.square(np.abs(np.fft.rfft(batch, axis=1))), axis=0)
    power = accumulated / starts.size * (4.0 / window.sum() ** 2)
    bin_freqs = np.fft.rfftfreq(seg_len, 1.0 / samplerate)

    grid = spectrum_grid(samplerate)
    with np.errstate(divide="ignore"):
        level_db = np.maximum(10.0 * np.log10(band_power_to_grid(power, bin_freqs, grid) + 1e-20), FLOOR_DB)
        band_db = []
        for lo, hi in TONE_BANDS:
            selected = power[(bin_freqs >= lo) & (bin_freqs < min(hi, samplerate / 2))]
            band_db.append(10.0 * math.log10(float(selected.mean()) + 1e-20) if selected.size else FLOOR_DB)
    return SpectrumProfile(grid, level_db.astype(np.float32), np.maximum(np.array(band_db), FLOOR_DB))


# ---------------------------------------------------------------------------
# Complete analysis
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Measurements:
    lufs: float
    true_peak_db: float
    sample_peak_db: float
    duration_s: float


def measure(audio: np.ndarray, samplerate: int) -> Measurements:
    return Measurements(
        lufs=integrated_loudness(audio, samplerate),
        true_peak_db=to_db(true_peak(audio)),
        sample_peak_db=to_db(sample_peak(audio)),
        duration_s=audio.shape[0] / samplerate,
    )


@dataclass
class TrackAnalysis:
    fmt: SourceFormat
    measurements: Measurements
    clipping: ClippingReport
    overview: WaveformOverview
    spectrum: SpectrumProfile
    momentary: np.ndarray


def analyze_audio(audio: np.ndarray, fmt: SourceFormat, progress: ProgressFn | None = None) -> TrackAnalysis:
    def step(fraction: float, text: str) -> None:
        if progress:
            progress(fraction, text)

    sr = fmt.samplerate
    step(0.0, "Measuring loudness")
    measurements = measure(audio, sr)
    step(0.45, "Checking for clipping")
    clipping = detect_clipping(audio, fmt)
    step(0.6, "Drawing waveform")
    overview = waveform_overview(audio, sr)
    step(0.7, "Analyzing tone")
    spectrum = spectrum_profile(audio, sr)
    step(0.85, "Measuring loudness over time")
    momentary = momentary_loudness(audio, sr)
    step(1.0, "Analyzed")
    return TrackAnalysis(fmt, measurements, clipping, overview, spectrum, momentary)


def analyze_file(path: str | Path, progress: ProgressFn | None = None) -> TrackAnalysis:
    audio, fmt = load_audio(path)
    return analyze_audio(audio, fmt, progress)
