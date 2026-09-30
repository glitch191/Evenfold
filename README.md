# Evenfold: album mastering

Desktop app that masters every WAV file of an album so the songs share one
loudness, never clip, and flow into each other with even gaps. Each file keeps
its own bit depth and sample rate, and originals are never modified.

Simple mode is the default: drop files, click **Master My Album**, read what
changed in plain language. Advanced mode adds the tone analyzer, the album
overview, the true-peak meter, an A/B player, editable track details, detailed
results, reports and every setting.

## Run

```
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python main.py
```

Options: `main.py [files or folders] [--software-rendering] [--reset]`.

## Windows executable

`dist\Evenfold\Evenfold.exe` runs without Python installed. Keep the whole
`dist\Evenfold` folder together (the `_internal` folder next to the .exe holds
Qt, numpy and the other libraries); zip that folder to share it. A folder build
is used rather than a single file because it starts in seconds instead of
unpacking about 300 MB on every launch.

To rebuild it (the script also builds a console twin that masters the test
album end to end, to prove every dependency made it into the bundle):

```
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1
```

### Automatic builds on GitHub

`.github/workflows/release.yml` builds the app on GitHub's Windows machines:

- **Every push to `main`** (the first upload of the repository included) and
  every pull request: the zipped app, named after the commit, is on the run's
  page (Actions tab > the run > Artifacts). Changes to documentation only don't
  trigger a build.
- **Publish a version**: push a tag, and the zipped app is attached to a GitHub
  Release for it.
  ```
  git tag v1.0.0
  git push origin v1.0.0
  ```
- **Test build**: Actions tab > *Build Windows app* > *Run workflow*; the zip is
  available on the run's page (Artifacts).

The workflow runs the audio pipeline tests, then the same build script and
frozen-build check as above. GitHub's machines have no sound card, so only the
playback part of the check is skipped there.

Command line (same pipeline, no window):

```
.venv\Scripts\python cli.py "album/*.wav"
.venv\Scripts\python cli.py "album/*.wav" --dry-run
.venv\Scripts\python cli.py "album/*.wav" --target -16 --eq-ref "album/03 Low Tide.wav" --bit-depth 16
```

Tests:

```
.venv\Scripts\python -m pytest tests -q          # audio pipeline, against the spec's constraints
.venv\Scripts\python -m tests.make_test_album    # a deliberately messy 6-track test album
.venv\Scripts\python -m tests.ui_smoke OUT --hidden --software   # drives the real window, saves screenshots
```

## The mastering chain

For every file, in parallel (`QThreadPool`, at most 4 tracks at a time, and
never more than half the RAM, see `MemoryBudget`):

1. **Read** the exact subtype, sample rate and channels (`soundfile`), then the
   audio as float64.
2. **Analyze**: integrated loudness (`pyloudnorm`, BS.1770-4), 4x-oversampled
   true peak, clipping (runs of 3+ full-scale samples; flat tops for float
   files, where overs above 0 dBFS are reported separately because they aren't
   damaged yet), waveform overview, long-term spectrum, momentary loudness.
3. **Tone matching** (optional): the spectral shape of each track is compared with a
   reference track in 5 bands; gentle shelf and peak filters (`pedalboard`,
   ±3 dB max, 50 % strength by default) move it toward the reference.
4. **Gain** to the target (-14 LUFS by default).
5. **True-peak limiter** at -1 dBTP, always after the gain (see below).
6. **Silence**: a 0.1 s lead-in and a trailing silence of (gap - 0.1 s), so tracks
   played in order are 2 s apart. Starts or ends with no silence at all (segues)
   are left untouched.
7. **Write** a new file `<name>_mastered.wav` in a `mastered` folder next to the
   original (or a chosen folder), in the source's own format, atomically (a
   hidden partial file renamed into place). Title, artist, album and track number
   are written as RIFF INFO and ID3.

Dry run ("Preview Results First") runs steps 1-6 in memory and writes nothing.

## Design decisions worth knowing

- **Custom true-peak limiter instead of `pedalboard.Limiter`.** Pedalboard's
  limiter works on sample peaks and has no look-ahead, so it can't guarantee a
  -1 dBTP ceiling. The limiter in `audio/processing.py` works from the 4x
  oversampled peak envelope, with 5 ms look-ahead, a dual release (fast after
  transients, slow during sustained limiting), and a re-measurement pass that
  guarantees the ceiling. It is fully vectorised with numpy (no Python loop per
  sample). If limiting pulls a track below the target, up to 2 dB of make-up gain
  is added and the limiter runs again. Pedalboard is still used for the EQ.
- **Apple Music.** Spotify and YouTube normalize to -14 LUFS, but Apple Music
  uses -16 LUFS. The tooltips say so; -14 remains the default.
- **Bit-exact formats.** Integer output is rounded and clipped by the app itself
  (not by libsndfile's float scaling), so untouched audio round-trips
  bit-exactly. Dither (TPDF, ±1 LSB) is applied only when the user forces a
  lower bit depth, with a seed derived from the audio, so exports are
  reproducible byte for byte (libsndfile's timestamped PEAK chunk is disabled
  for float files for the same reason).
- **Seamless A/B listening.** Both versions of the selected track are decoded
  into memory and played through one `QAudioSink` stream that reads them at the
  same timeline position (`ui/playback.py`). Switching Original / Mastered (or
  pressing A) never stops the music: a 20 ms equal-power crossfade moves from one
  version to the other, with level matching applied in the same mix. The switch
  is heard after the output buffer (about 50 ms). Clicking anywhere on a slider
  jumps straight there.
- **Rendering.** Plots use pyqtgraph with an OpenGL viewport by default. The
  toggle (Settings > Hardware Acceleration, or Advanced Settings) switches every
  live plot at runtime, with no restart. Animation (playhead, live loudness
  needle, live spectrum) runs on a `QTimer` whose interval follows
  `QScreen.refreshRate()` of the window's current screen, and only while audio
  plays.
- **Centralized styling and text.** Colours, type sizes and spacing live in
  `ui/theme.py`; the stylesheet `ui/theme.qss` uses them as `@tokens`. Every
  tooltip and teaching text lives in `ui/tooltips.py`, which also feeds
  Help > Mastering Basics and the first-run walkthrough.

## Layout

```
main.py                  entry point (maximized window, GL surface format)
cli.py                   command-line mastering
audio/analysis.py        source format, LUFS, true peak, clipping, display data
audio/processing.py      settings, EQ matching, gain, true-peak limiter, silence, quantization
audio/batch.py           output planning, per-track pipeline, thread pools, memory budget
audio/metadata.py        RIFF INFO + ID3 tags
audio/summary.py         plain-language result descriptions
ui/session.py            the album model: tracks, settings, background analysis and batches
ui/main_window.py        window, menus, modes, full screen, persistence, drag and drop
ui/simple_view.py        Simple mode
ui/advanced_view.py      Advanced mode (resizable zones)
ui/views_common.py       waveform / spectrum / album cards shared by both modes
ui/visualizers.py        pyqtgraph plots, painted meters, refresh-rate clock
ui/widgets.py            cards, track list and tables, panels, settings, player
ui/onboarding.py         first-run walkthrough
ui/dialogs.py            Mastering Basics, About, presets, default target
ui/settings.py           QSettings wrapper
ui/presets.py            built-in and saved presets
ui/theme.py, theme.qss   design tokens and stylesheet
ui/tooltips.py           all tooltip and teaching text
reports/report_export.py CSV and PDF reports
tests/                   pipeline tests, test-album generator, UI smoke test
tools/                   .exe build: PyInstaller recipe, icon, version info, frozen-build check
```
