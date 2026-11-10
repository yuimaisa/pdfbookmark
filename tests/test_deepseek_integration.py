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


# Offline Vision transport/validation tests run even without a real API key.
def image_client(content=None, status=200):
    import json

    import httpx
    from openai import OpenAI

    calls = []

    def respond(request):
        calls.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": {"message": "PRIVATE-PDF-KEY"}})
        return httpx.Response(
            200,
            json={
                "id": "test",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": content
                            if content is not None
                            else json.dumps(
                                {
                                    "entries": [
                                        {"level": 1, "title": "第1章 基礎", "printed_page": "i"},
                                        {"level": 2, "title": "線形代数", "printed_page": "A-1"},
                                    ]
                                }
                            ),
                        },
                    }
                ],
            },
        )

    client = OpenAI(
        api_key="PRIVATE-PDF-KEY",
        base_url="https://api.deepseek.com",
        max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    )
    return client, calls


def test_image_pdf_multiple_pages_base64_and_json_format(make_image_pdf):
    import base64
    import json

    from pdfbookmark.pdf import render_page

    data = make_image_pdf({1: "目次", 2: "続き", 3: "PRIVATE BODY"})
    client, calls = image_client()
    with client:
        parsed = DeepSeekTocParser("", client=client).parse_pdf_images(data, [1, 2])
    assert [e.level for e in parsed.entries] == [1, 2]
    payload = json.loads(calls[0].content)
    assert str(calls[0].url) == "https://api.deepseek.com/chat/completions"
    assert payload["response_format"] == {"type": "json_object"}
    assert "json_schema" not in str(payload)
    assert payload["thinking"] == {"type": "disabled"}
    parts = payload["messages"][1]["content"]
    images = [p["image_url"]["url"] for p in parts if p["type"] == "image_url"]
    assert len(images) == 2
    for number, url in enumerate(images, 1):
        assert url.startswith("data:image/png;base64,")
        assert base64.b64decode(url.split(",", 1)[1]) == render_page(data, number, width=1600)
    assert "PRIVATE BODY" not in calls[0].content.decode()


@pytest.mark.parametrize(
    "content",
    ["", " ", "not-json", '{"entries":[{"level":2,"title":"private content","printed_page":"1"}]}'],
)
def test_image_response_errors_are_safe(make_image_pdf, content, caplog):
    from pdfbookmark.llm import TocParseError

    client, _ = image_client(content)
    with client, pytest.raises(TocParseError) as error:
        DeepSeekTocParser("", client=client).parse_pdf_images(make_image_pdf(), [1])
    assert "private content" not in str(error.value)
    assert "PRIVATE-PDF-KEY" not in caplog.text


def test_image_api_failure_preserves_privacy(make_image_pdf, caplog):
    from pdfbookmark.llm import TocParseError

    client, _ = image_client(status=429)
    with client, pytest.raises(TocParseError, match="429") as error:
        DeepSeekTocParser("", client=client).parse_pdf_images(make_image_pdf(), [1])
    assert "PRIVATE-PDF-KEY" not in str(error.value) + caplog.text


def test_image_bounds_and_nonvision_models_do_not_call_api(make_image_pdf):
    from pdfbookmark.llm import TocParseError

    client, calls = image_client()
    with client:
        parser = DeepSeekTocParser("", client=client)
        for images in ([], [b"not-png"], [b"x"] * 13):
            with pytest.raises(TocParseError):
                parser.parse_images(images)
        with pytest.raises(TocParseError):
            parser.parse_pdf_images(make_image_pdf(), [2, 1])
        with pytest.raises(TocParseError):
            DeepSeekTocParser("", "deepseek-v4-pro", client).parse_images([b"\x89PNG\r\n\x1a\n"])
    assert not calls


@pytest.mark.integration
@pytest.mark.skipif(
    os.getenv("RUN_DEEPSEEK_TESTS") != "1" or not os.getenv("DEEPSEEK_API_KEY"),
    reason="Requires RUN_DEEPSEEK_TESTS=1 and DEEPSEEK_API_KEY",
)
def test_live_deepseek_image_toc(make_image_pdf):
    data = make_image_pdf({1: "第1章 基礎理論 ........ 1"}, pages=1)
    parsed = DeepSeekTocParser(os.environ["DEEPSEEK_API_KEY"]).parse_pdf_images(data, [1])
    assert parsed.entries[0].level == 1
    assert "基礎理論" in parsed.entries[0].title
    assert parsed.entries[0].printed_page == "1"
