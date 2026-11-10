"""Deterministic two-pass heading matching with conservative piecewise page offsets."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from statistics import median

from rapidfuzz.fuzz import ratio

from pdfbookmark.models import Bookmark, Candidate, PageReference, TocEntry
from pdfbookmark.normalization import normalize, printed_number, split_heading
from pdfbookmark.pdf import PdfIndex


def grams(value: str) -> set[str]:
    size = min(3, len(value))
    return {value[i : i + size] for i in range(len(value) - size + 1)} if size else set()


@dataclass(frozen=True)
class Span:
    page: int
    text: str
    normalized: str
    prefix: str
    body: str
    y: float
    prominent: bool
    running: bool
    source: str
    confidence: float


class HeadingIndex:
    def __init__(self, index: PdfIndex, excluded: set[int]):
        self.spans: list[Span] = []
        self.postings: dict[str, list[int]] = defaultdict(list)
        self.exact: dict[str, list[int]] = defaultdict(list)
        margins: dict[str, set[int]] = defaultdict(set)
        for page in index.pages:
            if page.number in excluded:
                continue
            for line in page.lines:
                if line.bbox[1] / page.height < 0.085 or line.bbox[1] / page.height > 0.91:
                    margins[line.normalized].add(page.number)
        for page in index.pages:
            if page.number in excluded:
                continue
            heights = [line.bbox[3] - line.bbox[1] for line in page.lines]
            typical = median(heights) if heights else 10
            typical_size = median([line.size for line in page.lines]) if page.lines else 10
            for i, line in enumerate(page.lines):
                # Include up to three nearby lines to reconstruct wrapped headings.
                for count in range(1, 4):
                    group = page.lines[i : i + count]
                    if len(group) != count:
                        break
                    if count > 1 and any(
                        b.bbox[1] - a.bbox[3]
                        > max(
                            a.bbox[3] - a.bbox[1] if a.source == "ocr" else a.size,
                            b.bbox[3] - b.bbox[1] if b.source == "ocr" else b.size,
                        )
                        * 1.6
                        or abs(b.bbox[0] - a.bbox[0]) > 80
                        for a, b in zip(group, group[1:], strict=False)
                    ):
                        break
                    text = " ".join(part.text for part in group).strip()
                    normalized = normalize(text)
                    if not normalized or len(normalized) > 300:
                        continue
                    prefix, body = split_heading(text)
                    y = line.bbox[1] / page.height
                    span = Span(
                        page.number,
                        text,
                        normalized,
                        prefix,
                        body,
                        y,
                        (
                            line.bbox[3] - line.bbox[1] >= typical * 1.25
                            or (
                                len(normalized) < 100
                                and (i == 0 or line.bbox[1] - page.lines[i - 1].bbox[3] > typical)
                                and (
                                    i + count == len(page.lines)
                                    or page.lines[i + count].bbox[1] - group[-1].bbox[3] > typical
                                )
                            )
                            if line.source == "ocr"
                            else line.size >= typical_size * 1.15
                        ),
                        (y < 0.085 or y > 0.91) and len(margins[line.normalized]) >= 3,
                        line.source,
                        min(part.confidence for part in group),
                    )
                    n = len(self.spans)
                    self.spans.append(span)
                    self.exact[normalized].append(n)
                    if body and body != normalized:
                        self.exact[body].append(n)
                    for gram in grams(body or normalized):
                        self.postings[gram].append(n)

    def search(self, entry: TocEntry) -> list[Candidate]:
        title = normalize(entry.title)
        prefix, body = split_heading(entry.title)
        query = body or title
        counts: Counter[int] = Counter()
        for gram in grams(query):
            counts.update(self.postings.get(gram, []))
        possible = set(self.exact.get(title, [])) | set(self.exact.get(query, []))
        possible.update(n for n, _ in counts.most_common(700))
        per_page: dict[int, Candidate] = {}
        for n in possible:
            span = self.spans[n]
            if entry.title.strip() == span.text:
                similarity, method = 1.0, "完全一致"
            elif title == span.normalized:
                similarity, method = 1.0, "正規化一致"
            elif query == span.body and query:
                similarity, method = 0.98, "見出し本文一致"
            else:
                similarity = max(ratio(title, span.normalized), ratio(query, span.body)) / 100
                method = "曖昧一致"
            if similarity < 0.70:
                continue
            reasons = [f"{method} {similarity:.0%}", f"ページ内位置 {span.y:.0%}"]
            score = similarity * 0.78 + 0.08 + 0.07 * (1 - span.y) ** 0.5
            score += 0.07 if span.prominent else 0.015
            if span.prominent:
                reasons.append(
                    "周辺より大きい文字・独立した行"
                    if span.source == "ocr"
                    else "周辺より大きい文字"
                )
            if span.source == "ocr":
                score -= (1 - span.confidence) * 0.30
                reasons.append(f"OCR認識信頼度 {span.confidence:.0%}（PDF座標で位置評価）")
            if prefix and span.prefix:
                if prefix == span.prefix:
                    score += 0.025
                    reasons.append("章節番号一致")
                else:
                    score -= 0.20
                    reasons.append("章節番号が不一致")
            if span.running:
                score -= 0.35
                reasons.append("繰り返しの柱・フッター候補")
            if re.search(
                r"参照|を見よ|see\s|refer\s|\.{3,}|…{2,}|[.．・]{3,}", span.text, re.IGNORECASE
            ):
                score -= 0.22
                reasons.append("相互参照・目次行の疑い")
            if len(query) < 5 and not (prefix and prefix == span.prefix):
                score -= 0.12
                reasons.append("短い見出しのため減点")
            candidate = Candidate(
                pdf_page=span.page,
                score=round(max(0, min(0.99, score)), 4),
                similarity=similarity,
                method=method,
                y=span.y,
                reasons=reasons,
                source=span.source,
                ocr_confidence=span.confidence,
            )
            old = per_page.get(span.page)
            if old is None or candidate.score > old.score:
                per_page[span.page] = candidate
        return sorted(per_page.values(), key=lambda c: (-c.score, c.pdf_page))[:12]


@dataclass(frozen=True)
class Anchor:
    entry: int
    domain: str
    printed: int
    pdf: int

    @property
    def offset(self) -> int:
        return self.pdf - self.printed


@dataclass(frozen=True)
class OffsetSegment:
    domain: str
    first: int
    last: int
    offset: int
    support: int
    reference: bool = False


class PageMap:
    def __init__(self, anchors: list[Anchor], reference: PageReference | None = None):
        self.reference = reference
        reference_anchor = None
        if reference is not None:
            domain, number = printed_number(reference.printed_page)
            reference_anchor = Anchor(-1, domain, number, reference.pdf_page)
        self.anchors = anchors + ([reference_anchor] if reference_anchor else [])
        self.segments: list[OffsetSegment] = []
        self.warnings: list[str] = []
        domains: dict[str, list[Anchor]] = defaultdict(list)
        for anchor in self.anchors:
            domains[anchor.domain].append(anchor)
        for domain, group in domains.items():
            group.sort(key=lambda a: (a.printed, a.entry))
            support: dict[int, set[int]] = defaultdict(set)
            for a in group:
                support[a.offset].add(a.printed)
            # A single automatic match is insufficient. An explicit user reference
            # can establish its own offset without relying on OCR'd headings.
            accepted = [
                a
                for a in group
                if len(support[a.offset]) >= 2
                or (
                    reference_anchor is not None
                    and a.domain == reference_anchor.domain
                    and a.offset == reference_anchor.offset
                )
            ]
            runs: list[list[Anchor]] = []
            for a in accepted:
                if runs and runs[-1][-1].offset == a.offset:
                    runs[-1].append(a)
                else:
                    runs.append([a])
            for run in runs:
                distinct = {a.printed for a in run}
                has_reference = reference_anchor is not None and reference_anchor in run
                if len(distinct) >= 2 or has_reference:
                    self.segments.append(
                        OffsetSegment(
                            domain,
                            min(distinct),
                            max(distinct),
                            run[0].offset,
                            len(distinct),
                            has_reference,
                        )
                    )
            domain_segments = [s for s in self.segments if s.domain == domain]
            if domain_segments:
                supported_offsets = {s.offset for s in domain_segments}
                isolated = sorted({a.offset for a in group if a.offset not in supported_offsets})
                for offset in isolated:
                    self.warnings.append(
                        f"{domain}: オフセット{offset:+d}の孤立したアンカーがあります。"
                        "他の項目のページ補正には使いません。挿入・欠落・誤一致を確認してください"
                    )
            for left, right in zip(domain_segments, domain_segments[1:], strict=False):
                self.warnings.append(
                    f"{domain}: 印刷ページ{left.last}〜{right.first}の間でオフセットが"
                    f"{left.offset:+d} → {right.offset:+d}に変化。切替位置は未確定です"
                )

    def predict(self, printed: str | None) -> tuple[int | None, str]:
        parsed = printed_number(printed)
        if parsed is None:
            return None, "印刷ページ番号を数値化できません"
        domain, number = parsed
        segments = [s for s in self.segments if s.domain == domain]
        if not segments:
            return None, "同じ番号体系の独立したアンカーが2件以上ありません"
        selected = None
        for segment in segments:
            if segment.first <= number <= segment.last:
                selected = segment
        if number < segments[0].first and not any(
            a.domain == domain and a.printed < segments[0].first and a.offset != segments[0].offset
            for a in self.anchors
        ):
            selected = segments[0]
        if number > segments[-1].last and not any(
            a.domain == domain and a.printed > segments[-1].last and a.offset != segments[-1].offset
            for a in self.anchors
        ):
            selected = segments[-1]
        if selected is None:
            return None, "オフセット変化の境界区間で移動先を一意に推定できません"
        reason = (
            f"{domain}のアンカー{selected.support}件、印刷ページ"
            f"{selected.first}〜{selected.last}、オフセット{selected.offset:+d}"
        )
        if selected.reference:
            reason = (
                f"手動指定の基準（印刷ページ{self.reference.printed_page} → "
                f"PDFページ{self.reference.pdf_page}）、" + reason
            )
        return number + selected.offset, reason


def monotone_anchors(anchors: list[Anchor]) -> list[Anchor]:
    """Longest nondecreasing PDF-page subsequence in TOC order (same-page allowed)."""
    if not anchors:
        return []
    lengths = [1] * len(anchors)
    parents = [-1] * len(anchors)
    for i, current in enumerate(anchors):
        for j in range(i):
            if anchors[j].pdf <= current.pdf and lengths[j] + 1 > lengths[i]:
                lengths[i] = lengths[j] + 1
                parents[i] = j
    at = max(range(len(anchors)), key=lambda i: lengths[i])
    selected = []
    while at != -1:
        selected.append(anchors[at])
        at = parents[at]
    return list(reversed(selected))


@dataclass
class MatchReport:
    entries: list[Bookmark]
    page_map: PageMap
    anchor_count: int
    warnings: list[str]


def match_entries(
    index: PdfIndex,
    entries: list[TocEntry],
    toc_pages: set[int],
    progress: Callable[[float], None] | None = None,
    *,
    page_reference: PageReference | None = None,
) -> MatchReport:
    if any(p < 1 or p > index.page_count for p in toc_pages):
        raise ValueError("目次ページが範囲外です")
    if page_reference is not None:
        page_reference.validate_location(index.page_count, toc_pages)
    search = HeadingIndex(index, toc_pages)
    candidates = []
    raw_anchors = []
    for i, entry in enumerate(entries):
        found = search.search(entry)
        candidates.append(found)
        parsed = printed_number(entry.printed_page)
        manual = isinstance(entry, Bookmark) and entry.manual and entry.pdf_page is not None
        unique = found and (len(found) == 1 or found[0].score - found[1].score >= 0.055)
        strong = (
            unique
            and found[0].score >= 0.90
            and found[0].similarity >= 0.97
            and found[0].method != "曖昧一致"
            and (found[0].source != "ocr" or found[0].ocr_confidence >= 0.90)
        )
        if parsed and (manual or strong):
            raw_anchors.append(
                Anchor(i, parsed[0], parsed[1], entry.pdf_page if manual else found[0].pdf_page)
            )
        if progress:
            progress(0.65 * (i + 1) / max(1, len(entries)))
    anchors = monotone_anchors(raw_anchors)
    number_anchors = page_number_anchors(index, toc_pages)
    page_map = PageMap(anchors + number_anchors, page_reference)
    results = []
    warnings = list(page_map.warnings)
    if len(anchors) < len(raw_anchors):
        warnings.append("目次順序と矛盾するアンカー候補をページ番号の推定から除外しました")
    for i, entry in enumerate(entries):
        if isinstance(entry, Bookmark) and entry.manual and entry.pdf_page is not None:
            if entry.pdf_page > index.page_count:
                raise ValueError("手動指定のページが範囲外です")
            results.append(entry.model_copy(deep=True))
            continue
        result = Bookmark(**entry.model_dump(include={"level", "title", "printed_page"}))
        if isinstance(entry, Bookmark):
            result.id = entry.id
        predicted, map_reason = page_map.predict(entry.printed_page)
        previous = max((a.pdf for a in anchors if a.entry < i), default=1)
        following = min((a.pdf for a in anchors if a.entry > i), default=index.page_count)
        ranked = []
        for original in candidates[i]:
            c = original.model_copy(deep=True)
            if not previous <= c.pdf_page <= following:
                continue
            if predicted is not None:
                distance = abs(c.pdf_page - predicted)
                if distance <= 1:
                    c.score = min(0.99, c.score + 0.06)
                    c.reasons.append(f"ページ対応と整合（予測{predicted}）")
                elif distance > 3:
                    c.score = max(0, c.score - 0.10)
                    c.reasons.append(f"ページ対応から{distance}ページ離れている")
                if c.source == "ocr" and distance:
                    # Distinguish duplicate headings on adjacent leaves, even
                    # when their raw text scores both saturate near one.
                    c.score = max(0, c.score - 0.045 * min(distance, 3))
            ranked.append(c)
        ranked.sort(key=lambda c: (-c.score, c.pdf_page))
        result.candidates = ranked[:5]
        best = ranked[0] if ranked else None
        gap = best.score - ranked[1].score if len(ranked) > 1 else 1
        original_unique = candidates[i] and (
            len(candidates[i]) == 1 or candidates[i][0].score - candidates[i][1].score >= 0.055
        )
        direct = candidates[i][0] if original_unique else None
        if (
            direct
            and direct.score >= 0.90
            and direct.similarity >= 0.97
            and direct.method != "曖昧一致"
            and previous <= direct.pdf_page <= following
            and (
                direct.source != "ocr"
                or (direct.ocr_confidence >= 0.90 and predicted == direct.pdf_page)
            )
        ):
            # A strong heading beats a provisional numeric model, especially just
            # after an insertion/deletion that has not yet gained two anchors.
            result.pdf_page = direct.pdf_page
            result.status = "Verified"
            result.confidence = min(0.98, direct.score)
            result.reasons = direct.reasons + ["本文見出しに強い一致。目次順序と整合"]
            if predicted is not None and abs(direct.pdf_page - predicted) > 2:
                result.confidence = min(0.94, result.confidence)
                result.reasons.append(
                    f"ページ予測{predicted}より本文見出しを優先。オフセット変化の可能性"
                )
                warnings.append(
                    f"「{entry.title}」: ページ予測{predicted}と本文見出し"
                    f"{direct.pdf_page}が一致しません。本文見出しを採用しました"
                )
        elif (
            best
            and best.score >= 0.81
            and best.similarity >= 0.78
            and gap >= 0.035
            and predicted is not None
            and abs(best.pdf_page - predicted) <= 2
        ):
            result.pdf_page = best.pdf_page
            result.status = "Probable"
            result.confidence = min(0.85, best.score)
            result.reasons = best.reasons + [map_reason]
        elif (
            predicted is not None
            and 1 <= predicted <= index.page_count
            and previous <= predicted <= following
            and predicted not in toc_pages
        ):
            result.pdf_page = predicted
            result.status = "Estimated"
            result.confidence = 0.55
            result.reasons = [map_reason, "本文見出しの強い一致なし。ページ推定のみのため要確認"]
        else:
            result.reasons = [map_reason, "見出しの不一致・重複・順序矛盾のため要確認"]
        results.append(result)
        if progress:
            progress(0.65 + 0.35 * (i + 1) / max(1, len(entries)))
    # Surface residual order conflicts even when a user manually chose destinations.
    resolved = [e for e in results if e.pdf_page is not None]
    conflicts = [
        (a, b) for a, b in zip(resolved, resolved[1:], strict=False) if b.pdf_page < a.pdf_page
    ]
    if conflicts:
        warnings.append("移動先に目次順との矛盾があります。編集画面で確認してください")
        for pair in conflicts:
            for e in pair:
                if not e.manual:
                    e.pdf_page, e.status, e.confidence = None, "Unresolved", 0
                    e.reasons.append("隣接する見出しの移動先と順序が矛盾するため要確認")
    return MatchReport(results, page_map, len(anchors), warnings)


def page_number_anchors(index: PdfIndex, excluded: set[int]) -> list[Anchor]:
    """Only isolated high-confidence margin labels, never a heading verification."""
    found = []
    for page in index.pages:
        if page.number in excluded or page.source != "ocr":
            continue
        labels = set()
        for line in page.lines:
            if line.confidence < 0.85:
                continue
            if line.bbox[3] / page.height > 0.12 and line.bbox[1] / page.height < 0.88:
                continue
            value = line.text.strip().strip("-–—·• ")
            parsed = printed_number(re.sub(r"\s+", "", value))
            if parsed is not None:
                labels.add(parsed)
        if len(labels) == 1:
            domain, number = labels.pop()
            found.append(Anchor(-1, domain, number, page.number))
    # Duplicated leaves/number resets cannot supply a unique automatic mapping.
    counts = Counter((a.domain, a.printed) for a in found)
    return [a for a in found if counts[a.domain, a.printed] == 1]
