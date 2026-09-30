"""Mastering DSP: optional tone-matching EQ, loudness gain, true-peak limiting,
silence normalisation and the final sample-format conversion.

Processing runs on float64 buffers shaped (frames, channels). Conversion to the
output sample format happens only in `quantize`, right before writing. Every
step is deterministic: the same input and settings always give the same output.
"""

from __future__ import annotations

import math
import zlib
from dataclasses import asdict, dataclass, field, fields
from typing import Any

import numpy as np
from pedalboard import HighShelfFilter, LowShelfFilter, PeakFilter, Pedalboard
from scipy.ndimage import maximum_filter1d

from .analysis import (
    FLOOR_DB,
    SUBTYPES,
    CancelFn,
    ProgressFn,
    SpectrumProfile,
    integrated_loudness,
    true_peak,
    true_peak_envelope,
)

DEFAULT_TARGET_LUFS = -14.0
DEFAULT_CEILING_DBTP = -1.0
DEFAULT_GAP_S = 2.0
DEFAULT_LEAD_IN_S = 0.1
DEFAULT_EQ_STRENGTH = 0.5
TARGET_LUFS_RANGE = (-24.0, -6.0)
CEILING_RANGE = (-3.0, -0.1)
GAP_RANGE = (0.2, 6.0)

EQ_MAX_GAIN_DB = 3.0
EQ_MIN_GAIN_DB = 0.1

LIMITER_LOOKAHEAD_MS = 5.0
LIMITER_HOLD_MS = 10.0
LIMITER_FAST_RELEASE_DB_PER_S = 60.0     # recovery after short transients
LIMITER_SLOW_WINDOW_MS = 60.0            # averaging window that detects sustained limiting
LIMITER_SLOW_RELEASE_DB_PER_S = 8.0      # recovery after sustained limiting (avoids pumping)
LIMITER_SAFETY_DB = 0.02
MAX_LOUDNESS_MAKEUP_DB = 2.0
LOUDNESS_TOLERANCE_LU = 0.1

SILENCE_THRESHOLD_DB = -60.0
MIN_SILENCE_S = 0.005

INT_BITS = {"PCM_U8": 8, "PCM_S8": 8, "PCM_16": 16, "PCM_24": 24, "PCM_32": 32}
OUTPUT_SUBTYPE_CHOICES = ("PCM_16", "PCM_24", "PCM_32", "FLOAT")
_QUANTIZE_CHUNK = 1 << 20


class Cancelled(Exception):
    """Raised when the user cancels a running batch."""


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

PRESET_FIELDS = (
    "target_lufs", "ceiling_dbtp", "eq_match", "eq_strength",
    "even_spacing", "gap_s", "output_subtype",
)


@dataclass
class MasteringSettings:
    target_lufs: float = DEFAULT_TARGET_LUFS
    ceiling_dbtp: float = DEFAULT_CEILING_DBTP
    eq_match: bool = False
    eq_reference: str | None = None      # path of the reference track in the batch
    eq_strength: float = DEFAULT_EQ_STRENGTH
    even_spacing: bool = True
    lead_in_s: float = DEFAULT_LEAD_IN_S
    gap_s: float = DEFAULT_GAP_S
    output_subtype: str | None = None    # None keeps each file's own bit depth
    output_dir: str | None = None        # None writes to a "mastered" folder next to each original
    suffix: str = "_mastered"
    album: str = ""
    album_artist: str = ""

    def copy(self) -> "MasteringSettings":
        return MasteringSettings(**asdict(self))

    def preset_dict(self) -> dict[str, Any]:
        return {name: getattr(self, name) for name in PRESET_FIELDS}

    def apply_preset(self, data: dict[str, Any]) -> None:
        """Apply preset values, ignoring unknown keys and clamping out-of-range numbers."""
        def number(key: str, lo: float, hi: float) -> None:
            if isinstance(data.get(key), (int, float)) and not isinstance(data.get(key), bool):
                setattr(self, key, float(min(hi, max(lo, data[key]))))

        number("target_lufs", *TARGET_LUFS_RANGE)
        number("ceiling_dbtp", *CEILING_RANGE)
        number("eq_strength", 0.0, 1.0)
        number("gap_s", *GAP_RANGE)
        for key in ("eq_match", "even_spacing"):
            if isinstance(data.get(key), bool):
                setattr(self, key, data[key])
        if "output_subtype" in data:
            value = data["output_subtype"]
            self.output_subtype = value if value in OUTPUT_SUBTYPE_CHOICES else None


