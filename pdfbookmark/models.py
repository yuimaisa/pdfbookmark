from __future__ import annotations

from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pdfbookmark.normalization import printed_number

Status = Literal["Verified", "Probable", "Estimated", "Unresolved"]


class TocEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: int = Field(ge=1, le=20, strict=True)
    title: str = Field(min_length=1, max_length=2000)
    printed_page: str | None

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("見出しは空にできません")
        return value

    @field_validator("printed_page")
    @classmethod
    def clean_page(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class TocDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entries: list[TocEntry] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def valid_hierarchy(self) -> TocDocument:
        validate_hierarchy(self.entries)
        return self


def validate_hierarchy(entries: list[TocEntry]) -> None:
    previous = 0
    for i, entry in enumerate(entries, 1):
        if entry.level > previous + 1:
            raise ValueError(f"項目{i}: 階層は1から始め、深くする際は1段ずつにしてください")
        previous = entry.level


class Candidate(BaseModel):
    pdf_page: int = Field(ge=1)
    score: float = Field(ge=0, le=1)
    similarity: float = Field(ge=0, le=1)
    method: str
    y: float
    reasons: list[str]
    source: str = "text"
    ocr_confidence: float = Field(default=1, ge=0, le=1)


class Bookmark(TocEntry):
    id: str = Field(default_factory=lambda: uuid4().hex)
    pdf_page: int | None = Field(default=None, ge=1, strict=True)
    status: Status = "Unresolved"
    confidence: float = Field(default=0, ge=0, le=1)
    reasons: list[str] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    manual: bool = False


class PageReference(BaseModel):
    """A user-confirmed correspondence, independent of heading detection."""

    model_config = ConfigDict(extra="forbid")

    printed_page: str = Field(min_length=1)
    pdf_page: int = Field(ge=1, strict=True)

    @field_validator("printed_page")
    @classmethod
    def valid_printed_page(cls, value: str) -> str:
        value = value.strip()
        if printed_number(value) is None:
            raise ValueError("基準の印刷ページは数字・ローマ数字・付録番号で指定してください")
        return value

    def validate_location(self, page_count: int, toc_pages: list[int] | set[int]) -> None:
        if self.pdf_page > page_count:
            raise ValueError("基準のPDFページが範囲外です")
        if self.pdf_page in toc_pages:
            raise ValueError("基準のPDFページには目次以外のページを指定してください")


class Project(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    document_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    page_count: int = Field(ge=1)
    toc_pages: list[int] = Field(min_length=1)
    entries: list[Bookmark] = Field(max_length=10000)
    page_reference: PageReference | None = None

    @model_validator(mode="after")
    def valid_pages(self) -> Project:
        if any(p < 1 or p > self.page_count for p in self.toc_pages):
            raise ValueError("目次のPDFページが範囲外です")
        if self.toc_pages != list(range(min(self.toc_pages), max(self.toc_pages) + 1)):
            raise ValueError("目次ページは重複のない連続した昇順の範囲にしてください")
        if any(e.pdf_page and e.pdf_page > self.page_count for e in self.entries):
            raise ValueError("移動先のPDFページが範囲外です")
        if len({e.id for e in self.entries}) != len(self.entries):
            raise ValueError("項目IDが重複しています")
        if self.page_reference is not None:
            self.page_reference.validate_location(self.page_count, self.toc_pages)
        return self
