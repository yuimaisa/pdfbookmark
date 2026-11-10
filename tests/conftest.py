import pymupdf
import pytest


@pytest.fixture
def make_pdf():
    """Pages are 1-based; heading tuples may specify (y, title, size)."""

    def make(headings=None, *, pages=60, contents=None, running=None, existing=None):
        headings = headings or {}
        contents = contents or {}
        with pymupdf.open() as doc:
            for n in range(1, pages + 1):
                page = doc.new_page(width=595, height=842)
                if running:
                    page.insert_text((55, 32), running, fontsize=10)
                for j, value in enumerate(headings.get(n, [])):
                    y, title, size = (
                        value if isinstance(value, tuple) else (110 + j * 34, value, 21)
                    )
                    # Built-in CJK font also permits Japanese normalization fixtures.
                    page.insert_text((55, y), title, fontsize=size, fontname="japan")
                for j, text in enumerate(contents.get(n, [])):
                    page.insert_text((55, 300 + j * 23), text, fontsize=11, fontname="japan")
                page.insert_text((55, 640), f"Body paragraph on leaf {n}.", fontsize=11)
                page.insert_text((285, 810), str(n - 10), fontsize=10)
            if existing:
                doc.set_toc(existing)
            return doc.tobytes()

    return make
