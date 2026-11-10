import pytest

from pdfbookmark.editing import merge_edits
from pdfbookmark.models import Bookmark


def make_entry(title, page=None):
    return Bookmark(
        level=1,
        title=title,
        printed_page="1",
        pdf_page=page,
        status="Verified" if page else "Unresolved",
    )


def test_filtered_edit_preserves_other_rows_and_allows_add_delete():
    verified, first, second = make_entry("Keep", 10), make_entry("First"), make_entry("Delete")
    row = first.model_dump()
    row["pdf_page"] = 20
    added = {"level": 1, "title": "New", "printed_page": None, "pdf_page": 30}
    result = merge_edits([verified, first, second], {first.id, second.id}, [row, added], 100)
    assert [e.title for e in result] == ["Keep", "First", "New"]
    assert result[0] == verified
    assert result[1].manual and result[1].status == "Probable"


def test_changed_title_invalidates_automatic_destination():
    old = make_entry("Old title", 10)
    row = old.model_dump()
    row["title"] = "New title"
    result = merge_edits([old], {old.id}, [row], 100)[0]
    assert result.status == "Unresolved" and result.pdf_page is None


@pytest.mark.parametrize("page", [0, 101, 1.5, "wrong"])
def test_invalid_destination_rejected(page):
    row = {"level": 1, "title": "Heading", "printed_page": None, "pdf_page": page}
    with pytest.raises(ValueError):
        merge_edits([], set(), [row], 100)
