from pathlib import Path

from streamlit.testing.v1 import AppTest

from pdfbookmark.demo import demo_entries
from pdfbookmark.export import export_project, import_project
from pdfbookmark.llm import DeepSeekTocParser, TocParseError
from pdfbookmark.models import Bookmark, PageReference

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


def test_first_page_reference_ui_estimates_and_invalidates_old_results(make_pdf):
    app = AppTest.from_file(str(APP))
    app.session_state["demo_data"] = make_pdf()
    app.run(timeout=30)
    app.session_state["entries"] = [
        Bookmark(level=1, title="Missing first chapter", printed_page="1"),
        Bookmark(level=1, title="Missing later chapter", printed_page="20"),
    ]
    app.run(timeout=30)
    app.checkbox(key="reference_enabled").check()
    app.number_input(key="reference_pdf_page").set_value(12)
    click(app, "ページ対応の設定を反映")
    click(app, "自動ページ照合を実行")
    assert [e.pdf_page for e in app.session_state["entries"]] == [12, 31]
    assert all(e.status == "Estimated" for e in app.session_state["entries"])
    click(app, "ブックマーク付きPDFを生成")
    assert app.session_state["output_pdf"].startswith(b"%PDF")
    # Reapplying identical settings keeps the current results and download valid.
    click(app, "ページ対応の設定を反映")
    assert "output_pdf" in app.session_state
    app.number_input(key="reference_pdf_page").set_value(13)
    click(app, "ページ対応の設定を反映")
    assert all(e.pdf_page is None for e in app.session_state["entries"])
    assert "output_pdf" not in app.session_state
    assert "match_report" not in app.session_state
    click(app, "自動ページ照合を実行")
    assert [e.pdf_page for e in app.session_state["entries"]] == [13, 32]
    app.checkbox(key="reference_enabled").uncheck()
    click(app, "ページ対応の設定を反映")
    click(app, "自動ページ照合を実行")
    assert all(e.status == "Unresolved" for e in app.session_state["entries"])


def test_invalid_reference_keeps_results_and_changes_preserve_manual_entries(make_pdf):
    app = AppTest.from_file(str(APP))
    app.session_state["demo_data"] = make_pdf()
    app.run(timeout=30)
    manual = Bookmark(
        level=1, title="Manual", printed_page="1", pdf_page=12, manual=True, status="Probable"
    )
    app.session_state["entries"] = [manual]
    app.run(timeout=30)
    click(app, "ブックマーク付きPDFを生成")
    app.checkbox(key="reference_enabled").check()
    app.number_input(key="reference_pdf_page").set_value(2)
    click(app, "ページ対応の設定を反映")
    assert any("目次以外" in e.value for e in app.error)
    assert app.session_state["entries"] == [manual]
    assert "output_pdf" in app.session_state
    assert "page_reference" not in app.session_state
    app.number_input(key="reference_pdf_page").set_value(12)
    click(app, "ページ対応の設定を反映")
    assert app.session_state["entries"] == [manual]
    assert "output_pdf" not in app.session_state


def test_restored_reference_populates_ui_and_is_cleared_for_another_pdf(make_pdf):
    data = make_pdf()
    reference = PageReference(printed_page="1", pdf_page=12)
    text = export_project(
        data,
        60,
        [1, 2],
        [Bookmark(level=1, title="Missing chapter", printed_page="20")],
        page_reference=reference,
    )
    app = AppTest.from_file(str(APP))
    app.session_state["demo_data"] = data
    app.run(timeout=30)
    app.session_state["pending_project"] = import_project(text, data, 60)
    app.run(timeout=30)
    assert not app.exception
    assert app.checkbox(key="reference_enabled").value
    assert app.number_input(key="reference_pdf_page").value == 12
    assert app.text_input(key="reference_printed_page").value == "1"
    click(app, "自動ページ照合を実行")
    assert app.session_state["entries"][0].pdf_page == 31
    app.session_state["demo_data"] = make_pdf({4: ["A different book"]}, pages=10)
    app.run(timeout=30)
    assert not app.exception
    assert "page_reference" not in app.session_state
    assert "reference_pdf_page" not in app.session_state


