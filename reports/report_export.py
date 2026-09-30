"""Mastering reports: one row per track with the before/after measurements and
the plain-language summary, as CSV (spreadsheet) or PDF (printable)."""

from __future__ import annotations

import csv
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from audio.analysis import FLOOR_DB, SUBTYPES, ClippingReport, Measurements, SourceFormat
from audio.batch import TrackResult
from audio.processing import MasteringSettings
from audio.summary import clipping_warning
from ui.theme import APP_NAME, APP_VERSION, REPORT_COLORS, REPORT_FONT_FILES

logging.getLogger("fontTools").setLevel(logging.ERROR)   # font subsetting chatter


@dataclass
class ReportEntry:
    number: int
    title: str
    path: str
    fmt: SourceFormat | None
    before: Measurements | None
    clipping: ClippingReport | None
    result: TrackResult | None
    error: str = ""


COLUMNS = (
    "Track", "Title", "Source file", "Format", "Output format",
    "Loudness before (LUFS)", "Loudness after (LUFS)", "Gain (dB)",
    "True peak before (dBTP)", "True peak after (dBTP)", "Limiting (dB)",
    "Clipping in source", "Tone matching", "Result", "Details", "Output file",
)


def _num(value: float | None, digits: int = 1, signed: bool = False) -> str:
    if value is None or not math.isfinite(value) or value <= FLOOR_DB:
        return ""
    return f"{value:+.{digits}f}" if signed else f"{value:.{digits}f}"


def _row(entry: ReportEntry) -> dict[str, str]:
    r = entry.result if entry.result is not None and entry.result.ok else None
    fmt = entry.fmt
    row = {
        "Track": str(entry.number),
        "Title": entry.title,
        "Source file": entry.path,
        "Format": fmt.summary if fmt else "",
        "Output format": (SUBTYPES[r.output_subtype][0] + f" \u00b7 {fmt.samplerate_label}") if r and fmt else "",
        "Loudness before (LUFS)": _num(entry.before.lufs) if entry.before else "",
        "Loudness after (LUFS)": _num(r.after.lufs) if r else "",
        "Gain (dB)": _num(r.gain_db, signed=True) if r else "",
        "True peak before (dBTP)": _num(entry.before.true_peak_db) if entry.before else "",
        "True peak after (dBTP)": _num(r.after.true_peak_db) if r else "",
        "Limiting (dB)": _num(r.limiter.max_reduction_db) if r else "",
        "Clipping in source": ("Yes" if entry.clipping.clipped else "No") if entry.clipping else "",
        "Tone matching": ", ".join(b.description for b in r.eq_bands) if r and r.eq_bands else "",
        "Result": "",
        "Details": "",
        "Output file": (r.output_path or "Preview only, not saved") if r else "",
    }
    if entry.error:
        row["Result"] = "Could not be processed"
        row["Details"] = entry.error
    elif entry.result is not None and entry.result.description is not None:
        d = entry.result.description
        row["Result"] = d.headline
        row["Details"] = " ".join(d.details + d.warnings)
    else:
        row["Result"] = "Not mastered yet"
        warning = clipping_warning(entry.clipping, fmt) if entry.clipping and fmt else None
        row["Details"] = warning or ""
    return row


def settings_lines(settings: MasteringSettings) -> list[str]:
    output = "each file keeps its own bit depth and sample rate" if not settings.output_subtype else \
        f"all files converted to {SUBTYPES[settings.output_subtype][0]} (dithered when reduced), sample rates kept"
    lines = [
        f"Loudness target {settings.target_lufs:g} LUFS, true-peak ceiling {settings.ceiling_dbtp:g} dBTP",
        f"Tone matching: {'on, reference ' + Path(settings.eq_reference).stem + f', strength {settings.eq_strength:.0%}' if settings.eq_match and settings.eq_reference else 'off'}",
        f"Track spacing: {f'{settings.gap_s:g} s between tracks' if settings.even_spacing else 'left as is'}",
        f"Output: {output}",
    ]
    return lines


def export_csv(path: str | Path, entries: list[ReportEntry], settings: MasteringSettings) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # BOM so Excel reads UTF-8
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for entry in entries:
            writer.writerow(_row(entry))


