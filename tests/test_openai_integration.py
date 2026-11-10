"""Explicit opt-in only: real request, network access and billing required."""

import os

import pytest

from pdfbookmark.llm import OpenAITocParser


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_OPENAI_TESTS") != "1" or not os.getenv("OPENAI_API_KEY"),
    reason="Requires RUN_OPENAI_TESTS=1 and OPENAI_API_KEY",
)
def test_live_structured_toc():
    parsed = OpenAITocParser(
        os.environ["OPENAI_API_KEY"], os.getenv("OPENAI_MODEL", "gpt-4.1-mini")
    ).parse("第1章 基礎 ........ 1\n  1.1 定義 ........ 3\n第2章 応用 ........ 20")
    assert [e.level for e in parsed.entries] == [1, 2, 1]
    assert [e.printed_page for e in parsed.entries] == ["1", "3", "20"]
