# Evenfold: guidance for Claude

## What this project is
Evenfold is a Windows desktop app (Python 3.14 + PySide6) that masters a whole
album of WAV files for people with no mastering knowledge. It brings every
track to the same loudness, keeps true peaks under a ceiling and evens out the
silence between tracks. It is distributed **as an executable** (a PyInstaller
onedir zip on GitHub Releases); end users never run Python.

- Repo: https://github.com/glitch191/Evenfold (default branch `main`)
- Owner/maintainer converses in **French**: reply in French. All code, UI text,
  comments, commit messages and docs are in **English**.

## Product rules (from the original spec, keep them)
- **Simple mode by default**: drop files, one "Master My Album" button, results
  in plain language. **Advanced mode** holds the detail (collapsible Advanced
  Settings, closed by default).
- Defaults: -14 LUFS integrated target, -1 dBTP true-peak ceiling.
  The limiter always runs **after** the gain.
- Each file keeps **its own bit depth and sample rate**. Dither (TPDF) is added
  only when the user forces a bit-depth reduction. DSP runs in float64.
- **Originals are never modified.** Output goes to `mastered/` next to the source
  as `<name>_mastered.wav`, written atomically.
- "Preview Results First" = a dry run: everything is processed, nothing is written.
- Output is **reproducible**: same input + settings give byte-identical files.
- Optional EQ matching to a reference track (gentle, ±3 dB max).
- Silence normalisation: 0.1 s lead-in; trailing silence = gap - lead-in;
  segues (tracks without silence at the edges) are left untouched.
- Visualisations use pyqtgraph: OpenGL by default, software toggle in
  Settings › Hardware Acceleration, animation synced to `QScreen.refreshRate`.
- Skippable onboarding walkthrough. Menus: File, Edit/Presets, View, Settings, Help.
  The window opens maximised and remembers its state.
- CSV and PDF reports. Tags (title/artist/album/track) are written with mutagen
  as RIFF INFO + ID3.
- The user asked for **no magic-wand icon anywhere** (the main button has no icon).

## Architecture
| Path | Role |
|---|---|
| `main.py` | GUI entry point: surface format (swap interval 1, 4x MSAA), AppUserModelID, `--software-rendering`, `--reset`, files to open |
| `cli.py` | Headless command line, same pipeline (`python cli.py album/*.wav --dry-run`). Not in the exe and not tested |
| `audio/analysis.py` | Load (float64, shape `(frames, channels)`), BS.1770 loudness via pyloudnorm, 4x-oversampled true peak (chunked `resample_poly`), clipping, waveform, 1/6-octave spectrum |
| `audio/processing.py` | `MasteringSettings` (clamped ranges, presets), EQ match (pedalboard filters), look-ahead true-peak limiter, `normalize_silence`, `master_audio`, dither/`quantize`. Tuning constants are at the top of the file |
| `audio/batch.py` | `TrackJob`/`TrackResult`, output planning, atomic `write_output` (hidden partial file + `os.replace`, PEAK chunk disabled), `MemoryBudget` (half of physical RAM), `BatchRunner` on `QThreadPool` (1–4 workers) |
| `audio/metadata.py`, `audio/summary.py` | Tags; plain-language descriptions of the results |
| `ui/session.py` | App state: tracks, settings, `results_stale`, run bookkeeping (`last_run_dry`, `last_summary`, `batch_settings`, `writing`), `aboutToStart`, cancel reverts status |
| `ui/playback.py` | `PlaybackEngine`: `QAudioSink` in pull mode fed by a `_MixSource` QIODevice; seamless Original↔Mastered switch through an equal-power crossfade; optional level match (off by default) |
| `ui/main_window.py` | Menus, shortcuts (Space = play/pause, A = switch version), report export |
| `ui/widgets.py`, `simple_view.py`, `advanced_view.py`, `views_common.py`, `visualizers.py`, `dialogs.py`, `onboarding.py`, `presets.py`, `settings.py` | Views and widgets |
| `ui/theme.py` + `ui/theme.qss` | **All** colours, fonts and constants (`APP_NAME`, `APP_VERSION`…). The QSS uses `@token`s that `theme.stylesheet()` substitutes. `AppStyle` (QProxyStyle on Fusion) enables click-anywhere seeking on sliders |
| `ui/tooltips.py` | **All** help texts: `TIPS`, `BASICS`, `ONBOARDING` |
| `reports/report_export.py` | CSV + PDF (fpdf2, Segoe UI TTF) |
| `tools/` | `evenfold.spec` (PyInstaller; `EVENFOLD_CHECK=1` builds the console twin `EvenfoldCheck.exe`), `build_exe.ps1`, `frozen_check.py`, `make_icon.py`, `evenfold.ico`, `version_info.txt` |
| `tests/` | `test_pipeline.py` (audio), `test_ui_session.py` (headless UI state), `make_test_album.py` (deterministic, deliberately messy album), `ui_smoke.py` (drives the real window, saves screenshots) |
| `.github/workflows/release.yml` | Windows CI build (see below) |

