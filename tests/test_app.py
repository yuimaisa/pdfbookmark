from pathlib import Path

from streamlit.testing.v1 import AppTest

from pdfbookmark.demo import demo_entries
from pdfbookmark.llm import DeepSeekTocParser, TocParseError

APP = Path(__file__).resolve().parents[1] / "app.py"


def click(app, label):
    next(b for b in app.button if b.label == label).click().run(timeout=30)
    assert not app.exception


def test_offline_ui_flow_and_cached_index():
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception
    click(app, "500ページのサンプルPDFで試す")
    index = app.session_state["index"]
    assert index.page_count == 500
    click(app, "サンプル目次を読み込む（API不要）")
    click(app, "自動ページ照合を実行")
    assert [e.pdf_page for e in app.session_state["entries"]] == [
        4,
        6,
        13,
        16,
        30,
        213,
        223,
        253,
        413,
    ]
    click(app, "ブックマーク付きPDFを生成")
    assert app.session_state["output_pdf"].startswith(b"%PDF")
    assert app.session_state["index"] is index
    app.selectbox(key="review_filter").select("未解決のみ").run(timeout=30)
    assert not app.exception
    assert app.session_state["output_pdf"].startswith(b"%PDF")


def test_invalid_page_range_is_shown_without_crash():
    app = AppTest.from_file(str(APP)).run(timeout=30)
    click(app, "500ページのサンプルPDFで試す")
    app.number_input(key="range_first").set_value(8)
    app.number_input(key="range_last").set_value(3)
    click(app, "この範囲の目次テキストを抽出")
    assert any("範囲が不正" in e.value for e in app.error)


def test_llm_action_sends_only_selected_toc_and_keeps_results_on_error(monkeypatch):
    calls = []

    def parse(self, text):
        calls.append(text)
        return demo_entries()

    monkeypatch.setattr(DeepSeekTocParser, "parse", parse)
    app = AppTest.from_file(str(APP)).run(timeout=30)
    click(app, "500ページのサンプルPDFで試す")
    app.sidebar.text_input[0].set_value("unit-test-key").run(timeout=30)
    click(app, "LLMで目次を解析")
    assert len(app.session_state["entries"]) == 9
    assert len(calls) == 1
    assert calls[0] == app.session_state["toc_text"]
    assert "Sample body paragraph" not in calls[0]
    click(app, "自動ページ照合を実行")
    assert len(calls) == 1
    previous = app.session_state["entries"]

    def fail(self, text):
        raise TocParseError("テスト解析エラー")

    monkeypatch.setattr(DeepSeekTocParser, "parse", fail)
    click(app, "LLMで目次を解析")
    assert app.session_state["entries"] == previous
    assert any("テスト解析エラー" in e.value for e in app.error)


def test_invalid_new_pdf_does_not_destroy_previous_document_state(make_pdf):
    app = AppTest.from_file(str(APP)).run(timeout=30)
    good = make_pdf({11: ["Foundations"]})
    app.session_state["demo_data"] = good
    app.run(timeout=30)
    assert not app.exception
    old_index = app.session_state["index"]
    app.session_state["demo_data"] = b"invalid pdf"
    app.run(timeout=30)
    assert not app.exception
    assert any("開けません" in e.value for e in app.error)
    app.session_state["demo_data"] = good
    app.run(timeout=30)
    assert not app.exception
    assert app.session_state["index"] is old_index
    assert app.session_state["entries"] == []


def test_cached_document_can_be_opened_in_a_fresh_session(make_pdf):
    data = make_pdf({11: ["Cache replay regression"]})
    for _ in range(2):
        app = AppTest.from_file(str(APP))
        app.session_state["demo_data"] = data
        app.run(timeout=30)
        assert not app.exception
        assert app.session_state["index"].page_count == 60


def test_deepseek_is_default_and_provider_keys_are_kept_separate(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("OPENAI_MODEL", "openai-test-model")
    app = AppTest.from_file(str(APP)).run(timeout=30)
    assert not app.exception
    assert app.selectbox(key="llm_provider").value == "DeepSeek"
    assert app.text_input(key="DEEPSEEK_api_key").value == "deepseek-test-key"
    assert app.text_input(key="DEEPSEEK_model").value == "deepseek-v4-pro"
    app.selectbox(key="llm_provider").select("OpenAI").run(timeout=30)
    assert not app.exception
    assert app.text_input(key="OPENAI_api_key").value == "openai-test-key"
    assert app.text_input(key="OPENAI_model").value == "openai-test-model"


def test_openai_key_does_not_enable_deepseek_action(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    app = AppTest.from_file(str(APP)).run(timeout=30)
    click(app, "500ページのサンプルPDFで試す")
    assert next(b for b in app.button if b.label == "LLMで目次を解析").disabled
