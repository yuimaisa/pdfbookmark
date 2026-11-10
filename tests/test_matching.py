import pytest

from pdfbookmark.matching import Anchor, PageMap, match_entries
from pdfbookmark.models import Bookmark, PageReference, TocEntry
from pdfbookmark.normalization import normalize, printed_number, split_heading
from pdfbookmark.pdf import extract_index, toc_text


def entry(title, printed=None, level=1):
    return TocEntry(title=title, printed_page=printed, level=level)


def test_simple_offset_and_missing_heading_is_estimated(make_pdf):
    data = make_pdf({11: ["1 Foundations"], 30: ["2 Analysis"]})
    report = match_entries(
        extract_index(data),
        [
            entry("1 Foundations", "1"),
            entry("An absent section", "10", 2),
            entry("2 Analysis", "20"),
        ],
        {1, 2},
    )
    assert [e.pdf_page for e in report.entries] == [11, 20, 30]
    assert [e.status for e in report.entries] == ["Verified", "Estimated", "Verified"]
    assert report.page_map.segments[0].offset == 10


def test_piecewise_offsets_and_uncertain_transition(make_pdf):
    index = extract_index(
        make_pdf({11: ["Alpha"], 20: ["Beta methods"], 41: ["Delta"], 51: ["Epsilon"]})
    )
    entries = [
        entry("Alpha", "1"),
        entry("Beta methods", "10"),
        entry("Unknown boundary", "20"),
        entry("Delta", "30"),
        entry("Absent later section", "35"),
        entry("Epsilon", "40"),
    ]
    report = match_entries(index, entries, {1, 2})
    assert [e.pdf_page for e in report.entries] == [11, 20, None, 41, 46, 51]
    assert [s.offset for s in report.page_map.segments] == [10, 11]
    assert report.warnings and report.entries[2].status == "Unresolved"


def test_unnumbered_chapter_leaf_matches_heading(make_pdf):
    # No dependency on an OCR'd footer or printed page number on the chapter leaf.
    index = extract_index(make_pdf({13: ["Chapter 1 Introduction"]}))
    report = match_entries(index, [entry("Chapter 1 Introduction", None)], {1, 2})
    assert report.entries[0].pdf_page == 13
    assert report.entries[0].status == "Verified"


def test_ambiguous_duplicate_resolved_by_number_map(make_pdf):
    index = extract_index(
        make_pdf({11: ["Foundations"], 20: ["Methods"], 24: ["Applications"], 45: ["Applications"]})
    )
    report = match_entries(
        index,
        [entry("Foundations", "1"), entry("Methods", "10"), entry("Applications", "14")],
        {1, 2},
    )
    assert report.entries[-1].pdf_page == 24
    assert report.entries[-1].status == "Probable"


def test_duplicate_without_map_stays_unresolved(make_pdf):
    index = extract_index(make_pdf({14: ["Applications"], 44: ["Applications"]}))
    result = match_entries(index, [entry("Applications")], {1, 2}).entries[0]
    assert result.pdf_page is None and result.status == "Unresolved"
    assert len(result.candidates) == 2


def test_unicode_punctuation_and_wrapped_heading(make_pdf):
    index = extract_index(
        make_pdf(
            {15: ["１．１　線形空間"], 26: [(110, "2 Spectral", 21), (135, "decomposition", 21)]}
        )
    )
    report = match_entries(
        index, [entry("1.1 線形 空間", "5"), entry("2 Spectral decomposition", "16")], {1}
    )
    assert [e.pdf_page for e in report.entries] == [15, 26]
    assert all(e.status == "Verified" for e in report.entries)
    assert report.entries[0].title == "1.1 線形 空間"


def test_fuzzy_ocr_needs_supporting_page_map(make_pdf):
    index = extract_index(
        make_pdf({11: ["Foundations"], 20: ["Methods"], 25: ["Spectra1 decomposition"]})
    )
    report = match_entries(
        index,
        [entry("Foundations", "1"), entry("Methods", "10"), entry("Spectral decomposition", "15")],
        {1, 2},
    )
    assert report.entries[-1].pdf_page == 25
    assert report.entries[-1].status == "Probable"