Mastering chain per track: read format → analyse → optional EQ → gain to target
→ true-peak limiter (5 ms look-ahead, forward max + backward mean guarantees the
ceiling; dual release 60 dB/s fast, 8 dB/s slow on a 60 ms window; up to 2 dB
make-up then re-measure) → silence normalisation → quantize in the source format
(24-bit left-aligned in int32; TPDF dither seeded by the crc32 of the audio) → write.

## Conventions
- Match the existing style: module docstring at the top, `from __future__ import
  annotations`, dataclasses, named constants at the top of modules, sparse comments
  that explain *why*.
- No UI string or colour inline: help text goes in `ui/tooltips.py`, visual values
  in `ui/theme.py` (then referenced from the QSS as `@token`).
- Icons: qtawesome Phosphor set (`ph.` prefix).
- Qt Multimedia: use the `QtAudio` enums (not `QAudio`).
- Keep the DSP deterministic (no unseeded randomness, no timestamps in files).
- The README is written for **end users of the exe**: never tell them to run a
  Python command. Build instructions stay in "For developers".

## Commands (PowerShell, from the repo root)
```powershell
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt   # setup
.venv\Scripts\python -m pytest tests -q -p no:cacheprovider           # 28 tests, ~45 s
.venv\Scripts\python -m tests.make_test_album                         # -> tests\album (gitignored)
.venv\Scripts\python main.py [files] [--software-rendering] [--reset]  # run from source (dev only)
.venv\Scripts\python -m tests.ui_smoke OUT_DIR [--software] [--size 1920x1080]
powershell -ExecutionPolicy Bypass -File tools\build_exe.ps1          # -> dist\Evenfold\Evenfold.exe
```
`build_exe.ps1` builds the app, then builds and runs `EvenfoldCheck.exe`, which
masters the test album end to end (it prints `ALL OK`). **Close Evenfold.exe
first**: a running exe locks `dist\` and the build fails.

Local environment when this file was written: Python 3.14.7, PySide6 6.11.2,
pyqtgraph 0.14.0, numpy 2.5.3, scipy 1.18.1, pedalboard 0.9.25, pyloudnorm
0.2.0, soundfile 0.14.0, mutagen 1.48.1, fpdf2 2.8.8, QtAwesome 1.4.2,
PyInstaller 6.22.3. `requirements.txt` sets minimum versions only (`>=`).

## CI and releases
- `release.yml` runs on windows-latest with Python 3.14. Triggers: push to
  `main`/`master` (docs-only changes are ignored: `**.md`, `docs/**`,
  `.gitignore`), `v*` tags, pull requests and manual runs. A newer push cancels
  the running build of the same ref.
- Steps: `pytest tests/test_pipeline.py` (CI does not run the UI tests) →
  `build_exe.ps1` → zip `Evenfold-<tag|sha7>-windows.zip` → artifact. On a tag,
  it also publishes a GitHub Release (softprops/action-gh-release, auto notes).
- CI has no sound card: `frozen_check.py` skips the playback check there.
- **Release procedure**: bump the version in `ui/theme.py` (`APP_VERSION`) **and**
  `tools/version_info.txt` (filevers, prodvers, FileVersion, ProductVersion),
  commit, then `git tag vX.Y.Z` and `git push origin vX.Y.Z`.
- Workflow: changes go through a branch + PR (the user uses `/create-pr`).

## Decisions and their reasons
- **PyInstaller onedir** rather than onefile: faster start, no extraction to temp.
  The zip must keep `_internal\` next to the exe.
- **Own quantization and PEAK chunk off**: libsndfile writes a timestamp in the
  PEAK chunk, which broke byte-identical output.
- **Level match off by default** in the player: when it was on, the user heard
  "the same file" on both versions (mastering is mostly a volume change). A hint
  now explains the option.
- **Pull-mode `QAudioSink` + crossfade mixer** (instead of two media players):
  gives a gapless A/B switch at the same position. The timeline offset
  is `lead_before - lead_after`, because silence normalisation shifts the audio.
- **Polling timer to detect the end of playback**, rather than `stateChanged`
  (unreliable). Separate sample rates for the original and mastered buffers, and
  a device-id check to avoid spurious restarts on device-change signals.
- **`tools/` rather than `packaging/`**: a local `packaging` folder shadowed the
  PyPI `packaging` library and broke the build.
- **Licences**: source MIT ("Copyright (c) 2026 glitch191"). The bundled exe is
  distributed under GPLv3 terms because of pedalboard (GPLv3) and mutagen
  (GPLv2+); PySide6 and fpdf2 are LGPLv3. All are listed in Help › About
  (`ui/dialogs.py`, `LIBRARIES`).
- Settings are stored in `HKCU\Software\Evenfold` (QSettings), presets in
  `%APPDATA%\Evenfold`, the icon cache in `%LOCALAPPDATA%\Evenfold`.

## Known testing limits
- `QWidget.grab()` does not capture the OpenGL plots: GPU rendering can only be
  checked by eye. For screenshots, use the `windows` QPA platform with
  `WA_DontShowOnScreen` (the `offscreen` platform renders fonts badly).
- pyqtgraph fails when given an empty list of pens (see the `_set_bars` helper).
