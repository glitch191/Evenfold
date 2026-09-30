"""Every tooltip and piece of teaching text in the app, in one place.

Tooltips explain the idea behind a control, not just what the button does.
The Help > Mastering Basics page and the first-run walkthrough draw from the
same text, so the explanations stay consistent everywhere.
"""

from __future__ import annotations

from html import escape

# key -> (title, explanation)
TIPS: dict[str, tuple[str, str]] = {
    # --- Main actions ---------------------------------------------------------
    "master": (
        "Master My Album",
        "Runs the whole mastering chain on every track: matches their volume to the streaming "
        "standard, keeps peaks safe from distortion, evens out the silence between songs and saves "
        "new files in a “mastered” folder. Your original files are never modified.",
    ),
    "preview": (
        "Preview Results First",
        "A dry run: every track is measured and processed in memory so you can see what would "
        "change, but no file is created or modified. Use it to check the results before saving.",
    ),
    "cancel": ("Cancel", "Stops processing. Files that were already saved stay; nothing half-written is left behind."),
    "add_files": (
        "Add tracks",
        "Choose the WAV files of your album. You can mix bit depths (16, 24, 32-bit) and sample "
        "rates (44.1, 48, 96 kHz and more): each file keeps its own format.",
    ),
    "remove_track": ("Remove from the album", "Takes the track out of this list. The file on your disk is not touched."),
    "clear_tracks": ("Clear the album", "Removes every track from the list. Files on your disk are not touched."),
    "open_output": ("Open Output Folder", "Opens the folder where the mastered files were saved."),
    "mode": (
        "Simple or Advanced",
        "Simple shows only what you need to master an album in one click. Advanced adds detailed "
        "meters, the tone analyzer, the audio player and every setting.",
    ),
    "help": ("Getting Started", "Replays the short introduction to the app."),
    "basics": ("Mastering Basics", "Every idea behind the settings, explained in plain language, in one place."),
    "about": ("About", "Version and the libraries this app is built with."),
    "exit": ("Exit", "Closes the app. Mastered files that were already saved stay where they are."),
    "originals_safe": (
        "Your originals are safe",
        "Mastering always writes new files. The files you added are only ever read, never changed, "
        "moved or deleted.",
    ),

    # --- Mastering concepts -------------------------------------------------------
    "target_lufs": (
        "Loudness target (LUFS)",
        "LUFS measures how loud a track feels on average, the way ears perceive it, not its "
        "highest peak. Spotify, YouTube and most streaming services play music back at about "
        "-14 LUFS (Apple Music uses a slightly quieter -16), so mastering to -14 means your album "
        "plays as intended there. Lower numbers (-16, -18) are quieter and keep more dynamics; "
        "higher numbers (-10, -9) are louder but more squashed.",
    ),
    "ceiling": (
        "Peak ceiling (dBTP)",
        "True peak is the real maximum level of the signal, including the peaks that appear "
        "between samples once the audio is converted to analog or encoded to MP3/AAC. A limiter "
        "set to -1 dBTP stops every peak 1 dB below the digital maximum, leaving a safety margin "
        "so nothing distorts on any device.",
    ),
    "true_peak": (
        "True peak",
        "The highest level the signal really reaches, including peaks between samples. 0 dBTP is "
        "the digital maximum: going above it causes distortion. Headroom is how far below that "
        "maximum the track stays.",
    ),
    "limiter": (
        "Limiter",
        "An automatic, very fast volume control that turns down only the loudest peaks, so the "
        "track can be louder overall without ever going over the peak ceiling. It always acts "
        "after the volume has been matched.",
    ),
    "clipping": (
        "Clipping",
        "Clipping happens when a signal is pushed past the digital maximum: the tops of the "
        "waveform get cut flat, which sounds like harsh, crackly distortion. It can't be undone: "
        "if a file is already clipped, mastering can't repair it, only avoid adding more. The fix "
        "is to go back to the mix and export it at a lower level.",
    ),
    "gain": (
        "Gain",
        "How much the volume was raised (+) or lowered (-), in decibels. Around +6 dB doubles the "
        "signal level; a change under 1 dB is barely noticeable.",
    ),
    "eq_match": (
        "Tone matching (EQ)",
        "Gently adjusts the balance between bass, mids and treble of each track so it sounds "
        "closer to a reference track you pick from the album. Corrections are small (3 dB at most) "
        "so every song keeps its own character.",
    ),
    "eq_reference": (
        "Reference track",
        "The song whose tonal balance the others are nudged toward. Pick the one that sounds best "
        "to you. The reference itself isn't changed.",
    ),
    "eq_strength": (
        "Matching strength",
        "How far each track moves toward the reference's tone. 50% is a gentle middle ground; "
        "100% goes as far as the 3 dB safety limit allows.",
    ),
    "even_spacing": (
        "Even out gaps between tracks",
        "Makes the silence at the start and end of every song consistent, so the album flows when "
        "played from start to finish, without long pauses or songs crashing into each other. "
        "Songs that run straight into the next one, with no silence at all, are left as they are.",
    ),
    "gap": (
        "Gap between tracks",
        "Total silence between the end of one song and the start of the next when the album plays "
        "in order. 2 seconds is the classic CD spacing.",
    ),
    "bit_depth_override": (
        "Same bit depth for all files",
        "Normally each file keeps its own bit depth. Some distributors ask for a single format, "
        "such as 24-bit, or 16-bit for CD. Going down in bit depth (24 to 16-bit, for example) "
        "needs dithering: a tiny, inaudible noise that prevents grainy distortion in quiet "
        "passages and fade-outs. It is added automatically, only in that case.",
    ),
    "sample_rate": (
        "Sample rate",
        "How many times per second the sound is measured (44.1 kHz for CD, 48 kHz for video, "
        "96 kHz for high resolution). Each file keeps its own rate: nothing is resampled.",
    ),
    "format": (
        "File format",
        "Bit depth and sample rate of the original file. The mastered file keeps exactly the same "
        "format unless you choose a common bit depth in Advanced Settings.",
    ),
    "output_folder": (
        "Where mastered files go",
        "New files get “_mastered” added to their name. By default they go into a "
        "“mastered” folder next to the originals, or into a folder you choose.",
    ),
    "album_details": (
        "Album details",
        "Title, artist, album and track number are written into the exported files so music "
        "players and distributors display them correctly.",
    ),
    "track_number": ("Track number", "Position of the song on the album. It's written into the mastered file."),
    "track_title": ("Title", "Song title written into the mastered file. Double-click to edit."),
    "track_artist": ("Artist", "Leave empty to use the album artist."),
    "presets": (
        "Presets",
        "Save the current mastering settings under a name and reuse them for your next album, so "
        "all your releases sound consistent.",
    ),
    "save_preset": ("Save Preset", "Stores the current settings (loudness, peaks, tone matching, spacing, bit depth) under a name."),
    "load_preset": ("Load Preset", "Applies settings saved earlier, or one of the built-in starting points."),
    "manage_presets": ("Manage Presets", "Rename or delete the presets you saved."),
    "builtin_preset": ("Built-in preset", "A ready-made starting point that comes with the app. It can't be renamed or deleted."),
    "hardware_acceleration": (
        "Hardware acceleration",
        "Draws the waveform and analyzers with your graphics card (GPU), which keeps them smooth "
        "at your screen's full refresh rate (120 Hz, 144 Hz and up) even on long files. Turn it off "
        "if graphics flicker or look corrupted: drawing then uses the processor instead.",
    ),
    "reset_settings": ("Reset to defaults", "Puts every mastering setting back to the recommended defaults."),

    # --- Views ---------------------------------------------------------------------
    "waveform": (
        "Waveform",
        "A picture of the track's level over time. Top: your original. Bottom: the mastered "
        "version. Click to jump there during playback; scroll to zoom, double-click to reset.",
    ),
    "loudness_meter": (
        "Loudness meter",
        "How loud the track feels (LUFS) compared with the target. Green is on target, amber is "
        "close, red is far off. The hollow marker is the original, the solid one the mastered "
        "result; during playback a thin line follows the loudness live.",
    ),
    "true_peak_meter": (
        "True peak meter",
        "Each version's highest true peak, and how much headroom is left below 0 dBTP, the "
        "digital maximum.",
    ),
    "spectrum": (
        "Tone analyzer",
        "How the track's energy is spread from deep bass (left) to high treble (right). Compare "
        "the original, the mastered version and the reference track to see what tone matching "
        "did. During playback the analyzer follows the music live.",
    ),
    "batch_comparison": (
        "Album loudness overview",
        "Every track's loudness side by side. After mastering all bars line up on the target line: "
        "that's what makes the album feel consistent. Click a bar to select its track.",
    ),
    "results_table": (
        "Detailed results",
        "Before-and-after measurements for every track. Hover a column title to learn what it means.",
    ),
    "export_report": (
        "Export Report",
        "Saves a summary of every track (loudness, gain, peaks, clipping, format) as a CSV "
        "spreadsheet or a PDF, for your records or for a distributor.",
    ),
    "player": ("Player", "Listen to the original and the mastered version of the selected track."),
    "play": ("Play / Pause", "Plays the selected track (space bar)."),
    "stop": ("Stop", "Stops playback and goes back to the start."),
    "ab_toggle": (
        "Original or Mastered",
        "Switches instantly between the original and the mastered version while the music keeps "
        "playing, at exactly the same point in the song, so you can compare them in real time. "
        "Press A to switch without the mouse.",
    ),
    "seek": ("Position", "Click anywhere on the bar to jump there, or drag to scrub through the song."),
    "ab_unavailable": (
        "Original or Mastered",
        "Master your album to listen to the mastered version. A preview doesn't save any file, so "
        "there is nothing to play yet.",
    ),
    "level_match": (
        "Compare at equal volume",
        "Louder always sounds “better” at first, which makes fair comparisons hard. This "
        "plays both versions at the same perceived loudness, so you only hear what changed besides "
        "the volume: tone and the loudest peaks. It's off at first so you hear the whole change.",
    ),
    "level_match_identical": (
        "Almost identical at equal volume",
        "At equal volume these two versions are practically the same: for this track, mastering "
        "mainly changed the volume. Turn off “Compare at equal volume” to hear that change.",
    ),
    "level_match_subtle": (
        "Subtle at equal volume",
        "At equal volume the difference is subtle: mostly the loudest peaks were softened. Turn off "
        "“Compare at equal volume” to hear the volume change as well.",
    ),
    "volume": ("Volume", "Playback volume. It doesn't affect the exported files."),
    "reset_layout": ("Reset Layout", "Puts every panel back to its original size and visibility."),
}