def test_roman_arabic_appendix_domains(make_pdf):
    index = extract_index(
        make_pdf(
            {
                4: ["Preface"],
                6: ["Notation"],
                11: ["Foundations"],
                20: ["Methods"],
                42: ["Appendix A Data"],
                46: ["Appendix A Tables"],
            }
        )
    )
    report = match_entries(
        index,
        [
            entry("Preface", "i"),
            entry("Notation", "iii"),
            entry("Foundations", "1"),
            entry("Methods", "10"),
            entry("Appendix A Data", "A-1"),
            entry("Appendix A Tables", "A-5"),
            entry("Missing appendix", "A-7"),
        ],
        {1, 2},
    )
    assert [e.pdf_page for e in report.entries] == [4, 6, 11, 20, 42, 46, 48]
    assert {s.domain: s.offset for s in report.page_map.segments} == {
        "roman": 3,
        "arabic": 10,
        "appendix:A": 41,
    }


def test_multipage_toc_is_excluded(make_pdf):
    index = extract_index(
        make_pdf({1: ["Foundations"], 2: ["Methods"], 11: ["Foundations"], 20: ["Methods"]})
    )
    text = toc_text(index, 1, 2)
    assert "PDFページ 1" in text and "PDFページ 2" in text
    report = match_entries(index, [entry("Foundations", "1"), entry("Methods", "10")], {1, 2})
    assert [e.pdf_page for e in report.entries] == [11, 20]


def test_running_headers_and_cross_references_are_not_verified(make_pdf):
    index = extract_index(
        make_pdf(
            {32: ["Spectral theory"]},
            running="Spectral theory",
            contents={5: ["See Spectral theory for details."]},
        )
    )
    result = match_entries(index, [entry("Spectral theory", "22")], {1, 2}).entries[0]
    assert result.pdf_page == 32 and result.status == "Verified"
    without_heading = extract_index(make_pdf(running="Spectral theory"))
    result = match_entries(without_heading, [entry("Spectral theory")], {1, 2}).entries[0]
    assert result.status == "Unresolved"


def test_midpage_heading_allowed(make_pdf):
    index = extract_index(make_pdf({18: [(440, "Local coordinates", 21)]}))
    assert match_entries(index, [entry("Local coordinates")], {1}).entries[0].pdf_page == 18


def test_missing_heading_without_anchors_unresolved(make_pdf):
    result = match_entries(
        extract_index(make_pdf()), [entry("Completely absent", "5")], {1}
    ).entries[0]
    assert result.status == "Unresolved" and result.pdf_page is None


def test_single_outlier_cannot_create_page_map():
    page_map = PageMap(
        [
            Anchor(0, "arabic", 1, 11),
            Anchor(1, "arabic", 5, 15),
            Anchor(2, "arabic", 10, 50),
            Anchor(3, "arabic", 20, 30),
        ]
    )
    assert page_map.predict("12")[0] == 22
    assert len(page_map.segments) == 1
    assert PageMap([Anchor(0, "arabic", 1, 11)]).predict("2")[0] is None


def test_same_printed_page_cannot_count_as_two_anchors():
    assert not PageMap([Anchor(0, "arabic", 1, 11), Anchor(1, "arabic", 1, 11)]).segments


def test_map_does_not_extrapolate_across_unsupported_conflicting_anchor():
    page_map = PageMap(
        [Anchor(0, "arabic", 1, 11), Anchor(1, "arabic", 30, 41), Anchor(2, "arabic", 40, 51)]
    )
    assert page_map.predict("20")[0] is None
    assert page_map.predict("35")[0] == 46


def test_unique_heading_beats_offset_after_large_insertion(make_pdf):
    index = extract_index(make_pdf({11: ["Foundations"], 20: ["Methods"], 45: ["Spectral theory"]}))
    report = match_entries(
        index,
        [entry("Foundations", "1"), entry("Methods", "10"), entry("Spectral theory", "25")],
        {1, 2},
    )
    assert report.entries[-1].pdf_page == 45
    assert report.entries[-1].status == "Verified"
    assert report.warnings


def test_conflicting_unnumbered_headings_require_review(make_pdf):
    index = extract_index(make_pdf({11: ["Foundations"], 20: ["Methods"]}))
    report = match_entries(index, [entry("Methods"), entry("Foundations")], {1, 2})
    assert all(e.status == "Unresolved" for e in report.entries)
    assert report.warnings


