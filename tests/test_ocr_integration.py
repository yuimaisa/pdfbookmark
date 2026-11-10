"""Explicit real local model tests, outside ordinary offline CI."""

import os

import pytest

from pdfbookmark.normalization import normalize
from pdfbookmark.ocr import OcrCache, OcrSettings, PaddleEngine, TesseractEngine, ocr_pages
from pdfbookmark.pdf import extract_index

pytestmark = [
    pytest.mark.ocr_integration,
    pytest.mark.skipif(os.getenv("RUN_OCR_TESTS") != "1", reason="Requires RUN_OCR_TESTS=1"),
]


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Linear algebra", "Linear algebra"),
        ("第1章 基礎理論", "基礎理論"),
        ("線形代数 Linear algebra", "線形代数"),
    ],
)
def test_real_local_ocr(make_image_pdf, tmp_path, text, expected):
    data = make_image_pdf({1: text}, pages=1)
    index = extract_index(data)
    engine = PaddleEngine() if os.getenv("OCR_TEST_ENGINE") == "paddle" else TesseractEngine()
    result = ocr_pages(
        data, index, engine, OcrSettings(dpi=240), OcrCache(tmp_path / "real.sqlite"), [1]
    )
    assert not result.failed, "OCR backend failed to load or recognize the test page"
    assert normalize(expected) in result.index.pages[0].normalized
    assert result.index.pages[0].lines
    assert all(0 <= line.confidence <= 1 for line in result.index.pages[0].lines)


@pytest.mark.skipif(
    os.getenv("RUN_OCR_LARGE_TESTS") != "1",
    reason="Requires RUN_OCR_LARGE_TESTS=1 (500 real page recognitions)",
)
def test_real_500_page_image_book_and_cache(make_image_pdf, tmp_path):
    import pymupdf

    from pdfbookmark.export import export_pdf
    from pdfbookmark.matching import match_entries
    from pdfbookmark.models import TocEntry

    headings = {13: "Foundations", 213: "Methods", 413: "Applications"}
    labels = {13: "1", 213: "201", 413: "401"}
    data = make_image_pdf(headings, pages=500, printed=labels)
    index = extract_index(data)
    engine = TesseractEngine()
    cache = OcrCache(tmp_path / "large-real.sqlite")
    result = ocr_pages(data, index, engine, OcrSettings(), cache, range(1, 501))
    assert not result.failed and len(result.completed) == 500
    entries = [
        TocEntry(level=1, title=title, printed_page=labels[n]) for n, title in headings.items()
    ]
    report = match_entries(result.index, entries, {1, 2})
    assert [e.pdf_page for e in report.entries] == [13, 213, 413]
    assert all(e.status != "Unresolved" for e in report.entries)
    output = export_pdf(data, report.entries)
    with pymupdf.open(stream=output, filetype="pdf") as checked:
        assert checked.page_count == 500 and len(checked.get_toc()) == 3
        assert all(not page.get_text().strip() for page in checked)
    reused = ocr_pages(data, index, TesseractEngine(), OcrSettings(), cache, range(1, 501))
    assert reused.cache_hits == 500 and not reused.failed
