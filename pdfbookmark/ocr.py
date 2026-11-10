"""Local, page-at-a-time OCR with PDF-space coordinates and durable results."""

from __future__ import annotations

import csv
import io
import json
import math
import os
import sqlite3
import subprocess
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, replace
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Protocol

import pymupdf

from pdfbookmark.normalization import normalize
from pdfbookmark.ocr_setup import TESSDATA_REVISION, cache_directory, tessdata_directory
from pdfbookmark.pdf import PageText, PdfIndex, TextLine, open_pdf


class OcrError(ValueError):
    """A sanitized error suitable for the UI (no OCR payload or backend stderr)."""


class OcrCacheError(OcrError):
    pass


@dataclass(frozen=True)
class OcrSettings:
    dpi: int = 200
    preprocessing: str = "none"
    max_pixels: int = 25_000_000

    def __post_init__(self):
        if not 72 <= self.dpi <= 400 or self.preprocessing not in {"none", "grayscale"}:
            raise ValueError("OCR解像度は72〜400 dpi、前処理はnone/grayscaleを指定してください")
        if not 1_000_000 <= self.max_pixels <= 50_000_000:
            raise ValueError("OCR画像のピクセル上限が範囲外です")


@dataclass(frozen=True)
class OcrLine:
    text: str
    bbox: tuple[float, float, float, float]  # engine input pixels
    confidence: float
    order: int
    block: int = 0


class OcrEngine(Protocol):
    name: str
    model_version: str

    def recognize(self, png: bytes) -> list[OcrLine]: ...


class TesseractEngine:
    name = "tesseract"

    def __init__(self, language: str = "jpn+eng"):
        if language not in {"jpn+eng", "eng", "jpn"}:
            raise OcrError("OCR言語が不正です")
        self.language = language
        self.directory = tessdata_directory()
        try:
            release = (
                subprocess.run(
                    ["tesseract", "--version"], capture_output=True, check=True, timeout=10
                )
                .stdout.decode()
                .splitlines()[0]
            )
            hashes = [
                sha256((self.directory / f"{lang}.traineddata").read_bytes()).hexdigest()
                for lang in language.split("+")
            ]
        except (OSError, subprocess.SubprocessError):
            raise OcrError(
                "pixi run -e ocr ocr-setup を実行し、OCR環境で起動してください"
            ) from None
        self.model_version = f"{release}:{TESSDATA_REVISION}:{language}:{':'.join(hashes)}"

    def recognize(self, png: bytes) -> list[OcrLine]:
        try:
            result = subprocess.run(
                [
                    "tesseract",
                    "stdin",
                    "stdout",
                    "--tessdata-dir",
                    str(self.directory),
                    "-l",
                    self.language,
                    "--psm",
                    "3",
                    "-c",
                    "tessedit_create_tsv=1",
                ],
                input=png,
                capture_output=True,
                check=True,
                timeout=180,
            )
            groups = defaultdict(list)
            for word in csv.DictReader(io.StringIO(result.stdout.decode("utf-8")), delimiter="\t"):
                if word["level"] == "5" and word["text"].strip() and float(word["conf"]) >= 0:
                    groups[(word["block_num"], word["par_num"], word["line_num"])].append(word)
            lines = []
            for order, words in enumerate(groups.values()):
                left = min(int(w["left"]) for w in words)
                top = min(int(w["top"]) for w in words)
                right = max(int(w["left"]) + int(w["width"]) for w in words)
                bottom = max(int(w["top"]) + int(w["height"]) for w in words)
                lines.append(
                    OcrLine(
                        " ".join(w["text"] for w in words),
                        (left, top, right, bottom),
                        sum(float(w["conf"]) for w in words) / len(words) / 100,
                        order,
                        int(words[0]["block_num"]),
                    )
                )
            return lines
        except (OSError, subprocess.SubprocessError, ValueError, KeyError):
            raise OcrError(
                "Tesseractのページ認識に失敗しました。言語モデルと画像を確認してください"
            ) from None