def test_toc_range_change_clears_overlapping_reference(make_pdf):
    app = AppTest.from_file(str(APP))
    app.session_state["demo_data"] = make_pdf()
    app.run(timeout=30)
    app.session_state["entries"] = [Bookmark(level=1, title="Missing chapter", printed_page="1")]
    app.run(timeout=30)
    app.checkbox(key="reference_enabled").check()
    app.number_input(key="reference_pdf_page").set_value(12)
    click(app, "ページ対応の設定を反映")
    app.number_input(key="range_last").set_value(12)
    click(app, "この範囲の目次テキストを抽出")
    assert app.session_state["page_reference"] is None
    assert not app.session_state["reference_enabled"]


def test_image_pdf_vision_ui_sends_only_toc_and_preserves_results_on_error(
    make_image_pdf, monkeypatch
):
    calls = []

    def parse(self, data, pages):
        calls.append((data, pages))
        return demo_entries()

    monkeypatch.setattr(DeepSeekTocParser, "parse_pdf_images", parse)
    data = make_image_pdf(pages=5)
    app = AppTest.from_file(str(APP))
    app.session_state["demo_data"] = data
    app.run(timeout=30)
    assert not app.exception
    assert app.radio(key="toc_input_mode").value == "画像"
    app.sidebar.text_input[0].set_value("unit-test-key").run(timeout=30)
    click(app, "LLMで目次を解析")
    assert calls == [(data, [2, 3])]
    previous = app.session_state["entries"]

    def fail(self, data, pages):
        raise TocParseError("画像解析エラー")

    monkeypatch.setattr(DeepSeekTocParser, "parse_pdf_images", fail)
    click(app, "LLMで目次を解析")
    assert app.session_state["entries"] == previous
    assert any("画像解析エラー" in e.value for e in app.error)


def test_image_ocr_ui_cache_restart_and_no_work_on_editor_rerun(
    make_image_pdf, tmp_path, monkeypatch
):
    from pdfbookmark.ocr import OcrLine, TesseractEngine

    monkeypatch.setenv("PDFBOOKMARK_CACHE_DIR", str(tmp_path))
    calls = []

    def setup(self):
        self.model_version = "test-stable-model"

    def recognize(self, png):
        calls.append(png[:8])
        n = len(calls)
        if n in (4, 5):
            return [
                OcrLine("Foundations" if n == 4 else "Methods", (100, 200, 900, 260), 0.99, 0),
                OcrLine(str(n - 3), (800, 2250, 860, 2300), 0.99, 1),
            ]
        return []

    monkeypatch.setattr(TesseractEngine, "__init__", setup)
    monkeypatch.setattr(TesseractEngine, "recognize", recognize)
    data = make_image_pdf(pages=5)
    for attempt in range(2):
        app = AppTest.from_file(str(APP))
        app.session_state["demo_data"] = data
        app.run(timeout=30)
        app.session_state["entries"] = [
            Bookmark(level=1, title="Foundations", printed_page="1"),
            Bookmark(level=2, title="Methods", printed_page="2"),
        ]
        app.run(timeout=30)
        app.selectbox(key="ocr_mode").select("全文OCR").run(timeout=30)
        click(app, "OCRを実行・再開")
        assert len(calls) == 5
        assert not app.session_state["ocr_plan"].active
        assert app.session_state["ocr_plan"].completed == 5
        assert app.session_state["ocr_plan"].cache_hits == (5 if attempt else 0)
        app.selectbox(key="review_filter").select("未解決のみ").run(timeout=30)
        assert not app.exception and len(calls) == 5


def test_ocr_job_can_be_stopped_without_discarding_pending_pages(make_image_pdf, monkeypatch):
    import streamlit

    import pdfbookmark.ocr
    from pdfbookmark.ocr import OcrBatch
    from pdfbookmark.ocr_workflow import OcrPlan

    app = AppTest.from_file(str(APP))
    app.session_state["demo_data"] = make_image_pdf(pages=5)
    app.run(timeout=30)
    app.session_state["entries"] = [Bookmark(level=1, title="Absent", printed_page="1")]
    app.run(timeout=30)
    # Render an active batch boundary without automatically advancing the job.
    monkeypatch.setattr(
        pdfbookmark.ocr, "ocr_pages", lambda data, index, *a, **kw: OcrBatch(index, [], [], 0)
    )
    monkeypatch.setattr(streamlit, "rerun", lambda: None)
    app.session_state["ocr_plan"] = OcrPlan(pending=[4, 5], active=True)
    app.session_state["ocr_engine"] = object()
    app.run(timeout=30)
    button = next(b for b in app.button if b.label == "OCRを中断")
    button.click().run(timeout=30)
    assert not app.exception
    assert not app.session_state["ocr_plan"].active
    assert app.session_state["ocr_plan"].pending == [4, 5]