def test_manual_destination_survives_rematching(make_pdf):
    manual = Bookmark(
        title="User choice",
        level=1,
        printed_page=None,
        pdf_page=19,
        status="Probable",
        manual=True,
        confidence=0.85,
    )
    result = match_entries(extract_index(make_pdf()), [manual], {1, 2}).entries[0]
    assert result == manual and result is not manual


def test_normalization_and_number_domains():
    assert normalize("ＡＢＣ、　１２\n３") == "abc123"
    assert split_heading("第１章　研究背景") == ("第1章", "研究背景")
    assert split_heading("1.2.3  Overview")[1] == "overview"
    assert printed_number("xiv") == ("roman", 14)
    assert printed_number("Ａ−１") == ("appendix:A", 1)
    assert printed_number("12b") is None


def test_explicit_first_page_reference_estimates_without_detected_headings(make_pdf):
    index = extract_index(make_pdf())
    reference = PageReference(printed_page="1", pdf_page=12)
    report = match_entries(
        index,
        [entry("Absent first chapter", "1"), entry("Absent later section", "20")],
        {1, 2},
        page_reference=reference,
    )
    assert [e.pdf_page for e in report.entries] == [12, 31]
    assert all(e.status == "Estimated" for e in report.entries)
    assert all("手動指定の基準" in e.reasons[0] for e in report.entries)
    assert report.anchor_count == 0
    assert report.page_map.segments[0].reference


@pytest.mark.parametrize("printed, later", [("i", "iii"), ("Ａ−１", "A-3"), ("５", "7")])
def test_reference_keeps_numbering_domains_separate(printed, later):
    page_map = PageMap([], PageReference(printed_page=printed, pdf_page=12))
    assert page_map.predict(later)[0] == 14
    assert page_map.predict("B-3")[0] is None


def test_arabic_reference_does_not_apply_to_front_matter_or_appendix():
    page_map = PageMap([], PageReference(printed_page="1", pdf_page=12))
    assert page_map.predict("iii")[0] is None
    assert page_map.predict("A-3")[0] is None


def test_reference_and_later_headings_detect_offset_change(make_pdf):
    report = match_entries(
        extract_index(make_pdf({31: ["Later chapter"], 41: ["Final chapter"]})),
        [
            entry("Missing first chapter", "1"),
            entry("Missing transition", "10"),
            entry("Later chapter", "20"),
            entry("Missing later section", "25"),
            entry("Final chapter", "30"),
        ],
        {1, 2},
        page_reference=PageReference(printed_page="1", pdf_page=11),
    )
    assert [e.pdf_page for e in report.entries] == [11, None, 31, 36, 41]
    assert [e.status for e in report.entries] == [
        "Estimated",
        "Unresolved",
        "Verified",
        "Estimated",
        "Verified",
    ]
    assert any("切替位置は未確定" in warning for warning in report.warnings)


def test_isolated_heading_conflict_blocks_reference_extrapolation(make_pdf):
    report = match_entries(
        extract_index(make_pdf({35: ["Detected chapter"]})),
        [entry("Detected chapter", "20"), entry("Missing last chapter", "25")],
        {1, 2},
        page_reference=PageReference(printed_page="1", pdf_page=11),
    )
    assert report.entries[0].pdf_page == 35
    assert report.entries[0].status == "Verified"
    assert report.entries[1].status == "Unresolved"
    assert report.warnings


def test_reference_does_not_estimate_outside_pdf_or_into_toc(make_pdf):
    report = match_entries(
        extract_index(make_pdf()),
        [entry("Before body", "1"), entry("Past end", "100")],
        {1, 2},
        page_reference=PageReference(printed_page="12", pdf_page=12),
    )
    assert all(e.status == "Unresolved" for e in report.entries)


@pytest.mark.parametrize("page", [1, 61])
def test_reference_location_is_validated_before_matching(make_pdf, page):
    with pytest.raises(ValueError, match="基準のPDFページ"):
        match_entries(
            extract_index(make_pdf()),
            [entry("Missing", "1")],
            {1, 2},
            page_reference=PageReference(printed_page="1", pdf_page=page),
        )


@pytest.mark.parametrize("printed", ["", " ", "12b", "Introduction"])
def test_reference_rejects_unusable_printed_page(printed):
    with pytest.raises(ValueError):
        PageReference(printed_page=printed, pdf_page=12)
