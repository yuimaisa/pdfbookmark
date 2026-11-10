from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256

import pymupdf

from pdfbookmark.normalization import normalize


@dataclass(frozen=True)
class TextLine:
    text: str
    normalized: str
    bbox: tuple[float, float, float, float]
    size: float
    block: int


@dataclass(frozen=True)
class PageText:
    number: int
    text: str
    normalized: str
    width: float
    height: float
    lines: tuple[TextLine, ...]


@dataclass(frozen=True)
class PdfIndex:
    digest: str
    pages: tuple[PageText, ...]
    existing_toc: tuple[tuple[int, str, int], ...]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text_page_count(self) -> int:
        return sum(bool(p.normalized) for p in self.pages)


def open_pdf(data: bytes) -> pymupdf.Document:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except (pymupdf.FileDataError, RuntimeError, ValueError) as exc:
        raise ValueError("PDFを開けません。ファイルが破損していないか確認してください") from exc
    if doc.needs_pass:
        doc.close()
        raise ValueError("暗号化されたPDFです。先にパスワード保護を解除してください")
    if not doc.is_pdf or doc.page_count == 0:
        doc.close()
        raise ValueError("ページのあるPDFを指定してください")
    return doc


def extract_index(data: bytes, progress: Callable[[float], None] | None = None) -> PdfIndex:
    """Extract once; no rendering and no disk copies. Replaceable by a future OCR adapter."""
    pages = []
    with open_pdf(data) as doc:
        existing = tuple(tuple(row) for row in doc.get_toc())
        for i, page in enumerate(doc):
            lines = []
            blocks = page.get_text("dict", flags=pymupdf.TEXTFLAGS_TEXT)["blocks"]
            for b, block in enumerate(blocks):
                for line in block.get("lines", []):
                    text = "".join(span["text"] for span in line["spans"])
                    lines.append(
                        TextLine(
                            text=text,
                            normalized=normalize(text),
                            bbox=tuple(line["bbox"]),
                            size=max((s["size"] for s in line["spans"]), default=0),
                            block=b,
                        )
                    )
            lines.sort(key=lambda line: (round(line.bbox[1], 1), line.bbox[0]))
            text = "\n".join(line.text for line in lines)
            pages.append(
                PageText(
                    i + 1, text, normalize(text), page.rect.width, page.rect.height, tuple(lines)
                )
            )
            if progress and (i % 10 == 0 or i + 1 == doc.page_count):
                progress((i + 1) / doc.page_count)
    return PdfIndex(sha256(data).hexdigest(), tuple(pages), existing)


def toc_text(index: PdfIndex, first: int, last: int) -> str:
    if not 1 <= first <= last <= index.page_count:
        raise ValueError("目次ページ範囲が不正です")
    return "\n\n".join(
        f"--- PDFページ {p.number} ---\n{p.text}" for p in index.pages[first - 1 : last]
    )


def render_page(data: bytes, number: int, width: int = 1000) -> bytes:
    with open_pdf(data) as doc:
        if not 1 <= number <= doc.page_count:
            raise ValueError("プレビューのページ番号が範囲外です")
        page = doc[number - 1]
        scale = min(width / page.rect.width, 2)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False).tobytes("png")
