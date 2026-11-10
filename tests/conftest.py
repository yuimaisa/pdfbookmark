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


@pytest.fixture
def make_image_pdf():
    """Rasterize generated pages and assert that no extractable text survives."""

    def make(headings=None, *, pages=4, printed=None, rotation=0):
        headings, printed = headings or {}, printed or {}
        with pymupdf.open() as output:
            images = {}
            for n in range(1, pages + 1):
                title = headings.get(n, "")
                label = printed.get(n, "")
                key = title, label
                page = output.new_page(width=595, height=842)
                if key not in images:
                    with pymupdf.open() as source:
                        leaf = source.new_page(width=595, height=842)
                        if title:
                            leaf.insert_text((55, 140), title, fontsize=26, fontname="japan")
                        if label:
                            leaf.insert_text((290, 815), label, fontsize=18)
                        png = leaf.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False).tobytes(
                            "png"
                        )
                    images[key] = page.insert_image(page.rect, stream=png)
                else:
                    page.insert_image(page.rect, xref=images[key])
                page.set_rotation(rotation)
            data = output.tobytes()
        with pymupdf.open(stream=data, filetype="pdf") as checked:
            assert all(not p.get_text().strip() and p.get_images() for p in checked)
        return data

    return make
