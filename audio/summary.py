"""Plain-language descriptions of analysis and mastering results.

Written for people with no mastering background. Used by the UI (track list,
"What changed" card) and by the exported reports.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .analysis import ClippingReport, SourceFormat, SUBTYPES

if TYPE_CHECKING:
    from .batch import TrackResult

TURNED_DOWN_EACH_DB = 0.5      # every track lowered by more than this (smaller counts as "no change")
TURNED_DOWN_AVERAGE_DB = 1.0   # ...and by at least this much on average -> explain why
LOUDER_PRESET_LUFS = -11.0     # target of the "Rock, Pop & Electronic" built-in preset


@dataclass
class ResultDescription:
    headline: str
    details: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def clipping_warning(clipping: ClippingReport, fmt: SourceFormat) -> str | None:
    if clipping.clipped:
        places = "1 place" if clipping.events == 1 else f"{clipping.events:,} places"
        return (
            f"The original file is already clipped in {places}: its loudest peaks were flattened "
            "before it reached this app, which causes harsh distortion. Mastering can't undo "
            "that damage, but it won't make it worse."
        )
    if clipping.over_full_scale and fmt.is_float:
        return (
            "Some peaks in the original go above the digital maximum. Because this is a float "
            "file they aren't damaged yet, and the limiter brings them back to a safe level."
        )
    return None


def gain_headline(gain_db: float, input_lufs: float, limiter_db: float = 0.0) -> str:
    if math.isnan(input_lufs):
        return "Too short to measure its volume, so only its peaks were checked."
    if not math.isfinite(input_lufs):
        return "This track is silent or nearly silent, so its volume was left as is."
    if abs(gain_db) < 0.5:
        if limiter_db >= 1.0:
            return "Already at the right volume; only its loudest peaks were brought down to a safe level."
        return "Already at the right volume, no change needed."
    size = abs(gain_db)
    direction = "raised" if gain_db > 0 else "lowered"
    if size < 3:
        return f"Volume {direction} slightly to match the rest of your album."
    if size < 8:
        return f"Volume {direction} to match the rest of your album."
    if gain_db > 0:
        return "Volume raised a lot: this track was much quieter than the streaming standard."
    return "Volume lowered a lot: this track was much louder than the streaming standard."


def describe_result(result: "TrackResult", target_lufs: float, ceiling_dbtp: float) -> ResultDescription:
    if result.error:
        return ResultDescription("This file couldn't be processed.", [], [result.error])

    after = result.after
    desc = ResultDescription(gain_headline(result.gain_db, result.input_lufs, result.limiter.max_reduction_db))

    if math.isfinite(after.lufs):
        if target_lufs - after.lufs > 0.5:
            desc.details.append(
                f"Reached {after.lufs:.1f} LUFS instead of {target_lufs:.1f}: pushing further "
                "would have squashed its dynamics."
            )
        else:
            desc.details.append(f"Now plays at {after.lufs:.1f} LUFS, right at the album target.")

    gr = result.limiter.max_reduction_db
    if gr >= 6.0:
        desc.warnings.append(
            f"This track needed strong limiting (up to {gr:.1f} dB) to reach the target. It may "
            "sound a little less punchy. A lower target, such as -16 LUFS, keeps more punch."
        )
    elif gr >= 2.0:
        desc.details.append(f"The limiter held back some peaks (up to {gr:.1f} dB) to prevent distortion.")
    elif gr >= 0.1:
        desc.details.append(f"The loudest peaks were gently held back to stay below {ceiling_dbtp:.1f} dBTP.")
    else:
        desc.details.append("Peaks were already safe, so the limiter didn't need to step in.")

    if result.is_eq_reference:
        desc.details.append("This is the tone reference for the album, so its tone was kept as is.")
    elif result.eq_bands:
        moves = ", ".join(b.description for b in result.eq_bands)
        reference = Path(result.eq_reference or "").stem or "the reference track"
        desc.details.append(f"Tone gently adjusted to sound closer to “{reference}” ({moves}).")

    silence = result.silence
    if silence is not None:
        if not silence.lead_untouched and abs(silence.lead_after_s - silence.lead_before_s) >= 0.02:
            verb = "trimmed" if silence.lead_after_s < silence.lead_before_s else "extended"
            desc.details.append(
                f"Silence at the start {verb} from {silence.lead_before_s:.1f} s to {silence.lead_after_s:.1f} s."
            )
        if silence.tail_untouched:
            desc.details.append(
                "It ends without any silence (it may flow into the next track), so its ending was left as is."
            )
        elif abs(silence.tail_after_s - silence.tail_before_s) >= 0.02:
            verb = "trimmed" if silence.tail_after_s < silence.tail_before_s else "extended"
            desc.details.append(
                f"Silence at the end {verb} from {silence.tail_before_s:.1f} s to {silence.tail_after_s:.1f} s "
                "so the gap to the next track matches the rest of the album."
            )

    out_label = SUBTYPES[result.output_subtype][0]
    if result.output_subtype == result.fmt.subtype:
        desc.details.append(f"Kept its original format ({result.fmt.summary}).")
    elif result.dithered:
        desc.details.append(f"Converted to {out_label}, with dithering to keep quiet passages clean.")
    else:
        desc.details.append(f"Converted to {out_label}.")

    warning = clipping_warning(result.clipping, result.fmt)
    if warning:
        desc.warnings.append(warning)
    return desc


def batch_summary(results: list["TrackResult"], target_lufs: float, dry_run: bool) -> str:
    ok = [r for r in results if not r.error]
    failed = len(results) - len(ok)
    if not ok:
        return "No tracks could be processed."
    finite = [r.after.lufs for r in ok if math.isfinite(r.after.lufs)]
    verb = "previewed" if dry_run else "mastered"
    noun = "track" if len(ok) == 1 else "tracks"
    text = f"{len(ok)} {noun} {verb}"
    if finite:
        spread = max(abs(v - target_lufs) for v in finite)
        text += f", all within {max(spread, 0.05):.1f} LU of the target" if spread < 1.0 else ""
    if failed:
        text += f" · {failed} could not be processed"
    return text + "." + turned_down_hint(ok, target_lufs)


def turned_down_hint(results: list["TrackResult"], target_lufs: float) -> str:
    """Explain an album whose every track was turned down: the mixes were already louder than
    the target, so "mastered" sounds quieter than the originals, which surprises people."""
    gains = [r.gain_db for r in results if math.isfinite(r.input_lufs)]
    if not gains or max(gains) >= -TURNED_DOWN_EACH_DB or sum(gains) / len(gains) > -TURNED_DOWN_AVERAGE_DB:
        return ""
    text = f" Your mixes were already louder than {target_lufs:g} LUFS, so every track was turned down."
    if target_lufs < LOUDER_PRESET_LUFS - 0.5:
        text += (
            " That suits streaming services, which turn loud songs down anyway. For downloads, CDs or "
            f"Bandcamp, load the {LOUDER_PRESET_LUFS:g} LUFS preset (Edit › Presets) to keep them loud."
        )
    return text
