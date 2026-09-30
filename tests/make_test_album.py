"""Generate a deterministic, deliberately messy test album.

Every track uses a different bit depth / sample rate / channel layout and a
different loudness, tone, and silence layout. One is clipped, one float file
peaks above full scale, and one starts and ends without silence (a segue).

    python -m tests.make_test_album [output_folder]
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pyloudnorm as pyln
import soundfile as sf
from scipy.signal import lfilter

from audio.processing import quantize


@dataclass(frozen=True)
class Spec:
    name: str
    subtype: str
    samplerate: int
    channels: int
    seconds: float
    lufs: float
    brightness: float
    lead_s: float = 0.0
    tail_s: float = 0.0
    bpm: float = 110.0
    clip_drive: float = 0.0     # > 0: hard clip at full scale after this much gain (dB)
    float_peak: float = 0.0     # > 0: scale a float file so its peak is this value
    segue: bool = False         # no fade at the end


ALBUM = (
    Spec("01 Opening.wav", "PCM_16", 44100, 2, 24, -21.0, 0.6, lead_s=1.5, tail_s=4.0, bpm=92),
    Spec("02 Neon Drive.wav", "PCM_24", 48000, 2, 22, -8.5, 1.0, lead_s=0.3, tail_s=1.0, bpm=128),
    Spec("03 Low Tide.wav", "FLOAT", 96000, 2, 20, -15.0, 0.25, lead_s=0.5, tail_s=2.5, bpm=84, float_peak=1.35),
    Spec("04 Paper Planes.wav", "PCM_16", 48000, 1, 18, -12.0, 0.8, lead_s=0.2, tail_s=1.5, bpm=118, clip_drive=7.0),
    Spec("05 Afterglow.wav", "PCM_24", 88200, 2, 20, -26.0, 0.5, bpm=70, segue=True),
    Spec("06 Static Hearts.wav", "PCM_32", 44100, 2, 16, -13.0, 0.9, lead_s=3.0, tail_s=7.0, bpm=140),
)

_CHORDS = ((0, 4, 7), (-3, 0, 4), (-7, -3, 0), (-5, -1, 2))   # I - vi - IV - V (semitones)


def _note(semitones: float, root: float = 220.0) -> float:
    return root * 2.0 ** (semitones / 12.0)


def _highpass(x: np.ndarray, sr: int, hz: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * hz / sr)
    return lfilter([(1 + a) / 2, -(1 + a) / 2], [1, -a], x)


def _lowpass(x: np.ndarray, sr: int, hz: float) -> np.ndarray:
    a = np.exp(-2 * np.pi * hz / sr)
    return lfilter([1 - a], [1, -a], x)


def synth(spec: Spec, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    sr = spec.samplerate
    n = int(spec.seconds * sr)
    t = np.arange(n) / sr
    beat = 60.0 / spec.bpm
    bar = 4 * beat

    tb = t % beat
    kick = np.sin(2 * np.pi * 48 * tb * (1 + 1.5 * np.exp(-tb * 25))) * np.exp(-tb * 8)
    ts = (t - beat) % (2 * beat)
    snare = _highpass(rng.standard_normal(n), sr, 900) * np.exp(-ts * 16) * 0.35
    th = t % (beat / 2)
    hats = _highpass(rng.standard_normal(n), sr, 7000) * np.exp(-th * 45) * 0.25 * spec.brightness

    chord_index = (t // bar).astype(int) % len(_CHORDS)
    pad = np.zeros(n)
    bass = np.zeros(n)
    for i, chord in enumerate(_CHORDS):
        mask = chord_index == i
        for semi in chord:
            f = _note(semi)
            for h in range(1, 5):
                pad[mask] += np.sin(2 * np.pi * f * h * t[mask]) * (0.12 / h ** (1.6 - spec.brightness))
        bass[mask] = np.sin(2 * np.pi * _note(chord[0], 55.0) * t[mask]) * 0.45
    pad *= 0.6 + 0.4 * np.sin(2 * np.pi * t / bar) ** 2
    pad = _lowpass(pad, sr, 1500 + 9000 * spec.brightness)

    melody_f = _note(12 + 5 * np.sin(2 * np.pi * t / (2 * bar)).round())
    melody = np.sin(2 * np.pi * np.cumsum(melody_f * (1 + 0.003 * np.sin(2 * np.pi * 5 * t))) / sr) * 0.18

    left = kick * 0.9 + snare + hats * 1.2 + pad * 1.1 + bass + melody * 0.6
    right = kick * 0.9 + snare * 0.9 + hats * 0.7 + pad * 0.9 + bass + melody * 1.1
    stereo = np.stack([left, right], axis=1)

    if not spec.segue:
        fade = int(1.5 * sr)
        stereo[-fade:] *= np.linspace(1.0, 0.0, fade)[:, None] ** 2
    fade_in = int(0.01 * sr)
    stereo[:fade_in] *= np.linspace(0.0, 1.0, fade_in)[:, None]

    audio = stereo if spec.channels == 2 else stereo.mean(axis=1, keepdims=True)
    meter = pyln.Meter(sr)
    if spec.lufs > -10:     # loud master: saturate so the level fits under full scale
        audio = np.tanh(audio * 2.5) / np.tanh(2.5)
    audio *= 10 ** ((spec.lufs - meter.integrated_loudness(audio)) / 20)

    if spec.clip_drive:
        audio = np.clip(audio * 10 ** (spec.clip_drive / 20), -1.0, 32767 / 32768)
    elif spec.float_peak:
        audio *= spec.float_peak / np.abs(audio).max()
    elif spec.subtype != "FLOAT":
        peak = np.abs(audio).max()
        if peak > 0.99:
            audio *= 0.99 / peak

    noise_floor = 10 ** (-84 / 20)
    lead = rng.standard_normal((int(spec.lead_s * sr), audio.shape[1])) * noise_floor
    tail = rng.standard_normal((int(spec.tail_s * sr), audio.shape[1])) * noise_floor
    return np.concatenate([lead, audio, tail])


def write_album(folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, spec in enumerate(ALBUM):
        audio = synth(spec, seed=1000 + i)
        path = folder / spec.name
        # Integer formats go through the app's own quantizer so full scale is exact.
        sf.write(str(path), quantize(audio, spec.subtype), spec.samplerate, subtype=spec.subtype)
        paths.append(path)
    return paths


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "album"
    for p in write_album(target):
        info = sf.info(str(p))
        print(f"{p.name:24} {info.subtype:7} {info.samplerate:6} Hz {info.channels} ch {info.duration:5.1f} s")
