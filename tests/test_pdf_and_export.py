import json
from hashlib import sha256

import pymupdf
import pytest

from pdfbookmark.demo import demo_entries, demo_pdf
from pdfbookmark.export import export_pdf, export_project, import_project
from pdfbookmark.matching import match_entries
from pdfbookmark.models import Bookmark, PageReference
from pdfbookmark.pdf import extract_index, render_page


def bookmark(title="Chapter", level=1, page=11, status="Verified"):
    return Bookmark(level=level, title=title, printed_page="1", pdf_page=page, status=status)


def test_pdf_metadata_text_positions_and_preview(make_pdf):
    data = make_pdf({11: ["Chapter"]}, existing=[[1, "Old", 4]])
    index = extract_index(data)
    assert index.page_count == 60 and index.text_page_count == 60
    assert index.existing_toc == ((1, "Old", 4),)
    line = index.pages[10].lines[0]
    assert line.bbox[1] > 0 and line.size > 0 and line.normalized
    assert render_page(data, 11).startswith(b"\x89PNG")
    with pytest.raises(ValueError, match="範囲外"):
        render_page(data, 0)


def test_invalid_encrypted_and_image_only_pdf():
    with pytest.raises(ValueError, match="開けません"):
        extract_index(b"not a PDF")
    with pymupdf.open() as doc:
        doc.new_page()
        blank = doc.tobytes()
        encrypted = doc.tobytes(
            encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="reader"
        )
    assert extract_index(blank).text_page_count == 0
    with pytest.raises(ValueError, match="暗号化"):
        extract_index(encrypted)


def test_bookmarks_and_page_streams_unchanged(make_pdf):
    data = make_pdf({11: ["Chapter"], 12: ["Section"]})
    digest = sha256(data).hexdigest()
    entries = [bookmark(), bookmark("Section", 2, 12)]
    output = export_pdf(data, entries)
    assert sha256(data).hexdigest() == digest
    with (
        pymupdf.open(stream=data, filetype="pdf") as original,
        pymupdf.open(stream=output, filetype="pdf") as result,
    ):
        assert result.get_toc() == [[1, "Chapter", 11], [2, "Section", 12]]
        assert result.page_count == original.page_count
        for a, b in zip(original, result, strict=True):
            assert a.read_contents() == b.read_contents()
            assert a.get_text() == b.get_text()
            assert a.get_images() == b.get_images()
        target = result.get_toc(simple=False)[1][3]
        assert target["page"] == 11  # PDF API internals alone are zero-based.


@pytest.mark.parametrize(
    "entries",
    [
        [],
        [bookmark(level=2)],
        [bookmark(), bookmark(level=3)],
        [bookmark(page=None, status="Unresolved")],
        [bookmark(page=1000)],
    ],
)
def test_export_rejects_invalid_outlines(make_pdf, entries):
    with pytest.raises(ValueError):
        export_pdf(make_pdf(), entries)


def test_existing_outline_requires_explicit_confirmation(make_pdf):
    data = make_pdf(existing=[[1, "Existing", 2]])
    with pytest.raises(ValueError, match="確認"):
        export_pdf(data, [bookmark()])
    output = export_pdf(data, [bookmark()], overwrite_existing=True)
    assert extract_index(output).existing_toc == ((1, "Chapter", 11),)
    assert extract_index(data).existing_toc == ((1, "Existing", 2),)


def test_project_roundtrip_and_pdf_binding(make_pdf):
    data = make_pdf()
    entries = [bookmark()]
    text = export_project(data, 60, [1, 2], entries)
    restored = import_project(text, data, 60)
    assert restored.entries == entries and restored.toc_pages == [1, 2]
    with pytest.raises(ValueError, match="別のPDF"):
        import_project(text, make_pdf({11: ["Other book"]}), 60)


def test_500_page_complete_deterministic_pipeline():
    data = demo_pdf()
    index = extract_index(data)
    report = match_entries(index, demo_entries().entries, {2, 3})
    assert index.page_count == 500
    assert [e.pdf_page for e in report.entries] == [4, 6, 13, 16, 30, 213, 223, 253, 413]
    assert all(e.status == "Verified" for e in report.entries)
    output = export_pdf(data, report.entries)
    assert len(extract_index(output).existing_toc) == 9


def test_project_preserves_reference_and_reads_older_json(make_pdf):
    data = make_pdf()
    reference = PageReference(printed_page="1", pdf_page=12)
    text = export_project(data, 60, [1, 2], [bookmark()], page_reference=reference)
    assert import_project(text, data, 60).page_reference == reference
    older = json.loads(text)
    older.pop("page_reference")
    assert import_project(json.dumps(older), data, 60).page_reference is None


@pytest.mark.parametrize("page", [1, 61])
def test_project_rejects_invalid_reference_location(make_pdf, page):
    with pytest.raises(ValueError, match="基準のPDFページ"):
        export_project(
            make_pdf(),
            60,
            [1, 2],
            [bookmark()],
            page_reference=PageReference(printed_page="1", pdf_page=page),
        )
