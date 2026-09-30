"""End-to-end checks of the explicit constraints in the specification.

    python -m pytest tests -q
"""

from __future__ import annotations

import hashlib
import math
import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from mutagen.wave import WAVE

from audio.analysis import (
    analyze_file,
    detect_clipping,
    integrated_loudness,
    load_audio,
    read_source_format,
    to_db,
    true_peak,
)
from audio.batch import TrackJob, run_batch
from audio.processing import (
    MasteringSettings,
    backward_mean,
    needs_dither,
    normalize_silence,
    quantize,
    window_max,
)
from tests.make_test_album import ALBUM, write_album


@pytest.fixture(scope="module")
def album(tmp_path_factory) -> list[Path]:
    return write_album(tmp_path_factory.mktemp("album"))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jobs(paths: list[Path]) -> list[TrackJob]:
    return [TrackJob(path=str(p), title=p.stem[3:], artist="Test Artist", track_number=i + 1,
                     track_total=len(paths)) for i, p in enumerate(paths)]


@pytest.fixture(scope="module")
def mastered(album, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    hashes = {p: _sha(p) for p in album}
    settings = MasteringSettings(output_dir=str(out), album="Test Album")
    results = run_batch(_jobs(album), settings)
    return results, hashes, out


# --- limiter building blocks -------------------------------------------------

def test_window_helpers_match_brute_force():
    rng = np.random.default_rng(1)
    x = rng.random(200)
    size = 7
    fwd = window_max(x, size, forward=True)
    bwd = window_max(x, size, forward=False)
    mean = backward_mean(x, size)
    for n in range(x.size):
        assert fwd[n] == pytest.approx(x[n:n + size].max())
        assert bwd[n] == pytest.approx(x[max(0, n - size + 1):n + 1].max())
        window = np.concatenate((np.full(max(0, size - 1 - n), x[0]), x[max(0, n - size + 1):n + 1]))
        assert mean[n] == pytest.approx(window.mean())


# --- whole batch ---------------------------------------------------------------

def test_originals_never_modified(mastered):
    _, hashes, _ = mastered
    for path, digest in hashes.items():
        assert _sha(path) == digest


def test_output_keeps_source_format(mastered):
    results, _, _ = mastered
    for r in results:
        assert r.ok, r.error
        src = sf.info(r.path)
        dst = sf.info(r.output_path)
        assert (dst.subtype, dst.samplerate, dst.channels) == (src.subtype, src.samplerate, src.channels)
        assert Path(r.output_path).name.endswith("_mastered.wav")


def test_loudness_and_true_peak_of_written_files(mastered):
    results, _, _ = mastered
    for r in results:
        audio, fmt = load_audio(r.output_path)
        assert to_db(true_peak(audio)) <= -1.0 + 1e-3, r.path
        lufs = integrated_loudness(audio, fmt.samplerate)
        assert abs(lufs - (-14.0)) <= 0.5, (r.path, lufs)


def test_clipping_detected_only_where_expected(mastered):
    results, _, _ = mastered
    flagged = {Path(r.path).name for r in results if r.clipping.clipped}
    assert flagged == {"04 Paper Planes.wav"}
    low_tide = next(r for r in results if r.path.endswith("03 Low Tide.wav"))
    assert low_tide.clipping.over_full_scale > 0


def test_metadata_written(mastered):
    results, _, _ = mastered
    r = results[1]
    tags = WAVE(r.output_path).tags
    assert str(tags["TIT2"].text[0]) == "Neon Drive"
    assert str(tags["TPE1"].text[0]) == "Test Artist"
    assert str(tags["TALB"].text[0]) == "Test Album"
    assert str(tags["TRCK"].text[0]) == "2/6"
    with sf.SoundFile(r.output_path) as f:
        assert f.title == "Neon Drive"


def test_silence_evened_out(mastered):
    results, _, _ = mastered
    for r in results:
        s = r.silence
        name = Path(r.path).name
        if name == "05 Afterglow.wav":          # segue: starts and ends without silence
            assert s.lead_untouched and s.tail_untouched
            continue
        assert s.lead_after_s == pytest.approx(0.1, abs=0.002), name
        assert s.tail_after_s == pytest.approx(1.9, abs=0.002), name


def test_reproducible_and_thread_independent(album, mastered, tmp_path):
    results, _, _ = mastered
    settings = MasteringSettings(output_dir=str(tmp_path), album="Test Album")
    again = run_batch(_jobs(album), settings, workers=1)
    for a, b in zip(results, again):
        assert _sha(Path(a.output_path)) == _sha(Path(b.output_path))


def test_dry_run_writes_nothing(album, tmp_path):
    before = sorted(p.name for p in album[0].parent.iterdir())
    settings = MasteringSettings(output_dir=str(tmp_path / "never"))
    results = run_batch(_jobs(album), settings, dry_run=True)
    assert all(r.ok and r.output_path is None for r in results)
    assert not (tmp_path / "never").exists()
    assert sorted(p.name for p in album[0].parent.iterdir()) == before
    assert not (album[0].parent / "mastered").exists()


def test_forced_bit_depth_dithers_only_reductions(album, tmp_path):
    settings = MasteringSettings(output_dir=str(tmp_path), output_subtype="PCM_16")
    results = run_batch(_jobs(album), settings)
    for r in results:
        assert sf.info(r.output_path).subtype == "PCM_16"
        assert sf.info(r.output_path).samplerate == sf.info(r.path).samplerate
        expected = r.fmt.subtype != "PCM_16"
        assert r.dithered == expected, r.path
    assert not needs_dither("PCM_16", "PCM_24")
    assert needs_dither("FLOAT", "PCM_24")
    assert not needs_dither("PCM_24", "PCM_24")


def test_eq_matching_moves_tone_toward_reference(album, tmp_path):
    reference = next(p for p in album if p.name == "02 Neon Drive.wav")       # bright
    dark = next(p for p in album if p.name == "03 Low Tide.wav")               # dark
    # A low target keeps the limiter out of the way, so only the EQ shapes the tone.
    settings = MasteringSettings(output_dir=str(tmp_path), eq_match=True, target_lufs=-24.0,
                                 eq_reference=str(reference), eq_strength=1.0)
    results = run_batch(_jobs([reference, dark]), settings)
    ref_result, dark_result = results
    assert ref_result.is_eq_reference and not ref_result.eq_bands
    air = {b.label: b.gain_db for b in dark_result.eq_bands}
    assert air.get("Air", 0) > 0.5, dark_result.eq_bands

    ref_bands = analyze_file(reference).spectrum.band_db
    before = analyze_file(dark).spectrum.band_db
    after = analyze_file(dark_result.output_path).spectrum.band_db
    shape = lambda b: b - b.mean()
    assert np.abs(shape(after) - shape(ref_bands)).sum() < np.abs(shape(before) - shape(ref_bands)).sum()


# --- units -------------------------------------------------------------------

@pytest.mark.parametrize("subtype", ["PCM_16", "PCM_24", "PCM_32", "PCM_U8"])
def test_unprocessed_audio_round_trips_bit_exact(tmp_path, subtype):
    rng = np.random.default_rng(3)
    bits = {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32, "PCM_U8": 8}[subtype]
    scale = 2 ** (bits - 1)
    ints = rng.integers(-scale, scale, size=(4000, 2))
    source = tmp_path / "a.wav"
    sf.write(str(source), quantize(ints / scale, subtype), 48000, subtype=subtype)
    audio, _ = load_audio(source)
    np.testing.assert_array_equal(np.rint(audio * scale).astype(np.int64), ints)
    copy = tmp_path / "b.wav"
    sf.write(str(copy), quantize(audio, subtype), 48000, subtype=subtype)
    assert copy.read_bytes() == source.read_bytes()


def test_true_peak_catches_intersample_overs():
    sr = 48000
    n = np.arange(sr)
    # A sine at fs/4 with a 45 degree phase offset: samples peak at 0.707, true peak is 1.0.
    # Faded in and out so the abrupt edges don't ring.
    x = np.sin(2 * np.pi * (sr / 4) * n / sr + np.pi / 4)
    fade = np.hanning(4000)
    x[:2000] *= fade[:2000]
    x[-2000:] *= fade[2000:]
    x = x[:, None]
    assert np.abs(x).max() == pytest.approx(math.sqrt(0.5), abs=1e-6)
    assert true_peak(x) == pytest.approx(1.0, abs=0.01)


def test_silence_normalisation_pads_short_silence():
    sr = 10000
    tone = np.sin(np.arange(sr) / 5.0)[:, None] * 0.5
    audio = np.concatenate((np.zeros((200, 1)), tone, np.zeros((300, 1))))   # 0.02 s lead, 0.03 s tail
    out, report = normalize_silence(audio, sr, lead_in_s=0.1, gap_s=2.0)
    assert report.lead_after_s == pytest.approx(0.1, abs=1e-3)
    assert report.tail_after_s == pytest.approx(1.9, abs=1e-3)
    assert abs(out.shape[0] - (sr + 1000 + 19000)) <= 2    # the tone's own zero crossings at the edges


def test_unsupported_files_are_explained(tmp_path):
    bogus = tmp_path / "not audio.wav"
    bogus.write_bytes(b"hello world" * 100)
    with pytest.raises(Exception) as info:
        read_source_format(bogus)
    assert "WAV" in str(info.value)