# ---------------------------------------------------------------------------
# Gain
# ---------------------------------------------------------------------------

def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def loudness_gain_db(measured_lufs: float, target_lufs: float) -> float:
    """Static gain that brings `measured_lufs` to `target_lufs` (0 if unmeasurable)."""
    return target_lufs - measured_lufs if math.isfinite(measured_lufs) else 0.0


# ---------------------------------------------------------------------------
# True-peak limiter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LimiterStats:
    max_reduction_db: float = 0.0
    active_fraction: float = 0.0   # share of the track where the limiter works (> 0.1 dB)


def _odd(n: int) -> int:
    return max(1, n | 1)


def window_max(x: np.ndarray, size: int, forward: bool) -> np.ndarray:
    """Running max over [n, n + size - 1] (forward) or [n - size + 1, n] (backward).

    `size` must be odd. Values outside the array count as 0.
    """
    half = size // 2
    return maximum_filter1d(x, size=size, mode="constant", cval=0.0, origin=-half if forward else half)


def backward_mean(x: np.ndarray, size: int) -> np.ndarray:
    """Running mean over [n - size + 1, n]; before the start, x[0] is repeated."""
    padded = np.concatenate((np.full(size - 1, x[0]), x))
    csum = np.concatenate(([0.0], np.cumsum(padded)))
    return (csum[size:] - csum[:-size]) / size


def _release(x: np.ndarray, db_per_frame: float) -> np.ndarray:
    """Instant attack, constant-rate release, without a Python loop:
    e[n] = max over k <= n of (x[k] - (n - k) * d) = cummax(x[k] + k*d) - n*d.
    """
    ramp = np.arange(x.size) * db_per_frame
    out = np.maximum.accumulate(x + ramp)
    out -= ramp
    return out


def limiter_reduction_db(peak_envelope: np.ndarray, ceiling: float, samplerate: int) -> np.ndarray:
    """Smooth gain-reduction curve (dB, >= 0) that keeps `peak_envelope` under `ceiling`.

    1. Required reduction per frame from the oversampled peak envelope.
    2. Dual release: a fast release after short transients, and a slow one
       driven by the reduction averaged over LIMITER_SLOW_WINDOW_MS, so dense
       passages don't pump. The larger of the two wins.
    3. Look-ahead: a forward max followed by a backward mean of the same length
       ramps the reduction in before each peak. Every value averaged at a frame
       is >= the reduction that frame needs, so the ceiling is never exceeded.
    """
    with np.errstate(divide="ignore"):
        needed = np.maximum(20.0 * np.log10(np.maximum(peak_envelope, 1e-12) / ceiling), 0.0)
    if not needed.any():
        return np.zeros_like(needed)
    held = window_max(needed, _odd(int(samplerate * LIMITER_HOLD_MS / 1000)), forward=False)
    released = _release(held, LIMITER_FAST_RELEASE_DB_PER_S / samplerate)
    del held
    sustained = backward_mean(needed, max(1, int(samplerate * LIMITER_SLOW_WINDOW_MS / 1000)))
    del needed
    np.maximum(released, _release(sustained, LIMITER_SLOW_RELEASE_DB_PER_S / samplerate), out=released)
    del sustained
    lookahead = _odd(int(samplerate * LIMITER_LOOKAHEAD_MS / 1000))
    ahead = window_max(released, lookahead, forward=True)
    del released
    return np.maximum(backward_mean(ahead, lookahead), 0.0)


