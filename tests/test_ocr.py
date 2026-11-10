from dataclasses import replace

import pymupdf
import pytest

from pdfbookmark.export import export_pdf
from pdfbookmark.matching import match_entries, page_number_anchors
from pdfbookmark.models import TocEntry
from pdfbookmark.ocr import OcrCache, OcrError, OcrLine, OcrSettings, adapt_page, ocr_pages
from pdfbookmark.ocr_workflow import plan_pages
from pdfbookmark.pdf import PdfIndex, extract_index


class FakeEngine:
    name = "fake"
    model_version = "jpn-eng-v1"

    def __init__(self, lines=None, failures=()):
        self.calls = 0
        self.failures = set(failures)
        self.lines = lines or [OcrLine("第1章 Linear algebra", (100, 200, 800, 260), 0.98, 0)]

    def recognize(self, png):
        assert png.startswith(b"\x89PNG")
        self.calls += 1
        if self.calls in self.failures:
            raise OcrError("private backend details")
        return self.lines


def entry(title, printed=None):
    return TocEntry(level=1, title=title, printed_page=printed)


def page(n, title="", label=None, confidence=0.99):
    lines = []
    if title:
        lines.append(OcrLine(title, (50, 100, 500, 140), confidence, 0))
    if label:
        lines.append(OcrLine(label, (290, 800, 310, 820), 0.99, 1))
    return adapt_page(n, 595, 842, 595, 842, lines)


def index_with(pages):
    return PdfIndex("a" * 64, tuple(pages), ())


def test_pdf_coordinate_conversion_confidence_order():
    converted = adapt_page(
        2,
        600,
        800,
        1200,
        1600,
        [
            OcrLine("English", (100, 200, 900, 280), 0.94, 1),
            OcrLine("日本語", (100, 50, 500, 100), 0.91, 0),
        ],
    )
    assert converted.number == 2 and converted.source == "ocr"
    assert converted.lines[1].bbox == (50, 100, 450, 140)
    assert converted.lines[1].confidence == 0.94
    assert [line.order for line in converted.lines] == [0, 1]
    assert converted.lines[0].size == 0  # Never masquerades as an actual font size.
    assert converted.normalized == "日本語english"


def test_full_ocr_cache_restart_failure_retry_and_cancel(make_image_pdf, tmp_path):
    data = make_image_pdf(pages=6)
    index = extract_index(data)
    assert index.text_page_count == 0
    cache = OcrCache(tmp_path / "cache.sqlite")
    engine = FakeEngine(failures={2})
    progress = []
    first = ocr_pages(
        data,
        index,
        engine,
        OcrSettings(),
        cache,
        range(1, 7),
        limit=3,
        progress=lambda *values: progress.append(values),
    )
    assert first.completed == [1, 3] and first.failed == [2]
    assert progress[-1] == (2, 1, 0)
    # A new app process starts with the original PDF, reuses committed successes,
    # retries the failed page and processes only the remaining pages.
    fresh = FakeEngine()
    resumed = ocr_pages(
        data, extract_index(data), fresh, OcrSettings(), OcrCache(cache.path), range(1, 7)
    )
    assert fresh.calls == 4 and resumed.cache_hits == 2
    assert resumed.completed == list(range(1, 7)) and not resumed.failed
    stop = ocr_pages(data, index, fresh, OcrSettings(), cache, range(1, 7), cancelled=lambda: True)
    assert stop.completed == []
    # Failure-only retries do not touch other pages.
    retried = ocr_pages(data, first.index, engine, OcrSettings(), cache, first.failed)
    assert retried.completed == [2] and retried.cache_hits == 1


@pytest.mark.parametrize("change", ["pdf", "page", "model", "engine", "dpi", "preprocessing"])
def test_cache_keys_isolate_document_and_settings(change):
    engine, settings = FakeEngine(), OcrSettings()
    digest, number = "a" * 64, 1
    before = OcrCache.key(digest, number, engine, settings)
    if change == "pdf":
        digest = "b" * 64
    elif change == "page":
        number = 2
    elif change == "model":
        engine.model_version = "v2"
    elif change == "engine":
        engine.name = "other"
    elif change == "dpi":
        settings = replace(settings, dpi=300)
    else:
        settings = replace(settings, preprocessing="grayscale")
    assert OcrCache.key(digest, number, engine, settings) != before


