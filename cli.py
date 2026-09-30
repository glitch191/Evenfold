"""Command-line mastering: the same pipeline as the app, without the window.

Examples:
    python cli.py album/*.wav
    python cli.py album/*.wav --dry-run
    python cli.py album/*.wav --target -16 --eq-ref "album/03 Low Tide.wav" --bit-depth 16
"""

from __future__ import annotations

import argparse
import glob
import math
import sys
import time
from pathlib import Path

from audio.analysis import FLOOR_DB, format_samplerate, SUBTYPES
from audio.batch import TrackJob, output_folders, run_batch
from audio.metadata import guess_from_filename
from audio.processing import MasteringSettings
from audio.summary import batch_summary

BIT_DEPTHS = {"keep": None, "16": "PCM_16", "24": "PCM_24", "32": "PCM_32", "32f": "FLOAT"}


def _lufs(value: float) -> str:
    return "  n/a" if not math.isfinite(value) or value <= FLOOR_DB else f"{value:6.1f}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Master a batch of WAV files to a consistent loudness.")
    parser.add_argument("files", nargs="+", help="WAV files (glob patterns allowed)")
    parser.add_argument("--target", type=float, default=-14.0, help="target loudness in LUFS (default -14)")
    parser.add_argument("--ceiling", type=float, default=-1.0, help="true-peak ceiling in dBTP (default -1)")
    parser.add_argument("--dry-run", action="store_true", help="measure only, write nothing")
    parser.add_argument("--out", help="output folder (default: a 'mastered' folder next to each file)")
    parser.add_argument("--eq-ref", help="reference track for gentle EQ matching")
    parser.add_argument("--eq-strength", type=float, default=0.5, help="EQ matching strength 0..1")
    parser.add_argument("--bit-depth", choices=sorted(BIT_DEPTHS), default="keep",
                        help="force one output bit depth for all files (dithered when reduced)")
    parser.add_argument("--no-spacing", action="store_true", help="leave silences at start/end untouched")
    parser.add_argument("--gap", type=float, default=2.0, help="seconds between tracks (default 2)")
    parser.add_argument("--workers", type=int, default=None, help="tracks processed in parallel")
    args = parser.parse_args(argv)

    paths: list[str] = []
    for pattern in args.files:
        matches = sorted(glob.glob(pattern)) or [pattern]
        paths.extend(m for m in matches if m.lower().endswith(".wav"))
    if not paths:
        print("No WAV files found.", file=sys.stderr)
        return 2

    settings = MasteringSettings(
        target_lufs=args.target, ceiling_dbtp=args.ceiling, eq_match=bool(args.eq_ref),
        eq_reference=args.eq_ref, eq_strength=args.eq_strength, even_spacing=not args.no_spacing,
        gap_s=args.gap, output_subtype=BIT_DEPTHS[args.bit_depth], output_dir=args.out,
    )
    jobs = []
    for i, path in enumerate(paths):
        number, title = guess_from_filename(path)
        jobs.append(TrackJob(path=path, title=title, track_number=number or i + 1, track_total=len(paths)))

    started = time.perf_counter()
    results = run_batch(jobs, settings, dry_run=args.dry_run, workers=args.workers)
    elapsed = time.perf_counter() - started

    header = f"{'File':34} {'Format':26} {'LUFS in':>7} {'Gain':>6} {'LUFS out':>8} {'TP in':>6} {'TP out':>6} {'Limit':>5}  Clip"
    print(header)
    print("-" * len(header))
    for r in results:
        name = Path(r.path).name[:34]
        if r.error:
            print(f"{name:34} ERROR: {r.error}")
            continue
        fmt = f"{SUBTYPES[r.output_subtype][0]} {format_samplerate(r.fmt.samplerate)} {r.fmt.channels}ch"
        print(f"{name:34} {fmt:26} {_lufs(r.before.lufs):>7} {r.gain_db:+6.1f} {_lufs(r.after.lufs):>8} "
              f"{r.before.true_peak_db:6.1f} {r.after.true_peak_db:6.1f} {r.limiter.max_reduction_db:5.1f}  "
              f"{'yes' if r.clipping.clipped else 'no'}")
    print()
    for r in results:
        print(f"{Path(r.path).name}: {r.description.headline}")
        for line in r.description.warnings:
            print(f"   ! {line}")
    print()
    print(batch_summary(results, settings.target_lufs, args.dry_run), f"({elapsed:.1f} s)")
    if not args.dry_run:
        for folder in output_folders(jobs):
            print(f"Output: {folder}")
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