def true_peak_limit(audio: np.ndarray, samplerate: int, ceiling_dbtp: float,
                    envelope: np.ndarray | None = None) -> tuple[np.ndarray, LimiterStats]:
    """Look-ahead true-peak limiter with a guaranteed ceiling.

    The reduction curve is computed from the 4x oversampled peak envelope
    (pass `envelope` to reuse one already computed for this exact audio). The
    result is re-measured; in the rare case interpolation between samples still
    overshoots, the pass is repeated with a slightly lower internal ceiling.
    """
    ceiling = db_to_linear(ceiling_dbtp)
    if envelope is None:
        envelope = true_peak_envelope(audio)
    internal_db = ceiling_dbtp - LIMITER_SAFETY_DB
    if envelope.max() <= db_to_linear(internal_db):
        return audio, LimiterStats()

    reduction = np.zeros(1)
    out = audio
    for _ in range(4):
        reduction = limiter_reduction_db(envelope, db_to_linear(internal_db), samplerate)
        out = audio * np.power(10.0, -reduction / 20.0)[:, None]
        measured = true_peak(out)
        if measured <= ceiling:
            break
        internal_db -= 20.0 * math.log10(measured / ceiling) + 0.01
    else:
        out *= ceiling / true_peak(out) * 0.999      # last-resort static trim, never needed in practice
    stats = LimiterStats(
        max_reduction_db=float(reduction.max()),
        active_fraction=float(np.count_nonzero(reduction > 0.1) / reduction.size),
    )
    return out, stats


# ---------------------------------------------------------------------------
# Tone matching EQ
# ---------------------------------------------------------------------------

# One filter per analysis band in analysis.TONE_BANDS: (kind, frequency Hz, Q, label)
EQ_FILTERS: tuple[tuple[str, float, float, str], ...] = (
    ("low_shelf", 120.0, 0.707, "Bass"),
    ("peak", 300.0, 0.9, "Low mids"),
    ("peak", 1000.0, 0.9, "Mids"),
    ("peak", 3500.0, 0.9, "Presence"),
    ("high_shelf", 8000.0, 0.707, "Air"),
)


@dataclass(frozen=True)
class EqBand:
    kind: str
    frequency_hz: float
    q: float
    gain_db: float
    label: str

    @property
    def description(self) -> str:
        return f"{self.label} {self.gain_db:+.1f} dB"


def eq_match_bands(track: SpectrumProfile, reference: SpectrumProfile, strength: float) -> tuple[EqBand, ...]:
    """Gentle EQ moves (max ±3 dB) that bring the track's tonal balance toward the reference.

    Only the shape of the spectrum is compared, never its level, so a quiet
    track with the same tone as the reference gets no EQ.
    """
    valid = (track.band_db > FLOOR_DB + 20) & (reference.band_db > FLOOR_DB + 20)
    if np.count_nonzero(valid) < 2:
        return ()
    t = track.band_db - track.band_db[valid].mean()
    r = reference.band_db - reference.band_db[valid].mean()
    gains = np.clip((r - t) * min(1.0, max(0.0, strength)), -EQ_MAX_GAIN_DB, EQ_MAX_GAIN_DB)
    bands = []
    for (kind, freq, q, label), gain, ok in zip(EQ_FILTERS, gains, valid):
        if ok and abs(gain) >= EQ_MIN_GAIN_DB:
            bands.append(EqBand(kind, freq, q, round(float(gain), 2), label))
    return tuple(bands)


def apply_eq(audio: np.ndarray, samplerate: int, bands: tuple[EqBand, ...]) -> np.ndarray:
    """Apply EQ bands with pedalboard's biquad filters."""
    makers = {"low_shelf": LowShelfFilter, "peak": PeakFilter, "high_shelf": HighShelfFilter}
    filters = [
        makers[b.kind](cutoff_frequency_hz=b.frequency_hz, gain_db=b.gain_db, q=b.q)
        for b in bands if b.frequency_hz < samplerate * 0.45
    ]
    if not filters:
        return audio
    processed = Pedalboard(filters)(np.ascontiguousarray(audio.T), samplerate)
    return np.ascontiguousarray(processed.T, dtype=np.float64)


