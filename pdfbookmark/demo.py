"""Synthetic, redistributable 500-page book for tests and an offline demo."""

import pymupdf

from pdfbookmark.models import TocDocument, TocEntry


def demo_entries() -> TocDocument:
    return TocDocument(
        entries=[
            TocEntry(level=1, title="Preface", printed_page="i"),
            TocEntry(level=1, title="Notation", printed_page="iii"),
            TocEntry(level=1, title="1 Foundations", printed_page="1"),
            TocEntry(level=2, title="1.1 Linear spaces", printed_page="4"),
            TocEntry(level=2, title="1.2 Inner products", printed_page="18"),
            TocEntry(level=1, title="2 Spectral theory", printed_page="200"),
            TocEntry(level=2, title="2.1 Eigenvalues", printed_page="210"),
            TocEntry(level=2, title="2.2 Applications", printed_page="240"),
            TocEntry(level=1, title="3 Probability", printed_page="400"),
        ]
    )


def demo_pdf() -> bytes:
    with pymupdf.open() as doc:
        headings = {
            4: "Preface",
            6: "Notation",
            13: "1 Foundations",
            16: "1.1 Linear spaces",
            30: "1.2 Inner products",
            213: "2 Spectral theory",
            223: "2.1 Eigenvalues",
            253: "2.2 Applications",
            413: "3 Probability",
        }
        for n in range(1, 501):
            page = doc.new_page(width=595, height=842)
            if n == 1:
                page.insert_text((56, 150), "A synthetic textbook", fontsize=26)
            elif n in (2, 3):
                page.insert_text((56, 90), "Contents", fontsize=22)
                entries = demo_entries().entries[:5] if n == 2 else demo_entries().entries[5:]
                for i, entry in enumerate(entries):
                    page.insert_text(
                        (56 + (entry.level - 1) * 16, 140 + i * 38),
                        f"{entry.title} ........ {entry.printed_page}",
                        fontsize=12,
                    )
            else:
                page.insert_text((56, 35), "Synthetic textbook / Running header", fontsize=9)
                if n in headings:
                    page.insert_text((56, 110), headings[n], fontsize=21)
                for line in range(8):
                    page.insert_text(
                        (56, 240 + line * 26),
                        f"Sample body paragraph {line + 1}. Concepts and examples.",
                        fontsize=11,
                    )
                # Chapter opening leaves may have no printed page number.
                if n not in headings:
                    printed = n - (13 if n >= 213 else 12)
                    if printed > 0:
                        page.insert_text((290, 810), str(printed), fontsize=10)
        return doc.tobytes()
