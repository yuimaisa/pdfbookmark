"""Adaptive OCR planning; heading matching remains in matching.py."""

from __future__ import annotations

from dataclasses import dataclass, field

from pdfbookmark.matching import MatchReport, match_entries
from pdfbookmark.models import PageReference, TocEntry
from pdfbookmark.pdf import PdfIndex


@dataclass
class OcrPlan:
    mode: str = "fast"
    stage: int = 0
    pending: list[int] = field(default_factory=list)
    failed: list[int] = field(default_factory=list)
    completed: int = 0
    cache_hits: int = 0
    active: bool = True


def plan_pages(
    index: PdfIndex,
    entries: list[TocEntry],
    toc_pages: set[int],
    stage: int,
    reference: PageReference | None = None,
) -> tuple[list[int], MatchReport]:
    report = match_entries(index, entries, toc_pages, page_reference=reference)
    count = index.page_count
    if stage == 0:
        # Dense opening samples plus middle/end samples find front matter and offsets.
        pages = set(range(1, min(count, max(toc_pages, default=1) + 24) + 1))
        pages.update(1 + round(i * (count - 1) / 12) for i in range(13))
    elif stage < 3:
        pages = set()
        radius = 2 if stage == 1 else 12
        for entry in report.entries:
            if entry.manual or entry.status in {"Verified", "Probable"}:
                continue
            predicted, _ = report.page_map.predict(entry.printed_page)
            centers = [c.pdf_page for c in entry.candidates]
            if predicted is not None:
                centers.append(predicted)
            for center in centers:
                pages.update(range(max(1, center - radius), min(count, center + radius) + 1))
    else:
        pages = set(range(1, count + 1))
    return sorted(
        n
        for n in (pages - toc_pages if stage < 3 else pages)
        if index.pages[n - 1].source != "ocr" and not index.pages[n - 1].normalized
    ), report