class PaddleEngine:
    name = "paddleocr"

    def __init__(self, device: str = "cpu"):
        if device not in {"cpu", "gpu:0"}:
            raise OcrError("OCRデバイスが不正です")
        self.device = device
        try:
            try:
                paddle_version = version("paddlepaddle")
            except PackageNotFoundError:
                paddle_version = version("paddlepaddle-gpu")
            self.model_version = (
                f"PP-OCRv6_medium:ocr-{version('paddleocr')}:paddle-{paddle_version}:{device}"
            )
        except PackageNotFoundError:
            raise OcrError("pixi run -e paddle start でPaddle環境を起動してください") from None
        try:
            import paddle
            from paddleocr import PaddleOCR

            if device.startswith("gpu") and not paddle.is_compiled_with_cuda():
                raise OcrError("CUDA対応のPaddlePaddleが必要です。CPUを選択してください")
            # Initialize once before computing cache keys. A missing model must
            # fail the job setup, rather than retrying a download for every page.
            self._engine = PaddleOCR(
                text_detection_model_name="PP-OCRv6_medium_det",
                text_recognition_model_name="PP-OCRv6_medium_rec",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=False,
                device=device,
            )
            root = Path(os.getenv("PADDLE_PDX_CACHE_HOME", Path.home() / ".paddlex"))
            weights = sha256()
            for model in ("PP-OCRv6_medium_det", "PP-OCRv6_medium_rec"):
                directory = root / "official_models" / model
                files = sorted(path for path in directory.rglob("*") if path.is_file())
                if not files:
                    raise OcrError("Paddleモデルのバージョンを確認できません")
                for path in files:
                    weights.update(str(path.relative_to(directory)).encode())
                    with path.open("rb") as source:
                        while chunk := source.read(1024 * 1024):
                            weights.update(chunk)
            self.model_version += ":" + weights.hexdigest()
        except OcrError:
            raise
        except Exception:
            raise OcrError(
                "PP-OCRv6を初期化できません。モデル配布先・実行環境を確認してください"
            ) from None

    def recognize(self, png: bytes) -> list[OcrLine]:
        try:
            import numpy as np

            pix = pymupdf.Pixmap(png)
            if pix.n != 3:
                pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
            # Paddle's ndarray interface takes BGR, whereas PyMuPDF returns RGB.
            array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
            result = next(iter(self._engine.predict(array[:, :, ::-1].copy())))
            lines = []
            for order, (text, score, polygon) in enumerate(
                zip(result["rec_texts"], result["rec_scores"], result["rec_polys"], strict=True)
            ):
                points = np.asarray(polygon)
                bbox = (
                    float(points[:, 0].min()),
                    float(points[:, 1].min()),
                    float(points[:, 0].max()),
                    float(points[:, 1].max()),
                )
                lines.append(OcrLine(text, bbox, float(score), order, order))
            return lines
        except Exception:
            raise OcrError(
                "PP-OCRv6の認識に失敗しました。モデル配布先・実行環境を確認してください"
            ) from None


def adapt_page(
    number: int,
    width: float,
    height: float,
    pixel_width: int,
    pixel_height: int,
    lines: list[OcrLine],
) -> PageText:
    """Use actual raster dimensions, including rounding/rotation, not nominal DPI."""
    converted = []
    for line in sorted(lines, key=lambda value: value.order):
        x0, y0, x1, y1 = line.bbox
        if not all(math.isfinite(v) for v in (*line.bbox, line.confidence)):
            raise OcrError("OCR座標・信頼度が不正です")
        if not 0 <= line.confidence <= 1 or x1 < x0 or y1 < y0:
            raise OcrError("OCR座標・信頼度が不正です")
        bbox = (
            max(0, min(width, x0 * width / pixel_width)),
            max(0, min(height, y0 * height / pixel_height)),
            max(0, min(width, x1 * width / pixel_width)),
            max(0, min(height, y1 * height / pixel_height)),
        )
        if normalize(line.text):
            converted.append(
                TextLine(
                    line.text,
                    normalize(line.text),
                    bbox,
                    0,
                    line.block,
                    "ocr",
                    line.confidence,
                    line.order,
                )
            )
    text = "\n".join(line.text for line in converted)
    return PageText(number, text, normalize(text), width, height, tuple(converted), "ocr")