def _rgb(name: str) -> tuple[int, int, int]:
    value = REPORT_COLORS[name].lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class _Report(FPDF):
    def __init__(self) -> None:
        super().__init__(orientation="L", unit="mm", format="A4")
        self.set_margins(14, 14, 14)
        self.set_auto_page_break(True, margin=14)
        fonts = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
        files = {style: fonts / name for style, name in REPORT_FONT_FILES.items()}
        if all(p.exists() for p in files.values()):
            for style, p in files.items():
                self.add_font("Report", style, str(p))
            self._family = "Report"
            self._unicode = True
        else:
            self._family = "Helvetica"
            self._unicode = False

    def text_safe(self, text: str) -> str:
        if self._unicode:
            return text
        replacements = {"\u2014": "-", "\u2013": "-", "\u2192": "->", "\u2019": "'", "\u201c": '"', "\u201d": '"',
                        "\u2026": "...", "\u203a": ">"}
        for old, new in replacements.items():
            text = text.replace(old, new)
        return text.encode("latin-1", "replace").decode("latin-1")

    def use_font(self, size: float, bold: bool = False, color: str = "ink") -> None:
        self.set_font(self._family, "B" if bold else "", size)
        self.set_text_color(*_rgb(color))

    def footer(self) -> None:
        self.set_y(-10)
        self.use_font(8, color="faint")
        self.cell(0, 5, self.text_safe(f"{APP_NAME} {APP_VERSION} \u00b7 page {self.page_no()}"), align="R")


STALE_NOTE = ("Settings were changed after these results were produced. The measurements below match the "
              "settings listed here; master the album again to apply the new ones.")


def export_pdf(path: str | Path, entries: list[ReportEntry], settings: MasteringSettings, summary: str = "",
               stale: bool = False) -> None:
    """`settings` should be the ones the results were produced with."""
    pdf = _Report()
    t = pdf.text_safe
    pdf.add_page()
    pdf.use_font(20, bold=True)
    title = "Mastering Report" + (f": {settings.album}" if settings.album else "")
    pdf.cell(0, 10, t(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.use_font(9, color="muted")
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    artist = f"{settings.album_artist} \u00b7 " if settings.album_artist else ""
    pdf.cell(0, 5, t(f"{artist}{len(entries)} tracks \u00b7 generated {stamp}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)
    if summary:
        pdf.use_font(11, bold=True, color="accent")
        pdf.multi_cell(0, 6, t(summary), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.ln(1)
    pdf.use_font(9, color="muted")
    for line in settings_lines(settings):
        pdf.cell(0, 5, t(line), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    if stale:
        pdf.ln(1)
        pdf.use_font(9, bold=True, color="warn")
        pdf.multi_cell(0, 5, t(STALE_NOTE), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)

    headers = ("#", "Title", "Format", "Before", "After", "Gain", "Peak before", "Peak after", "Clipping", "Result")
    widths = (8, 48, 50, 18, 18, 16, 22, 20, 16, 53)
    pdf.set_draw_color(*_rgb("rule"))
    pdf.set_fill_color(*_rgb("band"))
    pdf.use_font(8, bold=True, color="muted")
    for header, w in zip(headers, widths):
        pdf.cell(w, 8, t(header), border="B", fill=True, align="R" if header in ("#", "Before", "After", "Gain", "Peak before", "Peak after") else "L")
    pdf.ln(8)
    for entry in entries:
        row = _row(entry)
        after = row["Loudness after (LUFS)"]
        cells = (row["Track"], row["Title"], row["Format"], row["Loudness before (LUFS)"], after,
                 row["Gain (dB)"], row["True peak before (dBTP)"], row["True peak after (dBTP)"],
                 row["Clipping in source"], row["Result"])
        colors = ["ink", "ink", "muted", "muted", "ink", "muted", "muted", "muted",
                  "bad" if row["Clipping in source"] == "Yes" else "muted", "ink"]
        if after and abs(float(after) - settings.target_lufs) > 1.0:
            colors[4] = "warn"
        if pdf.get_y() > pdf.h - 30:
            pdf.add_page()
        for text, w, color, header in zip(cells, widths, colors, headers):
            pdf.use_font(8.5, bold=header == "After", color=color)
            value = t(text)
            while value and pdf.get_string_width(value) > w - 2:
                value = value[:-2] + "\u2026" if pdf._unicode else value[:-4] + "..."
            pdf.cell(w, 8, value, border="B",
                     align="R" if header in ("#", "Before", "After", "Gain", "Peak before", "Peak after") else "L")
        pdf.ln(8)

    pdf.ln(6)
    pdf.use_font(12, bold=True)
    pdf.cell(0, 8, t("What changed, track by track"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    for entry in entries:
        row = _row(entry)
        if pdf.get_y() > pdf.h - 36:
            pdf.add_page()
        pdf.ln(2)
        pdf.use_font(10, bold=True)
        pdf.cell(0, 6, t(f"{entry.number}. {entry.title}"), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.use_font(9, color="ink")
        pdf.multi_cell(0, 5, t(row["Result"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if row["Details"]:
            pdf.use_font(8.5, color="muted")
            pdf.multi_cell(0, 4.6, t(row["Details"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        if row["Output file"]:
            pdf.use_font(8, color="faint")
            pdf.multi_cell(0, 4.4, t(row["Output file"]), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.output(str(path))
