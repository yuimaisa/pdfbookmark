from __future__ import annotations

from hashlib import sha256

from pdfbookmark.models import Bookmark, Project, validate_hierarchy
from pdfbookmark.pdf import open_pdf


def export_pdf(data: bytes, entries: list[Bookmark], *, overwrite_existing: bool = False) -> bytes:
    if not entries:
        raise ValueError("ブックマークを1件以上指定してください")
    validate_hierarchy(entries)
    with open_pdf(data) as doc:
        if doc.get_toc() and not overwrite_existing:
            raise ValueError("既存ブックマークの置き換え確認が必要です")
        toc = []
        for i, entry in enumerate(entries, 1):
            if entry.pdf_page is None or entry.status == "Unresolved":
                raise ValueError(f"項目{i}: 未解決の移動先を指定するか、項目を削除してください")
            if not 1 <= entry.pdf_page <= doc.page_count:
                raise ValueError(f"項目{i}: PDFページが範囲外です")
            toc.append([entry.level, entry.title, entry.pdf_page])
        doc.set_toc(toc)
        # No page transformations, rasterization, image recompression, or text edits.
        output = doc.tobytes(garbage=0, deflate=False)
        page_count = doc.page_count
    with open_pdf(output) as verified:
        if verified.page_count != page_count or verified.get_toc() != toc:
            raise ValueError("出力PDFの項目数・階層・移動先の再検証に失敗しました")
    return output


def export_project(
    data: bytes, page_count: int, toc_pages: list[int], entries: list[Bookmark]
) -> str:
    project = Project(
        document_sha256=sha256(data).hexdigest(),
        page_count=page_count,
        toc_pages=toc_pages,
        entries=entries,
    )
    return project.model_dump_json(indent=2)


def import_project(text: str, data: bytes, page_count: int) -> Project:
    project = Project.model_validate_json(text)
    if project.document_sha256 != sha256(data).hexdigest() or project.page_count != page_count:
        raise ValueError("別のPDFの中間結果です。元のPDFと一致するJSONを指定してください")
    return project
