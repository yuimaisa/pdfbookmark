"""Install pinned official Japanese/English Tesseract data, never book images."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.request import urlopen

TESSDATA_REVISION = "87416418657359cb625c412a48b6e1d6d41c29bd"


def cache_directory() -> Path:
    return Path(os.getenv("PDFBOOKMARK_CACHE_DIR", Path.home() / ".cache" / "pdfbookmark"))


def tessdata_directory() -> Path:
    return cache_directory() / "tessdata" / TESSDATA_REVISION


def main():
    directory = tessdata_directory()
    directory.mkdir(parents=True, exist_ok=True)
    for language in ("eng", "jpn"):
        target = directory / f"{language}.traineddata"
        if target.exists():
            continue
        url = (
            "https://raw.githubusercontent.com/tesseract-ocr/tessdata_fast/"
            f"{TESSDATA_REVISION}/{language}.traineddata"
        )
        with urlopen(url, timeout=60) as response:
            data = response.read()
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(data)
        temporary.replace(target)
    print(f"日本語・英語モデルを準備しました: {directory}")


if __name__ == "__main__":
    main()
