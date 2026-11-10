from __future__ import annotations

import json
import os
from hashlib import sha256
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv
from pydantic import ValidationError

from pdfbookmark.demo import demo_entries, demo_pdf
from pdfbookmark.editing import merge_edits
from pdfbookmark.export import export_pdf, export_project, import_project
from pdfbookmark.llm import (
    DEEPSEEK_DEFAULT_MODEL,
    DeepSeekTocParser,
    OpenAITocParser,
    TocParseError,
    validate_toc_json,
)
from pdfbookmark.matching import match_entries
from pdfbookmark.models import Bookmark, PageReference
from pdfbookmark.pdf import extract_index, render_page, toc_text

load_dotenv(Path(__file__).with_name(".env"))
st.set_page_config(page_title="pdfbookmark | 目次からブックマークへ", page_icon="📖", layout="wide")


@st.cache_data(max_entries=2, ttl=3600, show_spinner=False)
def cached_index(data: bytes):
    # Cached functions must have no UI side effects: replaying callbacks to an
    # external progress widget breaks when another session opens the same PDF.
    return extract_index(data)


@st.cache_data(max_entries=12, ttl=600, show_spinner=False)
def cached_preview(data: bytes, page: int):
    return render_page(data, page)


def invalidate_output():
    st.session_state.pop("output_pdf", None)


def replace_entries(entries):
    st.session_state.entries = entries
    st.session_state.editor_version = st.session_state.get("editor_version", 0) + 1
    st.session_state.pop("match_report", None)
    invalidate_output()


def set_page_reference(reference):
    """A changed page map invalidates automatic results, preserving manual edits."""
    if reference == st.session_state.get("page_reference"):
        return
    st.session_state.page_reference = reference
    replace_entries(
        [
            entry
            if entry.manual
            else Bookmark(**entry.model_dump(include={"id", "level", "title", "printed_page"}))
            for entry in st.session_state.entries
        ]
    )


def clear_demo():
    st.session_state.pop("demo_data", None)


def error_message(exc):
    if isinstance(exc, ValidationError):
        st.error("入力の形式が不正です。見出し・階層・ページ番号・JSONの型を確認してください。")
    else:
        st.error(str(exc))


