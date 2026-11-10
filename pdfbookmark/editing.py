"""Merge a filtered editor without dropping rows outside the visible selection."""

from __future__ import annotations

import math

from pdfbookmark.models import Bookmark, validate_hierarchy


def empty(value) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value == ""


def integer(value, label: str, optional: bool = False) -> int | None:
    if empty(value) and optional:
        return None
    try:
        number = float(value)
        if not math.isfinite(number) or number != int(number):
            raise ValueError
        return int(number)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label}には整数を指定してください") from exc


def merge_edits(
    existing: list[Bookmark], visible_ids: set[str], rows: list[dict], page_count: int
) -> list[Bookmark]:
    known = {e.id: e for e in existing}
    edited = []
    seen = set()
    for row in rows:
        if all(empty(row.get(k)) for k in ("title", "level", "printed_page", "pdf_page")):
            continue
        row_id = row.get("id")
        old = known.get(row_id) if isinstance(row_id, str) else None
        if old and (old.id not in visible_ids or old.id in seen):
            raise ValueError("編集対象のIDが重複しているか不正です")
        title = "" if empty(row.get("title")) else str(row["title"])
        printed = None if empty(row.get("printed_page")) else str(row["printed_page"])
        page = integer(row.get("pdf_page"), "PDFページ", optional=True)
        if page is not None and not 1 <= page <= page_count:
            raise ValueError(f"PDFページは1〜{page_count}で指定してください")
        entry = Bookmark(
            level=integer(row.get("level"), "階層"),
            title=title,
            printed_page=printed,
            pdf_page=page,
        )
        if old:
            seen.add(old.id)
            entry = old.model_copy(
                update={
                    "level": entry.level,
                    "title": entry.title,
                    "printed_page": entry.printed_page,
                    "pdf_page": page,
                },
                deep=True,
            )
        changed_title = old and (old.title != entry.title or old.printed_page != printed)
        changed_page = page is not None and (old is None or old.pdf_page != page)
        if changed_page:
            entry.manual = True
            entry.status = "Probable"
            entry.confidence = 0.85
            entry.reasons = ["ユーザーが移動先を指定（本文の自動検証とは別）"]
            entry.candidates = []
        elif page is None or (changed_title and not entry.manual):
            entry.pdf_page = None
            entry.manual = False
            entry.status = "Unresolved"
            entry.confidence = 0
            entry.reasons = [
                "見出し・印刷ページの変更後は再照合、または移動先を手動指定してください"
            ]
            entry.candidates = []
        edited.append(entry)
    if visible_ids == set(known):
        result = edited
    else:
        replacements = {e.id: e for e in edited if e.id in known}
        result = [
            replacements[e.id] if e.id in replacements else e
            for e in existing
            if e.id not in visible_ids or e.id in replacements
        ]
        result.extend(e for e in edited if e.id not in known)
    validate_hierarchy(result)
    return result