class OcrCache:
    """Each page commits independently. Failed/interrupted pages remain retryable."""

    def __init__(self, path: Path | None = None):
        self.path = path or cache_directory() / "ocr.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS pages (key TEXT PRIMARY KEY, payload TEXT NOT NULL)"
                )
        except sqlite3.Error:
            raise OcrCacheError(
                "OCRキャッシュを開けません。保存先・ディスク容量を確認してください"
            ) from None

    @staticmethod
    def key(digest: str, number: int, engine: OcrEngine, settings: OcrSettings) -> str:
        value = [2, digest, number, engine.name, engine.model_version, asdict(settings)]
        return sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def get(self, key: str) -> PageText | None:
        try:
            with sqlite3.connect(self.path) as db:
                row = db.execute("SELECT payload FROM pages WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error:
            raise OcrCacheError("OCRキャッシュを読み取れません。保存先を確認してください") from None
        if row is None:
            return None
        try:
            value = json.loads(row[0])
            value["lines"] = tuple(
                TextLine(**{**line, "bbox": tuple(line["bbox"])}) for line in value["lines"]
            )
            return PageText(**value)
        except (ValueError, TypeError, KeyError):
            return None  # An interrupted/obsolete/corrupt record is recomputed.

    def put(self, key: str, page: PageText):
        try:
            with sqlite3.connect(self.path) as db:
                db.execute(
                    "INSERT OR REPLACE INTO pages VALUES (?, ?)",
                    (key, json.dumps(asdict(page), ensure_ascii=False)),
                )
        except sqlite3.Error:
            raise OcrCacheError(
                "OCRキャッシュを保存できません。ディスク容量を確認してください"
            ) from None


@dataclass
class OcrBatch:
    index: PdfIndex
    completed: list[int]
    failed: list[int]
    cache_hits: int


def ocr_pages(
    data: bytes,
    index: PdfIndex,
    engine: OcrEngine,
    settings: OcrSettings,
    cache: OcrCache,
    numbers: Iterable[int],
    *,
    limit: int | None = None,
    progress: Callable[[int, int, int], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> OcrBatch:
    requested = list(dict.fromkeys(numbers))
    if any(n < 1 or n > index.page_count for n in requested):
        raise ValueError("OCRページが範囲外です")
    # The digest is supplied by extract_index; prevent accidental cross-document reuse.
    if sha256(data).hexdigest() != index.digest:
        raise ValueError("OCR対象PDFとインデックスが一致しません")
    pages = list(index.pages)
    completed, failed, hits = [], [], 0
    with open_pdf(data) as doc:
        for number in requested:
            if (limit is not None and len(completed) + len(failed) >= limit) or (
                cancelled and cancelled()
            ):
                break
            original = pages[number - 1]
            if original.source == "text" and original.normalized:
                completed.append(number)
                continue
            key = cache.key(index.digest, number, engine, settings)
            page = cache.get(key)
            try:
                if page is not None:
                    hits += 1
                else:
                    source = doc[number - 1]
                    scale = min(
                        settings.dpi / 72,
                        math.sqrt(settings.max_pixels / (source.rect.width * source.rect.height)),
                    )
                    pix = source.get_pixmap(
                        matrix=pymupdf.Matrix(scale, scale),
                        alpha=False,
                        colorspace=pymupdf.csGRAY
                        if settings.preprocessing == "grayscale"
                        else pymupdf.csRGB,
                    )
                    page = adapt_page(
                        number,
                        source.rect.width,
                        source.rect.height,
                        pix.width,
                        pix.height,
                        engine.recognize(pix.tobytes("png")),
                    )
                    cache.put(key, page)
                pages[number - 1] = page
                completed.append(number)
            except OcrCacheError:
                raise
            except (OcrError, RuntimeError, ValueError):
                failed.append(number)
            if progress:
                progress(len(completed), len(failed), hits)
    return OcrBatch(replace(index, pages=tuple(pages)), completed, failed, hits)
