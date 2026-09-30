"""Seamless A/B playback of the original and mastered versions of a track.

Both versions are decoded into memory and played through one audio stream
(QAudioSink in pull mode). Every block is read from the same position on a
shared timeline, so switching between Original and Mastered never stops the
music or jumps: it only moves an equal-power crossfade (FADE_S long) from one
version to the other. Level matching is applied inside the same mix.

Timeline: positions are seconds on the original's timeline. The mastered file
may start earlier or later (silence at the start was trimmed or extended), so
mastered frame = original frame - offset, with offset = lead_before - lead_after.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QElapsedTimer, QIODevice, QObject, QThreadPool, QTimer, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices, QtAudio

from audio.analysis import MOMENTARY_HOP_S

from .session import Track

FADE_S = 0.02            # A/B crossfade length
BUFFER_S = 0.05          # audio buffered ahead of the speakers (sets the A/B switch latency)
FRAME_BYTES = 8          # stereo float32
MAX_EXTRAPOLATION_S = 0.05


def to_stereo(data: np.ndarray) -> np.ndarray:
    """(frames, channels) -> (frames, 2) float32; surround is folded down with -3 dB centre/rears."""
    channels = data.shape[1]
    if channels == 2:
        return np.ascontiguousarray(data, dtype=np.float32)
    if channels == 1:
        return np.ascontiguousarray(np.repeat(data, 2, axis=1), dtype=np.float32)
    # Channel order L, R, C, (LFE,) Ls, Rs, ...: centre to both sides, LFE left out,
    # remaining channels alternate left / right.
    left = data[:, 0].astype(np.float64)
    right = data[:, 1].astype(np.float64)
    left += 0.7071 * data[:, 2]
    right += 0.7071 * data[:, 2]
    lfe = 3 if channels >= 6 else None
    for i, c in enumerate(c for c in range(3, channels) if c != lfe):
        side = left if i % 2 == 0 else right
        side += 0.7071 * data[:, c]
    peak = max(1.0, float(np.max(np.abs(left))), float(np.max(np.abs(right))))
    return np.ascontiguousarray(np.stack([left, right], axis=1) / peak, dtype=np.float32)


class _MixSource(QIODevice):
    """Pull-mode audio source that reads both versions at the same timeline position."""

    def __init__(self) -> None:
        super().__init__()
        self.original: np.ndarray | None = None
        self.mastered: np.ndarray | None = None
        self.offset = 0          # frames: mastered frame = original frame - offset
        self.index = 0           # next frame to deliver, on the original's timeline
        self.end = 0             # timeline length in frames
        self.mix = 0.0           # 0 = original, 1 = mastered
        self.target = 0.0
        self.fade_step = 1.0
        self.gain_original = 1.0
        self.gain_mastered = 1.0
        self.volume = 0.8
        self.open(QIODevice.OpenModeFlag.ReadOnly)

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        return max(0, self.end - self.index) * FRAME_BYTES + super().bytesAvailable()

    @staticmethod
    def _block(buffer: np.ndarray | None, start: int, frames: int) -> np.ndarray:
        out = np.zeros((frames, 2), dtype=np.float32)
        if buffer is not None:
            lo, hi = max(start, 0), min(start + frames, buffer.shape[0])
            if hi > lo:
                out[lo - start:hi - start] = buffer[lo:hi]
        return out

    def render(self, frames: int) -> np.ndarray:
        start = self.index
        if self.mix == self.target:
            if self.mix >= 1.0:
                out = self._block(self.mastered, start - self.offset, frames) * self.gain_mastered
            else:
                out = self._block(self.original, start, frames) * self.gain_original
        else:
            direction = 1.0 if self.target > self.mix else -1.0
            curve = np.clip(self.mix + direction * self.fade_step * np.arange(1, frames + 1), 0.0, 1.0)
            self.mix = float(curve[-1])
            a = (np.cos(curve * (math.pi / 2)) * self.gain_original)[:, None]
            b = (np.sin(curve * (math.pi / 2)) * self.gain_mastered)[:, None]
            out = self._block(self.original, start, frames) * a + self._block(self.mastered, start - self.offset, frames) * b
        out *= self.volume
        np.clip(out, -1.0, 1.0, out=out)
        self.index += frames
        return out.astype(np.float32, copy=False)

    def readData(self, maxlen: int) -> bytes:
        frames = min(maxlen // FRAME_BYTES, self.end - self.index)
        if frames <= 0:
            return b""
        return self.render(frames).tobytes()

    def writeData(self, data) -> int:
        return -1


class _LoadRelay(QObject):
    loaded = Signal(int, str, object, int)   # generation, version, stereo float32 samples, sample rate


class PlaybackEngine(QObject):
    """Plays the selected track and switches between its versions without a gap."""

    stateChanged = Signal(bool)       # playing
    sourceChanged = Signal()          # track, version or loaded buffers changed
    durationChanged = Signal(float)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._source = _MixSource()
        self._sink: QAudioSink | None = None
        self._sink_rate = 0
        self._sink_device = b""
        self._devices = QMediaDevices(self)
        self._devices.audioOutputsChanged.connect(self._on_devices_changed)
        self._relay = _LoadRelay(self)
        self._relay.loaded.connect(self._on_loaded)
        self._clock = QElapsedTimer()
        self._clock.start()
        self._generation = 0
        self._samplerate = 0
        self._mastered_rate = 0
        self._playing = False
        # Watches for the end of the track (the sink's stateChanged enum doesn't reach Python here).
        self._end_watch = QTimer(self)
        self._end_watch.setInterval(100)
        self._end_watch.timeout.connect(self._check_end)
        self._start_frame = 0          # timeline frame where the sink last (re)started
        self._last_processed = -1
        self._last_processed_at = 0
        self._last_position = 0.0
        self.track: Track | None = None
        self._result = None
        self.version = "original"
        self.level_match = False       # off at first, so the whole change is heard
        self.matched_difference_db: float | None = None
        self.volume = 0.8
        self._paths: dict[str, str | None] = {"original": None, "mastered": None}
        self._offset_s = 0.0

    # --- source ------------------------------------------------------------
    def set_track(self, track: Track | None) -> None:
        result = track.result if track else None
        # A re-master keeps the same file names, so the result object tells what changed.
        unchanged = track is self.track and result is self._result
        paths = {"original": track.path if track else None, "mastered": None}
        offset = 0.0
        if result and result.ok and result.output_path and Path(result.output_path).exists():
            paths["mastered"] = result.output_path
            if result.silence is not None:
                offset = result.silence.lead_before_s - result.silence.lead_after_s
        self.track, self._result = track, result
        if unchanged and paths == self._paths:
            return
        self._reset(paths, offset)
        for version, path in paths.items():
            if path:
                self._load(version, path)
        self.sourceChanged.emit()

    def release_files(self) -> None:
        """Stop and forget the current track (its files are about to be replaced)."""
        self.track, self._result = None, None
        self._reset({"original": None, "mastered": None}, 0.0)
        self.sourceChanged.emit()

    def _reset(self, paths: dict, offset_s: float) -> None:
        self._stop_sink()
        self._set_playing(False)
        self._generation += 1
        self._paths, self._offset_s = paths, offset_s
        src = self._source
        src.original = src.mastered = None
        src.index = src.end = 0
        self._samplerate = self._mastered_rate = 0
        self.matched_difference_db = None
        self._start_frame = 0
        self._last_position = 0.0
        if self.version == "mastered" and not paths["mastered"]:
            self.version = "original"
        src.mix = src.target = 1.0 if self.version == "mastered" else 0.0
        self.durationChanged.emit(0.0)

    def _load(self, version: str, path: str) -> None:
        generation, relay = self._generation, self._relay

        def task() -> None:
            try:
                data, samplerate = sf.read(path, dtype="float32", always_2d=True)
                relay.loaded.emit(generation, version, to_stereo(data), int(samplerate))
            except Exception:
                pass            # the player simply stays unavailable for this version

        QThreadPool.globalInstance().start(task)

    def _on_loaded(self, generation: int, version: str, samples: np.ndarray, samplerate: int) -> None:
        if generation != self._generation:
            return
        src = self._source
        # The two files load in parallel and either may arrive first.
        if version == "original":
            self._samplerate = samplerate
            src.original = samples
            src.fade_step = 1.0 / max(1, int(FADE_S * samplerate))
        else:
            self._mastered_rate = samplerate
            src.mastered = samples
        if src.mastered is not None and self._samplerate and self._mastered_rate != self._samplerate:
            src.mastered = None                 # never happens: mastering keeps the sample rate
        if self._samplerate:
            src.offset = int(round(self._offset_s * self._samplerate))
            ends = [src.original.shape[0]] if src.original is not None else []
            if src.mastered is not None:
                ends.append(src.mastered.shape[0] + src.offset)
            src.end = max(ends) if ends else 0
            self.durationChanged.emit(self.duration())
        self.matched_difference_db = self._measure_matched_difference() if self._samplerate else None
        self._apply_gains()
        self.sourceChanged.emit()

    @property
    def ready(self) -> bool:
        return self._source.original is not None

    def has_mastered(self) -> bool:
        return self._source.mastered is not None

    # --- versions --------------------------------------------------------------
    def set_version(self, version: str) -> None:
        if version == self.version or (version == "mastered" and not self.has_mastered()):
            return
        self.version = version
        src = self._source
        src.target = 1.0 if version == "mastered" else 0.0
        if not self._playing:
            src.mix = src.target         # no need to fade while silent
        self.sourceChanged.emit()

    def toggle_version(self) -> None:
        self.set_version("original" if self.version == "mastered" else "mastered")

    # --- transport ---------------------------------------------------------------
    @property
    def playing(self) -> bool:
        return self._playing

    def toggle(self) -> None:
        if self._playing:
            self.pause()
        else:
            self.play()

    def play(self) -> None:
        if not self.ready or self._playing:
            return
        src = self._source
        if src.index >= src.end:
            src.index = 0
        sink = self._ensure_sink()
        if sink is None:
            return
        if sink.state() == QtAudio.State.SuspendedState:
            sink.resume()
        else:
            self._restart_sink()
        self._set_playing(True)

    def pause(self) -> None:
        if self._sink is not None and self._playing:
            self._last_position = self.position()
            self._sink.suspend()
        self._set_playing(False)

    def stop(self) -> None:
        self._stop_sink()
        self._source.index = 0
        self._start_frame = 0
        self._last_position = 0.0
        self._set_playing(False)

    def seek(self, seconds: float) -> None:
        if not self._samplerate:
            return
        src = self._source
        src.index = int(min(max(0.0, seconds), self.duration()) * self._samplerate)
        src.mix = src.target
        self._last_position = src.index / self._samplerate
        if self._playing:
            self._restart_sink()        # flush what was already buffered so the jump is immediate
        else:
            self._stop_sink()
            self._start_frame = src.index

    def _set_playing(self, playing: bool) -> None:
        if playing != self._playing:
            self._playing = playing
            if playing:
                self._end_watch.start()
            else:
                self._end_watch.stop()
            self.stateChanged.emit(playing)

    # --- audio output --------------------------------------------------------------
    def _format(self, samplerate: int) -> QAudioFormat:
        fmt = QAudioFormat()
        fmt.setSampleRate(samplerate)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        return fmt

    def _ensure_sink(self) -> QAudioSink | None:
        if self._sink is not None and self._sink_rate == self._samplerate:
            return self._sink
        self._stop_sink()
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            return None
        self._sink = QAudioSink(device, self._format(self._samplerate), self)
        self._sink.setBufferSize(int(BUFFER_S * self._samplerate) * FRAME_BYTES)
        self._sink_rate = self._samplerate
        self._sink_device = bytes(device.id())
        return self._sink

    def _restart_sink(self) -> None:
        sink = self._ensure_sink()
        if sink is None:
            return
        sink.stop()
        self._start_frame = self._source.index
        self._last_processed = -1
        sink.start(self._source)

    def _stop_sink(self) -> None:
        if self._sink is not None and self._sink.state() != QtAudio.State.StoppedState:
            self._sink.stop()

    def _check_end(self) -> None:
        src = self._source
        if self._playing and src.index >= src.end and (
                self._sink is None or self._sink.state() == QtAudio.State.IdleState):
            self.stop()                 # the last buffered audio has been heard

    def _on_devices_changed(self) -> None:
        # Headphones plugged in or out: rebuild the output on the new default device.
        # Windows also sends this notification when nothing relevant changed; restarting
        # then would put a gap in the music, so only a real change of device counts.
        if self._sink is None or bytes(QMediaDevices.defaultAudioOutput().id()) == self._sink_device:
            return
        was_playing, position = self._playing, self.position()
        self._stop_sink()
        self._sink = None
        self._set_playing(False)
        if was_playing:
            self.seek(position)
            self.play()

    def set_volume(self, volume: float) -> None:
        self.volume = volume
        self._source.volume = max(0.0, min(1.0, volume))

    def set_level_match(self, on: bool) -> None:
        self.level_match = on
        self._apply_gains()

    def _matched_gains(self) -> tuple[float, float] | None:
        """(original, mastered) gains that give both versions the same loudness.

        The louder version is turned down, since playback can't go above full scale.
        """
        r = self._result
        if r is None or not r.ok or self.track is None or self.track.analysis is None:
            return None
        difference = r.after.lufs - self.track.analysis.measurements.lufs
        if not math.isfinite(difference):
            return None
        return (1.0, 10 ** (-difference / 20)) if difference > 0 else (10 ** (difference / 20), 1.0)

    def _apply_gains(self) -> None:
        src = self._source
        gains = self._matched_gains() if self.level_match else None
        src.gain_original, src.gain_mastered = gains or (1.0, 1.0)
        src.volume = max(0.0, min(1.0, self.volume))

    def _measure_matched_difference(self) -> float | None:
        """Level of what differs between the versions once their loudness is matched,
        in dB relative to the music (about -40 dB or lower means they sound the same)."""
        src, gains = self._source, self._matched_gains()
        if src.original is None or src.mastered is None or gains is None:
            return None
        frames = min(src.original.shape[0], int(20 * self._samplerate))
        start = (src.original.shape[0] - frames) // 2
        original = src._block(src.original, start, frames) * gains[0]
        mastered = src._block(src.mastered, start - src.offset, frames) * gains[1]
        music = float(np.sqrt(np.mean(np.square(original, dtype=np.float64))))
        if music < 1e-6:
            return None
        residual = float(np.sqrt(np.mean(np.square(mastered - original, dtype=np.float64))))
        return 20 * math.log10(max(residual, 1e-9) / music)

    # --- position ---------------------------------------------------------------------
    def duration(self) -> float:
        return self._source.end / self._samplerate if self._samplerate else 0.0

    def position(self) -> float:
        """Seconds on the original's timeline, smooth enough for a display-rate playhead."""
        if not self._samplerate:
            return 0.0
        if not self._playing or self._sink is None:
            return self._last_position
        processed = self._sink.processedUSecs()
        now = self._clock.elapsed()
        if processed != self._last_processed:
            self._last_processed, self._last_processed_at = processed, now
        extra = min(MAX_EXTRAPOLATION_S, (now - self._last_processed_at) / 1000.0)
        seconds = self._start_frame / self._samplerate + processed / 1e6 + extra
        seconds = min(seconds, self._source.index / self._samplerate)       # never ahead of the audio sent
        self._last_position = max(self._last_position, seconds)
        return self._last_position

    def local_position(self) -> float:
        """Position within the version being heard (the mastered file has its own start)."""
        position = self.position()
        return position - self._offset_s if self.version == "mastered" else position

    # --- live analysis data -------------------------------------------------------------
    def recent_block(self, seconds: float = 0.1) -> tuple[np.ndarray | None, int]:
        """Mono samples just heard, from the version being heard (for the live analyzer)."""
        if not self._playing or not self._samplerate:
            return None, 0
        src = self._source
        frames = int(seconds * self._samplerate)
        end = int(self.position() * self._samplerate)
        if self.version == "mastered" and src.mastered is not None:
            block = _MixSource._block(src.mastered, end - frames - src.offset, frames)
        else:
            block = _MixSource._block(src.original, end - frames, frames)
        return block.mean(axis=1), self._samplerate

    def momentary_lufs(self) -> float | None:
        if self.track is None or not self._playing:
            return None
        if self.version == "mastered" and self._result is not None:
            curve = self._result.momentary_after
        else:
            curve = self.track.analysis.momentary if self.track.analysis else None
        if curve is None or curve.size == 0:
            return None
        index = int(self.local_position() / MOMENTARY_HOP_S) - 1
        return float(curve[min(max(0, index), curve.size - 1)])