# ---------------------------------------------------------------------------
# Silence and spacing
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SilenceReport:
    lead_before_s: float
    lead_after_s: float
    tail_before_s: float
    tail_after_s: float
    lead_untouched: bool   # the track starts straight away, so its start was kept as is
    tail_untouched: bool   # the track ends without silence (e.g. flows into the next one)


def _fade(length: int, rising: bool) -> np.ndarray:
    ramp = np.sin(np.linspace(0.0, math.pi / 2.0, length)) ** 2
    return ramp if rising else ramp[::-1]


def normalize_silence(audio: np.ndarray, samplerate: int, lead_in_s: float, gap_s: float) -> tuple[np.ndarray, SilenceReport]:
    """Give every track the same short lead-in and the same trailing silence.

    Played back to back, tracks are then separated by `gap_s` seconds. Starts
    or ends with no silence at all are left untouched so segues keep flowing.
    Leftover silence is shortened with a smooth fade; missing silence is added
    as digital silence.
    """
    frames = audio.shape[0]
    threshold = db_to_linear(SILENCE_THRESHOLD_DB)
    loud = np.flatnonzero(np.abs(audio).max(axis=1) > threshold)
    if loud.size == 0:
        seconds = frames / samplerate
        return audio, SilenceReport(seconds, seconds, 0.0, 0.0, True, True)

    first, last = int(loud[0]), int(loud[-1])
    lead, tail = first, frames - 1 - last
    min_frames = int(round(MIN_SILENCE_S * samplerate))
    lead_target = int(round(lead_in_s * samplerate))
    tail_target = int(round(max(0.0, gap_s - lead_in_s) * samplerate))

    start, stop, head_pad, tail_pad = 0, frames, 0, 0
    lead_untouched = lead < min_frames
    tail_untouched = tail < min_frames
    if not lead_untouched:
        if lead >= lead_target:
            start = lead - lead_target
        else:
            head_pad = lead_target - lead
    if not tail_untouched:
        if tail >= tail_target:
            stop = last + 1 + tail_target
        else:
            tail_pad = tail_target - tail

    body = audio[start:stop].copy()
    fade_in = first - start if start > 0 else 0
    if fade_in > 1:
        body[:fade_in] *= _fade(fade_in, rising=True)[:, None]
    fade_out = stop - (last + 1) if stop < frames else 0
    if fade_out > 1:
        body[-fade_out:] *= _fade(fade_out, rising=False)[:, None]

    channels = audio.shape[1]
    out = np.concatenate((np.zeros((head_pad, channels)), body, np.zeros((tail_pad, channels))))
    report = SilenceReport(
        lead_before_s=lead / samplerate,
        lead_after_s=(lead - start + head_pad) / samplerate,
        tail_before_s=tail / samplerate,
        tail_after_s=(stop - 1 - last + tail_pad) / samplerate,
        lead_untouched=lead_untouched,
        tail_untouched=tail_untouched,
    )
    return out, report


# ---------------------------------------------------------------------------
# Complete mastering chain
# ---------------------------------------------------------------------------

@dataclass
class MasteringOutcome:
    audio: np.ndarray
    input_lufs: float            # loudness right before the gain stage (after EQ)
    gain_db: float               # total static gain, including loudness make-up
    makeup_db: float             # extra gain added to make up for limiting
    limiter: LimiterStats
    eq_bands: tuple[EqBand, ...] = ()
    silence: SilenceReport | None = None
    notes: list[str] = field(default_factory=list)


