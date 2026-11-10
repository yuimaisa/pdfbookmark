import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError, APIStatusError, OpenAI

from pdfbookmark.llm import (
    DEEPSEEK_BASE_URL,
    DeepSeekTocParser,
    OpenAITocParser,
    TocParseError,
    validate_toc_json,
)

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
        self.content = json.dumps(TOC, ensure_ascii=False) if content is None else content
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


def test_deepseek_sdk_request_is_compatible_and_sends_only_toc():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "deepseek-flash",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(TOC)},
                    }
                ],
            },
        )

    with OpenAI(
        api_key="deepseek-test-key",
        base_url=DEEPSEEK_BASE_URL,
        http_client=httpx.Client(transport=httpx.MockTransport(respond)),
    ) as client:
        parsed = DeepSeekTocParser("", client=client).parse("TOC PAGE ONE\nTOC PAGE TWO")
    assert [e.level for e in parsed.entries] == [1, 2, 3, 1]
    assert parsed.entries[2].printed_page is None
    assert parsed.entries[-1].printed_page == "A-1"
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == "https://api.deepseek.com/chat/completions"
    assert request.headers["Authorization"] == "Bearer deepseek-test-key"
    payload = json.loads(request.content)
    assert payload["model"] == "deepseek-flash"
    assert payload["messages"][1] == {"role": "user", "content": "TOC PAGE ONE\nTOC PAGE TWO"}
    assert "JSON" in payload["messages"][0]["content"]
    assert '"entries"' in payload["messages"][0]["content"]
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["max_tokens"] == 16000 and payload["temperature"] == 0
    assert "max_completion_tokens" not in payload


def test_deepseek_endpoint_is_not_overridden_by_openai_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://other-provider.invalid/v1")
    parser = DeepSeekTocParser("deepseek-test-key")
    try:
        assert str(parser.client.base_url).rstrip("/") == DEEPSEEK_BASE_URL
    finally:
        parser.client.close()


def test_deepseek_model_is_configurable():
    client = FakeClient()
    DeepSeekTocParser("", "deepseek-v4-pro", client).parse("TOC")
    assert client.calls[0]["model"] == "deepseek-v4-pro"


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


@pytest.mark.parametrize("parser_class", [DeepSeekTocParser, OpenAITocParser])
@pytest.mark.parametrize("options", [{"finish": "length"}, {"refusal": "refused"}])
def test_incomplete_or_refused_response(parser_class, options):
    with pytest.raises(TocParseError, match="完了しません"):
        parser_class("", client=FakeClient(**options)).parse("TOC")


@pytest.mark.parametrize("content", ["", "  \n "])
def test_deepseek_blank_response_is_not_accepted_or_retried(content):
    client = FakeClient(content=content)
    with pytest.raises(TocParseError, match="空の応答"):
        DeepSeekTocParser("", client=client).parse("TOC")
    assert len(client.calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        '{"entries": []}',
        '{"entries":[{"level":1,"title":"Invalid page type","printed_page":4}]}',
    ],
)
def test_deepseek_json_is_validated_locally(content):
    with pytest.raises(TocParseError, match="目次JSONの形式"):
        DeepSeekTocParser("", client=FakeClient(content=content)).parse("TOC")


@pytest.mark.parametrize("text", ["  ", "a" * 120_001])
def test_deepseek_invalid_input_does_not_call_api(text):
    client = FakeClient()
    with pytest.raises(TocParseError):
        DeepSeekTocParser("", client=client).parse(text)
    assert not client.calls


def test_deepseek_requires_its_own_key_and_nonempty_model(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    with pytest.raises(TocParseError, match="DeepSeek APIキー"):
        DeepSeekTocParser("")
    with pytest.raises(TocParseError, match="モデル名"):
        DeepSeekTocParser("deepseek-test-key", " ")


@pytest.mark.parametrize("parser_class", [DeepSeekTocParser, OpenAITocParser])
def test_api_errors_do_not_expose_credentials_or_body(parser_class):
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
            parser_class("", client=FakeClient(error=error)).parse("TOC")
        assert secret not in str(exc.value)


@pytest.mark.parametrize("status, hint", [(401, "APIキー"), (402, "残高"), (429, "利用上限")])
def test_deepseek_api_error_guidance(status, hint):
    request = httpx.Request("POST", DEEPSEEK_BASE_URL + "/chat/completions")
    error = APIStatusError(
        "private server response",
        response=httpx.Response(status, request=request),
        body={"message": "private server response"},
    )
    with pytest.raises(TocParseError, match=hint) as exc:
        DeepSeekTocParser("", client=FakeClient(error=error)).parse("TOC")
    assert "DeepSeek" in str(exc.value) and str(status) in str(exc.value)
    assert "private server response" not in str(exc.value)