def test_rotated_crop_coordinates_and_bounded_raster(make_image_pdf, tmp_path):
    data = make_image_pdf(pages=1, rotation=90)
    index = extract_index(data)
    engine = FakeEngine()
    result = ocr_pages(
        data,
        index,
        engine,
        OcrSettings(max_pixels=1_000_000),
        OcrCache(tmp_path / "ocr.sqlite"),
        [1],
    )
    assert result.index.pages[0].width == 842
    assert result.index.pages[0].height == 595
    for line in result.index.pages[0].lines:
        assert 0 <= line.bbox[0] <= line.bbox[2] <= 842
        assert 0 <= line.bbox[1] <= line.bbox[3] <= 595


def test_page_numbers_alone_never_verify_and_offset_changes():
    index = index_with(
        [
            page(1),
            page(2),
            page(3, label="1"),
            page(4, label="2"),
            page(5),
            page(6, label="3"),
            page(7, label="4"),
        ]
    )
    report = match_entries(index, [entry("Absent", "2"), entry("Missing", "4")], {1})
    assert [e.pdf_page for e in report.entries] == [4, 7]
    assert all(e.status == "Estimated" for e in report.entries)
    assert [s.offset for s in report.page_map.segments] == [2, 3]
    assert report.warnings


def test_ocr_heading_requires_number_agreement_and_confidence():
    index = index_with(
        [
            page(1),
            page(2, "Foundations", "1"),
            page(3, "Methods", "2"),
            page(4, "Spectra1 decomposition", "3"),
            page(5, "Low confidence", "4", confidence=0.6),
        ]
    )
    report = match_entries(
        index,
        [
            entry("Foundations", "1"),
            entry("Methods", "2"),
            entry("Spectral decomposition", "3"),
            entry("Low confidence", "4"),
        ],
        {1},
    )
    assert [e.status for e in report.entries[:3]] == ["Verified", "Verified", "Probable"]
    assert report.entries[3].status != "Verified"
    unsupported = match_entries(
        index_with([page(1), page(2, "Unique heading")]), [entry("Unique heading")], {1}
    ).entries[0]
    assert unsupported.status != "Verified"


def test_ocr_duplicate_roman_appendix_and_top_labels():
    top = adapt_page(2, 595, 842, 595, 842, [OcrLine("i", (290, 20, 310, 40), 0.99, 0)])
    index = index_with(
        [
            page(1),
            top,
            page(3, label="ii"),
            page(4, "Applications", "A-1"),
            page(5, "Applications", "A-2"),
        ]
    )
    anchors = page_number_anchors(index, {1})
    assert {a.domain for a in anchors} == {"roman", "appendix:A"}
    report = match_entries(
        index, [entry("Missing preface", "ii"), entry("Applications", "A-2")], {1}
    )
    assert report.entries[0].pdf_page == 3 and report.entries[0].status == "Estimated"
    assert report.entries[1].pdf_page == 5 and report.entries[1].status == "Probable"


def test_fast_mode_samples_targets_expands_then_falls_back(make_image_pdf):
    index = extract_index(make_image_pdf(pages=500))
    entries = [entry("Foundations", "200")]
    samples, _ = plan_pages(index, entries, {1, 2}, 0)
    assert 250 < max(samples) and len(samples) < 50
    pages = list(index.pages)
    pages[99] = page(100, label="90")
    pages[299] = page(300, label="290")
    partial = replace(index, pages=tuple(pages))
    nearby, _ = plan_pages(partial, entries, {1, 2}, 1)
    expanded, _ = plan_pages(partial, entries, {1, 2}, 2)
    full, _ = plan_pages(partial, entries, {1, 2}, 3)
    assert set(nearby) == set(range(208, 213))
    assert set(nearby) < set(expanded) < set(full)