def main():
    st.title("pdfbookmark")
    st.caption("テキスト付きの書籍PDFに、確かな移動先と階層付きブックマークを。")
    notice = st.empty()
    if message := st.session_state.pop("notice", None):
        notice.success(message)
    with st.sidebar:
        st.header("目次解析の設定")
        provider = st.selectbox("LLMプロバイダー", ["DeepSeek", "OpenAI"], key="llm_provider")
        parser_class = DeepSeekTocParser if provider == "DeepSeek" else OpenAITocParser
        prefix = provider.upper()
        api_key = st.text_input(
            f"{provider} APIキー",
            type="password",
            value=os.getenv(f"{prefix}_API_KEY", ""),
            key=f"{prefix}_api_key",
        )
        default_model = DEEPSEEK_DEFAULT_MODEL if provider == "DeepSeek" else "gpt-4.1-mini"
        model = st.text_input(
            "モデル", value=os.getenv(f"{prefix}_MODEL") or default_model, key=f"{prefix}_model"
        )
        if provider == "DeepSeek":
            st.caption("DeepSeekのJSON出力で解析します。推論モードは無効です。")
        else:
            st.caption("Structured Outputs対応モデルを指定してください。")
        st.info(
            f"{provider}に送信するのは、画面で確認した目次テキストだけです。"
            "本文の照合はローカルで行います。"
        )
        st.caption(
            "入力したAPIキー・PDF・抽出テキストをアプリからファイルやログに保存しません。"
            "中間JSONには見出しが含まれます。"
        )
        st.markdown("**状態の読み方**")
        st.caption(
            "Verified：本文見出しの強い一致\n\nProbable：曖昧一致＋ページ対応、または手動指定"
            "\n\nEstimated：ページ対応だけの推定\n\nUnresolved：移動先が未確定"
        )
    st.subheader("1. PDFを読み込む")
    upload = st.file_uploader("テキスト付きPDF", type=["pdf"], on_change=clear_demo)
    if st.button("500ページのサンプルPDFで試す"):
        st.session_state.demo_data = demo_pdf()
    if st.session_state.get("demo_data"):
        data, name = st.session_state.demo_data, "sample-textbook.pdf"
        demo = True
    elif upload:
        data, name = upload.getvalue(), Path(upload.name).name
        demo = False
    else:
        st.info("PDFを選択するか、サンプルで操作を試してください。元のPDFは変更しません。")
        return
    digest = sha256(data).hexdigest()
    if st.session_state.get("active_digest") != digest:
        progress = st.progress(0.0, text="テキストレイヤーを抽出しています…")
        try:
            index = cached_index(data)
        except ValueError as exc:
            progress.empty()
            error_message(exc)
            return
        progress.empty()
        # Commit document state only after the new PDF was opened successfully.
        for key in list(st.session_state):
            if key.startswith(("editor_", "preview_", "reference_")) or key in {
                "entries",
                "output_pdf",
                "match_report",
                "toc_text",
                "range_first",
                "range_last",
                "pending_project",
                "overwrite",
                "detail_entry",
                "review_filter",
                "page_reference",
            }:
                del st.session_state[key]
        st.session_state.index = index
        st.session_state.active_digest = digest
        st.session_state.entries = []
        first, last = (2, 3) if demo else (1, min(3, index.page_count))
        st.session_state.range_first, st.session_state.range_last = first, last
        st.session_state.toc_pages = list(range(first, last + 1))
        st.session_state.toc_text = toc_text(index, first, last)
    index = st.session_state.index
    restore_notice = st.empty()
    if project := st.session_state.pop("pending_project", None):
        replace_entries(project.entries)
        st.session_state.toc_pages = project.toc_pages
        reference = project.page_reference
        st.session_state.page_reference = reference
        st.session_state.reference_enabled = reference is not None
        st.session_state.reference_pdf_page = (
            reference.pdf_page if reference else min(index.page_count, max(project.toc_pages) + 1)
        )
        st.session_state.reference_printed_page = reference.printed_page if reference else "1"
        if project.toc_pages:
            first, last = min(project.toc_pages), max(project.toc_pages)
            st.session_state.range_first, st.session_state.range_last = first, last
            st.session_state.toc_text = toc_text(index, first, last)
        restore_notice.success("中間結果を復元しました。")
    st.write(f"**{name}**")
    a, b, c = st.columns(3)
    a.metric("総ページ数", index.page_count)
    b.metric("テキストを含むページ", index.text_page_count)
    c.metric("既存ブックマーク", len(index.existing_toc))
    if index.existing_toc:
        with st.expander("既存ブックマークを確認"):
            st.dataframe(index.existing_toc, column_order=[0, 1, 2], hide_index=True)
    if not index.text_page_count:
        st.error("テキストレイヤーが見つかりません。別ソフトでOCRしてから読み込んでください。")
        st.image(cached_preview(data, 1), caption="PDFページ 1", width=650)
        return
    if index.text_page_count < index.page_count * 0.5:
        st.warning(
            "テキストを持つページが半数未満です。"
            "必要なページを別ソフトでOCRしてから読み込んでください。"
        )
    st.subheader("2. 目次ページを指定する")
    st.caption("画面上のページ番号はすべて1始まりです。目次ページは本文検索から除外します。")
    with st.form("toc_range"):
        a, b = st.columns(2)
        a.number_input(
            "開始PDFページ", min_value=1, max_value=index.page_count, key="range_first", step=1
        )
        b.number_input(
            "終了PDFページ", min_value=1, max_value=index.page_count, key="range_last", step=1
        )
        extract = st.form_submit_button("この範囲の目次テキストを抽出")
    if extract:
        try:
            text = toc_text(index, st.session_state.range_first, st.session_state.range_last)
            pages = list(range(st.session_state.range_first, st.session_state.range_last + 1))
            st.session_state.toc_text = text
            if pages != st.session_state.toc_pages:
                replace_entries([])
            st.session_state.toc_pages = pages
            reference = st.session_state.get("page_reference")
            if reference is not None and reference.pdf_page in pages:
                set_page_reference(None)
                st.session_state.reference_enabled = False
                st.warning("基準ページが目次範囲に入ったため、本文先頭のページ対応を解除しました。")
        except ValueError as exc:
            error_message(exc)
    st.caption("現在の目次PDFページ：" + ", ".join(map(str, st.session_state.toc_pages)))
    st.text_area("目次テキスト（送信前に編集できます）", key="toc_text", height=230)
    with st.expander("目次のページプレビュー"):
        page = st.selectbox("目次プレビューのPDFページ", st.session_state.toc_pages or [1])
        if st.checkbox("目次画像を表示"):
            st.image(cached_preview(data, page), caption=f"PDFページ {page}", width=700)
    st.subheader("3. 目次を構造化する")
    st.caption("解析ボタンを押したときだけAPIを呼び出します。既存の編集結果は置き換わります。")
    if st.button("LLMで目次を解析", type="primary", disabled=not bool(api_key.strip())):
        try:
            with st.spinner("目次を解析しています…"):
                parsed = parser_class(api_key, model).parse(st.session_state.toc_text)
            replace_entries([Bookmark(**e.model_dump()) for e in parsed.entries])
            st.success(f"目次を{len(parsed.entries)}件抽出しました。")
        except (TocParseError, ValueError) as exc:
            error_message(exc)
    if demo and st.button("サンプル目次を読み込む（API不要）"):
        replace_entries([Bookmark(**e.model_dump()) for e in demo_entries().entries])
    with st.expander("JSONをインポート（中間結果・構造化した目次）", expanded=True):
        st.caption(
            "中間結果は同じPDFにだけ復元できます。目次JSONの形式はREADMEを参照してください。"
        )
        json_file = st.file_uploader("JSONファイル", type=["json"], key=f"json_{digest}")
        if st.button("JSONを読み込む", disabled=json_file is None):
            try:
                text = json_file.getvalue().decode("utf-8-sig")
                value = json.loads(text)
                if isinstance(value, dict) and "document_sha256" in value:
                    st.session_state.pending_project = import_project(text, data, index.page_count)
                    st.rerun()
                parsed = validate_toc_json(text)
                replace_entries([Bookmark(**e.model_dump()) for e in parsed.entries])
                st.success("目次JSONを読み込みました。")
            except (ValueError, UnicodeError) as exc:
                error_message(exc)
    entries = st.session_state.entries
    if not entries:
        st.info("目次を解析するかJSONを読み込むと、自動照合・編集に進めます。")
        return
    st.subheader("4. 本文とページ番号を照合する")
    st.caption(
        "本文先頭の印刷ページがPDFの何ページ目か分かる場合は、ページ対応を指定できます。"
        "例：印刷1ページがPDF12ページ目なら、目次の印刷ページに11を足して推定します。"
    )
    st.session_state.setdefault(
        "reference_pdf_page", min(index.page_count, max(st.session_state.toc_pages) + 1)
    )
    st.session_state.setdefault("reference_printed_page", "1")
    with st.form("page_reference_form"):
        st.checkbox("本文先頭のページ対応を使う", key="reference_enabled")
        a, b = st.columns(2)
        a.number_input(
            "本文先頭のPDFページ",
            min_value=1,
            max_value=index.page_count,
            step=1,
            key="reference_pdf_page",
        )
        b.text_input("そのページの印刷ページ番号", key="reference_printed_page")
        st.caption("印刷ページは数字・ローマ数字・付録番号（例：1、i、A-1）に対応します。")
        save_reference = st.form_submit_button("ページ対応の設定を反映")
    if save_reference:
        try:
            reference = None
            if st.session_state.reference_enabled:
                reference = PageReference(
                    pdf_page=st.session_state.reference_pdf_page,
                    printed_page=st.session_state.reference_printed_page,
                )
                reference.validate_location(index.page_count, st.session_state.toc_pages)
            set_page_reference(reference)
            entries = st.session_state.entries
            st.success("ページ対応の設定を反映しました。自動ページ照合を実行してください。")
        except ValueError as exc:
            error_message(exc)
    reference = st.session_state.get("page_reference")
    if reference is not None:
        st.info(
            f"使用中の基準：印刷ページ {reference.printed_page} → PDFページ {reference.pdf_page}。"
            "同じ番号体系の項目に適用し、途中のずれは本文見出しとの照合で確認します。"
        )
        if st.checkbox("本文先頭の画像を確認", key="reference_preview"):
            st.image(
                cached_preview(data, reference.pdf_page),
                caption=f"PDFページ {reference.pdf_page} / 印刷ページ {reference.printed_page}",
                width=700,
            )
    keep_manual = st.checkbox("再照合時に手動指定した移動先を保持", value=True)
    if st.button("自動ページ照合を実行"):
        progress = st.progress(0.0, text="本文見出しを検索し、ページ対応を推定しています…")
        try:
            targets = (
                entries
                if keep_manual
                else [e.model_copy(update={"manual": False}) for e in entries]
            )
            report = match_entries(
                index,
                targets,
                set(st.session_state.toc_pages),
                progress=lambda v: progress.progress(v),
                page_reference=reference,
            )
            replace_entries(report.entries)
            st.session_state.match_report = report
            entries = report.entries
        except ValueError as exc:
            error_message(exc)
        finally:
            progress.empty()
    if report := st.session_state.get("match_report"):
        st.caption(
            f"高信頼度アンカー {report.anchor_count}件 / "
            f"推定されたオフセット区間 {len(report.page_map.segments)}件"
        )
        for warning in report.warnings:
            st.warning(warning)
        with st.expander("ページ対応の推定根拠"):
            st.dataframe([vars(s) for s in report.page_map.segments], hide_index=True)
    st.subheader("5. 確認・編集する")
    cols = st.columns(4)
    for col, status in zip(cols, ("Verified", "Probable", "Estimated", "Unresolved"), strict=False):
        col.metric(status, sum(e.status == status for e in entries))
    mode = st.selectbox("表示する項目", ["すべて", "未解決のみ", "要確認のみ"], key="review_filter")
    visible = [
        e
        for e in entries
        if mode == "すべて"
        or (mode == "未解決のみ" and e.status == "Unresolved")
        or (mode == "要確認のみ" and e.status != "Verified")
    ]
    st.caption(
        "表の末尾で項目を追加、行選択で削除できます。変更後は「編集内容を保存」を押してください。"
        "見出し・印刷ページを変えた自動判定項目は再照合が必要です。"
    )
    records = [
        e.model_dump(include={"id", "level", "title", "printed_page", "pdf_page", "status"})
        for e in visible
    ]
    frame = pd.DataFrame(
        records, columns=["id", "level", "title", "printed_page", "pdf_page", "status"]
    )
    frame["pdf_page"] = pd.array(frame["pdf_page"], dtype="Int64")
    frame["level"] = pd.array(frame["level"], dtype="Int64")
    with st.form("edit_form"):
        edited = st.data_editor(
            frame,
            hide_index=True,
            num_rows="dynamic",
            width="stretch",
            key=f"editor_{st.session_state.get('editor_version', 0)}_{mode}",
            disabled=["id", "status"],
            column_config={
                "id": None,
                "level": st.column_config.NumberColumn(
                    "階層", min_value=1, max_value=20, step=1, required=True
                ),
                "title": st.column_config.TextColumn("見出し", required=True, width="large"),
                "printed_page": st.column_config.TextColumn("印刷ページ"),
                "pdf_page": st.column_config.NumberColumn(
                    "PDFページ", min_value=1, max_value=index.page_count, step=1
                ),
                "status": st.column_config.TextColumn("検証状態"),
            },
        )
        save = st.form_submit_button("編集内容を保存")
    if save:
        try:
            # Convert pandas NA to None before validation.
            rows = edited.astype(object).where(pd.notna(edited), None).to_dict("records")
            replace_entries(merge_edits(entries, {e.id for e in visible}, rows, index.page_count))
            st.session_state.notice = "編集内容を保存しました。"
            st.rerun()
        except ValueError as exc:
            error_message(exc)
    if entries:
        left, right = st.columns([1, 1])
        with left:
            by_id = {e.id: e for e in entries}
            selected_id = st.selectbox(
                "根拠・プレビューを確認する項目",
                list(by_id),
                format_func=lambda key: f"{by_id[key].title} [{by_id[key].status}]",
                key="detail_entry",
            )
            selected = by_id[selected_id]
            st.write(f"信頼度：{selected.confidence:.0%}（確率ではなく照合スコアです）")
            for reason in selected.reasons:
                st.write("・" + reason)
            if selected.candidates:
                st.dataframe(
                    [
                        {
                            "候補PDFページ": c.pdf_page,
                            "スコア": round(c.score, 3),
                            "根拠": " / ".join(c.reasons),
                        }
                        for c in selected.candidates
                    ],
                    hide_index=True,
                    width="stretch",
                )
        with right:
            preview = st.number_input(
                "プレビューするPDFページ",
                min_value=1,
                max_value=index.page_count,
                value=selected.pdf_page or 1,
                step=1,
                key=f"preview_{selected.id}_{selected.pdf_page}",
            )
            if st.checkbox("対象ページの画像を表示", value=True):
                st.image(
                    cached_preview(data, preview), caption=f"PDFページ {preview}", width="stretch"
                )
    st.subheader("6. 保存・ダウンロード")
    st.download_button(
        "中間結果JSONをダウンロード",
        export_project(
            data,
            index.page_count,
            st.session_state.toc_pages,
            entries,
            page_reference=reference,
        ),
        file_name=f"{Path(name).stem}.bookmarks.json",
        mime="application/json",
        on_click="ignore",
    )
    if any(e.status in {"Estimated", "Probable"} for e in entries):
        st.warning(
            "推定・手動指定の移動先が含まれます。必要なページをプレビューして確認してください。"
        )
    unresolved = any(e.pdf_page is None or e.status == "Unresolved" for e in entries)
    if unresolved:
        st.info("未解決項目の移動先を指定するか、その項目を削除するとPDFを生成できます。")
    overwrite = False
    if index.existing_toc:
        overwrite = st.checkbox(
            "出力PDFの既存ブックマークを今回の内容で置き換える", key="overwrite"
        )
    if st.button("ブックマーク付きPDFを生成", type="primary", disabled=unresolved or not entries):
        try:
            with st.spinner("PDFを生成し、保存後のブックマークを再検証しています…"):
                st.session_state.output_pdf = export_pdf(
                    data, entries, overwrite_existing=overwrite
                )
            st.success(f"{len(entries)}件の階層・移動先を再検証しました。ダウンロードできます。")
        except ValueError as exc:
            error_message(exc)
    if output := st.session_state.get("output_pdf"):
        st.download_button(
            "PDFをダウンロード",
            output,
            file_name=f"{Path(name).stem}_bookmarked.pdf",
            mime="application/pdf",
            on_click="ignore",
        )


if __name__ == "__main__":
    main()
