import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError

from pdfbookmark.llm import OpenAITocParser, TocParseError, validate_toc_json

TOC = {
    "entries": [
        {"level": 1, "title": "第1章 はじめに", "printed_page": "1"},
        {"level": 2, "title": "1.1 研究背景", "printed_page": "3"},
        {"level": 3, "title": "1.1.1 従来研究", "printed_page": None},
        {"level": 1, "title": "付録A", "printed_page": "A-1"},
    ]
}


class FakeClient:
    def __init__(self, content=None, finish="stop", refusal=None, error=None):
        self.chat = SimpleNamespace(completions=self)
        self.content = content or json.dumps(TOC, ensure_ascii=False)
        self.finish, self.refusal, self.error = finish, refusal, error
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason=self.finish,
                    message=SimpleNamespace(content=self.content, refusal=self.refusal),
                )
            ]
        )


def test_schema_hierarchy_and_only_supplied_toc_is_sent():
    client = FakeClient()
    parsed = OpenAITocParser("", "test-model", client).parse("TOC PAGE ONE\nTOC PAGE TWO")
    assert [e.level for e in parsed.entries] == [1, 2, 3, 1]
    assert parsed.entries[-1].printed_page == "A-1"
    assert parsed.entries[2].printed_page is None
    assert len(client.calls) == 1
    request = client.calls[0]
    assert request["model"] == "test-model"
    assert request["messages"][1]["content"] == "TOC PAGE ONE\nTOC PAGE TWO"
    assert request["response_format"]["json_schema"]["strict"] is True


@pytest.mark.parametrize(
    "payload",
    [
        "not-json",
        '{"entries": []}',
        '{"entries": [{"level": 2, "title": "Bad root", "printed_page": "1"}]}',
        '{"entries": [{"level": 1, "title": "Wrong type", "printed_page": 1}]}',
        '{"entries": [{"level": 1, "title": "   ", "printed_page": null}]}',
        '{"entries": [{"level": "1", "title": "Wrong level type", "printed_page": null}]}',
    ],
)
def test_invalid_output_is_explicit_and_does_not_echo_payload(payload):
    with pytest.raises(TocParseError, match="目次JSONの形式") as exc:
        validate_toc_json(payload)
    assert payload not in str(exc.value)


@pytest.mark.parametrize("options", [{"finish": "length"}, {"refusal": "refused"}])
def test_incomplete_or_refused_response(options):
    with pytest.raises(TocParseError, match="完了しません"):
        OpenAITocParser("", client=FakeClient(**options)).parse("TOC")


def test_api_errors_do_not_expose_credentials_or_body():
    secret = "PRIVATE-PDF-CONTENT-AND-KEY"
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    errors = [
        APIConnectionError(request=request),
        APIStatusError(
            secret,
            response=httpx.Response(429, request=request),
            body={"message": secret},
        ),
    ]
    for error in errors:
        with pytest.raises(TocParseError) as exc:
            OpenAITocParser("", client=FakeClient(error=error)).parse("TOC")
        assert secret not in str(exc.value)