def test_500_page_image_pdf_ocr_export_preserves_images_and_destinations(make_image_pdf, tmp_path):
    data = make_image_pdf({13: "Foundations", 213: "Methods", 413: "Applications"}, pages=500)
    index = extract_index(data)
    # Real raster fixture, deterministic backend; large models/APIs are not CI dependencies.
    engine = FakeEngine()
    cached = OcrCache(tmp_path / "large.sqlite")
    for n, title, label in [
        (13, "Foundations", "1"),
        (213, "Methods", "201"),
        (413, "Applications", "401"),
    ]:
        engine.lines = [
            OcrLine(title, (100, 200, 900, 260), 0.99, 0),
            OcrLine(label, (580, 1600, 620, 1640), 0.99, 1),
        ]
        index = ocr_pages(data, index, engine, OcrSettings(dpi=144), cached, [n]).index
    report = match_entries(
        index,
        [entry("Foundations", "1"), entry("Methods", "201"), entry("Applications", "401")],
        {1, 2},
    )
    assert [e.pdf_page for e in report.entries] == [13, 213, 413]
    result = export_pdf(data, report.entries)
    with (
        pymupdf.open(stream=data, filetype="pdf") as before,
        pymupdf.open(stream=result, filetype="pdf") as after,
    ):
        assert after.page_count == 500
        assert after.get_toc() == [[1, e.title, e.pdf_page] for e in report.entries]
        assert all(not p.get_text().strip() for p in after)
        for number in (0, 12, 212, 412, 499):
            assert before[number].get_pixmap().samples == after[number].get_pixmap().samples
            old_xref = before[number].get_images()[0][0]
            new_xref = after[number].get_images()[0][0]
            assert before.xref_stream_raw(old_xref) == after.xref_stream_raw(new_xref)


def test_paddle_v6_adapter_configuration_coordinates_and_model_fingerprint(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace

    import numpy as np

    import pdfbookmark.ocr as ocr

    root = tmp_path / "paddle"
    for model in ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"):
        directory = root / "official_models" / model
        directory.mkdir(parents=True)
        (directory / "inference.pdiparams").write_bytes(b"test-weight-v1")
    monkeypatch.setenv("PADDLE_PDX_CACHE_HOME", str(root))
    monkeypatch.setattr(ocr, "version", lambda package: "test-package-version")
    monkeypatch.setitem(sys.modules, "paddle", SimpleNamespace(is_compiled_with_cuda=lambda: False))
    configurations = []

    class Pipeline:
        def __init__(self, **options):
            configurations.append(options)

        def predict(self, array):
            assert array.shape == (10, 10, 3)
            assert tuple(array[0, 0]) == (30, 20, 10)  # BGR, not RGB
            return [
                {
                    "rec_texts": ["日本語 English"],
                    "rec_scores": [0.97],
                    "rec_polys": [np.array([[1, 2], [9, 2], [9, 5], [1, 5]])],
                }
            ]

    monkeypatch.setitem(sys.modules, "paddleocr", SimpleNamespace(PaddleOCR=Pipeline))
    engine = ocr.PaddleEngine()
    pix = pymupdf.Pixmap(pymupdf.csRGB, (0, 0, 10, 10), 0)
    pix.set_rect(pix.irect, (10, 20, 30))
    lines = engine.recognize(pix.tobytes("png"))
    assert lines == [OcrLine("日本語 English", (1, 2, 9, 5), 0.97, 0)]
    assert configurations[0]["text_recognition_model_name"] == "PP-OCRv6_medium_rec"
    assert configurations[0]["text_detection_model_name"] == "PP-OCRv6_medium_det"
    assert not configurations[0]["use_doc_unwarping"]
    before = engine.model_version
    (root / "official_models/PP-OCRv6_medium_rec/inference.pdiparams").write_bytes(b"v2")
    assert ocr.PaddleEngine().model_version != before
    with pytest.raises(OcrError, match="CUDA"):
        ocr.PaddleEngine("gpu:0")


def test_corrupt_database_and_cache_write_errors_are_safe(make_image_pdf, tmp_path, monkeypatch):
    from pdfbookmark.ocr import OcrCacheError

    path = tmp_path / "broken.sqlite"
    path.write_bytes(b"PRIVATE BOOK TEXT - not a database")
    with pytest.raises(OcrCacheError) as error:
        OcrCache(path)
    assert "PRIVATE BOOK" not in str(error.value)
    cache = OcrCache(tmp_path / "valid.sqlite")

    def fail(*args):
        raise OcrCacheError("ディスク容量を確認してください")

    monkeypatch.setattr(cache, "put", fail)
    data = make_image_pdf(pages=1)
    # Storage failures stop the job instead of repeatedly OCRing every page.
    with pytest.raises(OcrCacheError):
        ocr_pages(data, extract_index(data), FakeEngine(), OcrSettings(), cache, [1])