def master_audio(
    audio: np.ndarray,
    samplerate: int,
    settings: MasteringSettings,
    eq_bands: tuple[EqBand, ...] = (),
    progress: ProgressFn | None = None,
    cancel: CancelFn | None = None,
) -> MasteringOutcome:
    """EQ (optional) -> loudness gain -> true-peak limiter -> silence normalisation.

    The limiter always comes after the gain. When limiting pulls the loudness
    below the target, a little make-up gain (at most MAX_LOUDNESS_MAKEUP_DB)
    is added and the limiter runs again, so tracks land closer to the target.
    """
    def step(fraction: float, text: str) -> None:
        if cancel and cancel():
            raise Cancelled()
        if progress:
            progress(fraction, text)

    x = audio
    if eq_bands:
        step(0.0, "Matching tone")
        x = apply_eq(x, samplerate, eq_bands)

    step(0.15, "Matching volume")
    input_lufs = integrated_loudness(x, samplerate)
    gain = loudness_gain_db(input_lufs, settings.target_lufs)
    envelope = true_peak_envelope(x)        # scales linearly with gain, so it is computed once
    makeup = 0.0
    for attempt in range(3):
        step(0.3 + attempt * 0.2, "Keeping peaks safe")
        linear = db_to_linear(gain + makeup)
        y, limiter = true_peak_limit(x * linear, samplerate, settings.ceiling_dbtp, envelope * linear)
        if not math.isfinite(input_lufs) or limiter.max_reduction_db < 0.05:
            break
        shortfall = settings.target_lufs - integrated_loudness(y, samplerate)
        if shortfall <= LOUDNESS_TOLERANCE_LU or makeup >= MAX_LOUDNESS_MAKEUP_DB:
            break
        makeup = min(MAX_LOUDNESS_MAKEUP_DB, makeup + shortfall)

    silence = None
    if settings.even_spacing:
        step(0.9, "Evening out silence")
        y, silence = normalize_silence(y, samplerate, settings.lead_in_s, settings.gap_s)
    step(1.0, "Mastered")
    return MasteringOutcome(y, input_lufs, gain + makeup, makeup, limiter, eq_bands, silence)


# ---------------------------------------------------------------------------
# Output sample format
# ---------------------------------------------------------------------------

def resolve_output_subtype(source_subtype: str, override: str | None) -> str:
    return override if override in OUTPUT_SUBTYPE_CHOICES else source_subtype


def needs_dither(source_subtype: str, output_subtype: str) -> bool:
    """True when writing to an integer format with fewer bits than the source.

    Without an override the output subtype equals the source, so this is False.
    """
    if output_subtype not in INT_BITS:
        return False
    _, source_bits, source_is_float = SUBTYPES[source_subtype]
    return source_is_float or source_bits > INT_BITS[output_subtype]


def dither_seed(audio: np.ndarray) -> int:
    """Seed derived from the audio itself, so dithered exports are reproducible."""
    head = np.ascontiguousarray(audio[: min(audio.shape[0], 1 << 16)])
    return (zlib.crc32(head.tobytes()) ^ audio.shape[0]) & 0xFFFFFFFF


def quantize(audio: np.ndarray, subtype: str, dither: bool = False, seed: int = 0) -> np.ndarray:
    """Convert float64 audio into the array soundfile writes for `subtype`.

    Integer formats are rounded (with optional TPDF dither of +/-1 LSB) and
    clipped to the legal range here, instead of relying on libsndfile's own
    float-to-int scaling, so that unprocessed audio round-trips bit-exactly.
    24-bit and 8-bit samples are left-aligned in int32/int16 containers, which
    is how libsndfile expects them.
    """
    if subtype == "FLOAT":
        return audio.astype(np.float32)
    if subtype == "DOUBLE":
        return np.ascontiguousarray(audio, dtype=np.float64)
    bits = INT_BITS[subtype]
    scale = float(2 ** (bits - 1))
    dtype, shift = {8: (np.int16, 8), 16: (np.int16, 0), 24: (np.int32, 8), 32: (np.int32, 0)}[bits]
    out = np.empty(audio.shape, dtype=dtype)
    rng = np.random.default_rng(seed) if dither else None
    for start in range(0, audio.shape[0], _QUANTIZE_CHUNK):
        stop = min(audio.shape[0], start + _QUANTIZE_CHUNK)
        x = audio[start:stop] * scale
        if rng is not None:
            x += rng.random(x.shape) - rng.random(x.shape)
        np.rint(x, out=x)
        np.clip(x, -scale, scale - 1.0, out=x)
        q = x.astype(dtype)
        if shift:
            q <<= shift
        out[start:stop] = q
    return out
