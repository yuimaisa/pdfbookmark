"""Explicit opt-in only: a real DeepSeek request consumes API credit."""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from pdfbookmark.llm import DEEPSEEK_DEFAULT_MODEL, DeepSeekTocParser

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_TESTS") != "1" or not os.getenv("DEEPSEEK_API_KEY"),
    reason="Requires RUN_DEEPSEEK_TESTS=1 and DEEPSEEK_API_KEY",
)
def test_live_deepseek_toc():
    parsed = DeepSeekTocParser(
        os.environ["DEEPSEEK_API_KEY"],
        os.getenv("DEEPSEEK_MODEL") or DEEPSEEK_DEFAULT_MODEL,
    ).parse("第1章 基礎 ........ 1\n  1.1 定義 ........ 3\n第2章 応用 ........ 20")
    assert [e.level for e in parsed.entries] == [1, 2, 1]
    assert [e.title for e in parsed.entries] == ["第1章 基礎", "1.1 定義", "第2章 応用"]
    assert [e.printed_page for e in parsed.entries] == ["1", "3", "20"]