def tip(key: str) -> str:
    """Rich-text tooltip: bold title, then the explanation."""
    title, body = TIPS[key]
    return f"<b>{escape(title)}</b><br>{escape(body)}"


def album_bar_tip(number: int, title: str, before: float, after: float | None) -> str:
    """Hover text for one bar of the album loudness overview."""
    text = f"<b>{number}. {escape(title)}</b><br>Original: {before:.1f} LUFS"
    if after is not None:
        text += f"<br>Mastered: {after:.1f} LUFS"
    return text


# Column header tooltips for the results table: header -> tooltip key
RESULT_COLUMN_TIPS = {
    "Loudness before": "target_lufs",
    "Loudness after": "target_lufs",
    "Gain": "gain",
    "True peak before": "true_peak",
    "True peak after": "true_peak",
    "Limiting": "limiter",
    "Clipping": "clipping",
    "Format": "format",
}


# Help > Mastering Basics: (topic, paragraphs)
BASICS: list[tuple[str, list[str]]] = [
    ("What mastering does", [
        "Mastering is the last step before release. It makes the songs of an album sound like they "
        "belong together, at a volume that suits streaming services, with no distortion.",
        "This app does the technical part automatically: matching loudness, keeping peaks safe, "
        "optionally balancing tone, and evening out the silence between songs.",
    ]),
    ("Loudness and LUFS", [TIPS["target_lufs"][1],
        "Tracks mastered to the same LUFS feel equally loud, which is why an album sounds "
        "consistent after mastering even when the songs started out very different."]),
    ("True peak and the limiter", [TIPS["ceiling"][1], TIPS["limiter"][1]]),
    ("Clipping", [TIPS["clipping"][1]]),
    ("Tone matching", [TIPS["eq_match"][1], TIPS["eq_reference"][1]]),
    ("Gaps between songs", [TIPS["even_spacing"][1], TIPS["gap"][1]]),
    ("Bit depth, sample rate and dithering", [
        "Bit depth sets how finely each sample's level is stored (16-bit for CD, 24-bit for studio "
        "work, 32-bit float for maximum headroom). " + TIPS["sample_rate"][1],
        TIPS["bit_depth_override"][1],
    ]),
    ("Previewing safely", [TIPS["preview"][1], TIPS["originals_safe"][1]]),
    ("Listening fairly", [TIPS["level_match"][1],
        "Listen on the speakers or headphones you know best, at a comfortable volume, and switch "
        "between Original and Mastered at the same point in the song."]),
    ("Presets", [TIPS["presets"][1]]),
    ("Hardware acceleration", [TIPS["hardware_acceleration"][1]]),
]


# First-run walkthrough: (step label, title, body, icon name)
ONBOARDING: list[tuple[str, str, str, str]] = [
    ("Welcome", "Make your album sound like one album",
     "Evenfold masters every song of your album so they share the same volume, stay free of "
     "distortion and flow into each other smoothly. No mastering knowledge needed.",
     "ph.sparkle"),
    ("Step 1", "Add your songs",
     "Drag your WAV files into the window, or click Add Files. Any mix of bit depths and sample "
     "rates works: each file keeps its own format.",
     "ph.upload-simple"),
    ("Step 2", "Click Master My Album",
     "That's it. Volume is matched to the streaming standard (-14 LUFS), peaks are kept safe "
     "(-1 dBTP) and gaps are evened out. New files are saved in a “mastered” folder, "
     "and your originals are never modified.",
     "ph.check-circle"),
    ("Step 3", "Learn as you go",
     "Hover over anything to see what it means. Switch to Advanced mode for detailed meters, the "
     "player and more settings, and find every explanation in Help › Mastering Basics.",
     "ph.book-open"),
]
