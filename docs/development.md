# 開発ガイド

[READMEへ戻る](../README.md) · [コントリビューション](../CONTRIBUTING.md)

## 環境

Pythonと依存パッケージはpixiで管理します。Python 3.12が既定で、3.11 / 3.13のテスト環境もあります。Linux（x86_64 / aarch64）、macOS（Apple Silicon / Intel）、Windows向けの依存関係をロックしています。CIはLinux上のPython 3.11 / 3.12 / 3.13で実行します。ロック対象の全OS・アーキテクチャで動作を保証するものではありません。

```bash
pixi install --locked
pixi run --locked check
```

## テスト

```bash
pixi run --locked check
# 個別に実行する場合
pixi run --locked test
pixi run --locked lint
pixi run --locked format-check
# 自動整形
pixi run --locked format
```

`check` はlint・整形チェック・通常テストをまとめて実行します。Python 3.11 / 3.13でも確認する場合は、それぞれ `pixi run --locked -e py311 check` / `pixi run --locked -e py313 check` を使います。CIでは同じ3環境を `--locked` で実行し、ロックファイルと依存定義のずれを検出します。

テスト時にPDFをプログラムで生成します。実際の書籍やAPIキーは不要です。単純・区間別オフセット、章扉、重複見出し、表記揺れ・軽微なOCR誤認、ローマ数字・付録、複数目次ページ、未発見項目、誤アンカー、柱・相互参照、途中開始の見出し、フィルター付き編集、JSON復元、既存アウトライン確認、出力の内容保持を検証します。Streamlit AppTestで500ページの読込→照合→生成と、画像だけのPDFの目次解析→手動基準によるページ対応→画像を保持したPDF生成を通します。GitHub ActionsはPython 3.11 / 3.12 / 3.13でこのテストを実行します。

API呼び出しの通常テストは偽クライアントとHTTPモックを使い、DeepSeekへの送信先・JSON出力・推論無効・送信範囲・階層検証・空の応答・APIエラーを検証します。UIの初期選択とプロバイダーごとのキー分離も確認します。**実APIテストは明示的な有効化が必要**です。

```bash
# .env または環境変数に DEEPSEEK_API_KEY を設定してから実行（API利用料が発生）
RUN_DEEPSEEK_TESTS=1 pixi run --locked test-deepseek
```

この接続テストは人工的な日本語目次テキストと生成した目次画像を送信し、階層・見出し・印刷ページを検証します。実際のPDF本文は送りません。PowerShellでは次のように実行します。

```powershell
$env:RUN_DEEPSEEK_TESTS = "1"
pixi run --locked test-deepseek
```

APIキー未設定、または `RUN_DEEPSEEK_TESTS=1` が未指定ならスキップします。OpenAIの任意接続テストは `RUN_OPENAI_TESTS=1 pixi run --locked test-openai` で実行できます（`OPENAI_API_KEY` 環境変数が必要）。

## 依存関係の更新

`pyproject.toml` を変更した場合は `pixi lock` を実行し、`pixi.lock` も同じPRに含めてください。通常の起動・CIでは `--locked` を使用して、依存定義とのずれを検出します。環境は `.pixi/` に作成されます。

## 構成と拡張

```text
pyproject.toml                Python依存定義・pixi環境・実行タスク
pixi.lock                     各OS・Python環境の依存バージョン固定
app.py                        日本語Streamlit UI・セッション・メモリキャッシュ
pdfbookmark/pdf.py            PDF抽出・位置情報・オンデマンドプレビュー
pdfbookmark/llm.py            目次テキスト・DeepSeek画像解析・JSON検証
pdfbookmark/models.py         目次・結果・中間JSONの検証
pdfbookmark/normalization.py   検索用正規化・番号体系
pdfbookmark/matching.py        見出し索引・段階的照合・区間別オフセット
pdfbookmark/editing.py         絞り込み結果の安全な編集・マージ
pdfbookmark/export.py          PDF生成・再検証・JSON保存復元
pdfbookmark/demo.py            人工的な500ページ書籍
tests/                        生成PDF・LLMモック・UI・任意の実APIテスト
```

LLMは `TocParser.parse(text) -> TocDocument` を実装して差し替えられます。UIではDeepSeek（初期選択）とOpenAIを切り替えられます。DeepSeekは `parse_images(images)` / `parse_pdf_images(data, pages)` による目次画像解析にも対応します。
