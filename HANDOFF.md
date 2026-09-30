# Handoff: Evenfold (state as of 2026-09-30)

This file replaces the conversation history of the previous Claude account,
which does not carry over. Durable context is in `CLAUDE.md`.

## Current state
- `main` = `65958a7` (merge of PR #1, MIT licence), in sync with `origin/main`,
  working tree clean. Only branch: `main` (`add-mit-license` deleted locally and
  on GitHub).
- **v1.0.0 is released**: tag `v1.0.0` on `65958a7`, GitHub Release
  "Evenfold 1.0.0" published 2026-09-30 by the tag's CI run (success).
- Local tests: 28/28 pass (`pytest tests`).
- The latest local build, `dist\Evenfold\Evenfold.exe`, passed the frozen check:
  6 tracks at -14 ±0.04 LUFS in their original formats, seamless A/B, reports OK.
  *Uncertain*: that build predates the last two commits. Those commits changed
  only README.md and LICENSE, so the code should be identical, but this has not
  been checked.

## Done
- The whole app from the spec: Simple/Advanced modes, mastering chain, dry run,
  parallel batch, EQ match, silence normalisation, presets, CSV/PDF reports, tags,
  onboarding, menus, window state, visualisations (GPU + software toggle).
- A/B player: click-anywhere seek, seamless Original↔Mastered switch (A key),
  optional level match (off by default, with an explanatory hint).
- Magic-wand icon removed everywhere.
- Cleanup of large unused folders and files.
- GitHub CI: build on push/PR, release on `v*` tag.
- README for end users of the exe (screenshots in `docs/`), MIT licence plus
  explanation of the bundled libraries' licences.

## In progress
Nothing in progress in the code.

## Next steps (prioritised)
1. **Check GPU rendering by eye**: open Advanced mode with Hardware Acceleration
   on, then off (Settings menu). The plots can't be captured automatically, and
   the user has not confirmed yet that they display correctly.
2. **Test the release zip on a clean Windows machine** (download from Releases,
   SmartScreen warning, first launch, playback). Not done, as far as known.
3. **Licence decision** (see open questions): if the exe must be MIT-compatible,
   replace pedalboard (EQ filters in `audio/processing.py`, `apply_eq`) and mutagen
   (tags in `audio/metadata.py` / `audio/batch.py`). Nothing started.
4. *Suggestion, not requested*: pin the dependency versions for CI builds
   (`requirements.txt` sets minimums only, so a new PySide6/pyqtgraph/PyInstaller
   release can change the exe without any code change).
5. *Suggestion, not requested*: the script that generated `docs/simple-mode.png`
   and `docs/advanced-mode.png` existed only in the previous session's temporary
   folder. It is not in the repo. It loaded `tests/album`, mastered the album,
   grabbed the window in Simple mode, then in Advanced mode during playback
   (`w.grab()` at 1920×1080, `WA_DontShowOnScreen`). Add it to `tools/` if the
   screenshots need regenerating.

## Pitfalls encountered
- **A running Evenfold.exe locks `dist\`**: close the app before
  `build_exe.ps1` (or build elsewhere and swap in afterwards).
- **A local folder named `packaging`** shadows the PyPI library: hence `tools/`.
- **libsndfile PEAK chunk** = timestamp = non-reproducible output: it is disabled
  (`_SFC_SET_ADD_PEAK_CHUNK` in `audio/batch.py`).
- **EQ test disturbed by the limiter**: the test uses a -24 LUFS target, so the
  limiter does not act.
- **True peak slow** on long files: fixed with strided `np.maximum` per chunk.
- **Qt Multimedia**: the `QAudio`/`QtAudio` enums don't match (use `QtAudio`);
  `stateChanged` is unreliable (use a polling timer); race between the two
  versions' sample rates (separate variables); false device changes (compare
  the device id).
- **"I hear the same file on both versions"**: that was level match being on
  by default. It is now off by default.
- **pyqtgraph**: an empty list of pens raises an exception; OpenGL teardown raised
  a `RuntimeError` (caught in `ui/visualizers.py`).
- **Screenshots**: the `offscreen` platform renders fonts badly, and `grab()`
  misses the GL plots.
- **GitHub Desktop "not a Git repository"**: resolved by the previous session;
  the details of the fix aren't in the notes that carried over.
- **gh CLI**: had to be installed, then authenticated (account `glitch191`).
- **CI**: the first two runs on `main` show as *cancelled*. They were replaced by
  newer pushes (concurrency), not failures.

## Open questions (for the user)
- Keep the exe under GPLv3 (pedalboard + mutagen), or replace those two libraries
  so it can be MIT-compatible?
- Should the copyright holder in `LICENSE` stay "glitch191", or be a real name?
- `cli.py` (command-line mastering) is neither documented in the README, nor
  tested, nor included in the exe: keep it, test it, or remove it?
- Should `HANDOFF.md` be committed to the repo, or stay local?
