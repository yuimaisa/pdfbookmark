# pdfbookmark

[![Tests](https://github.com/yuimaisa/pdfbookmark/actions/workflows/tests.yml/badge.svg)](https://github.com/yuimaisa/pdfbookmark/actions/workflows/tests.yml)

書籍PDFの目次から、章・節・項の階層を持つブックマークを作成するローカルWebアプリです。日本語UIで目次の解析、移動先の確認・編集、PDFの出力を行えます。

## できること

- DeepSeekで目次テキスト・目次画像を構造化。OpenAIはテキスト入力に対応。
- 本文見出しの検索、印刷ページとPDFページの対応による移動先の推定。
- 階層・見出し・移動先の編集、ページプレビュー、既存ブックマークの置き換え。
- 作業途中のJSON保存・復元と、ブックマーク付きPDFのダウンロード。

| 入力PDF | 目次の解析 | 移動先の設定 |
| --- | --- | --- |
| テキスト付き | テキスト、またはDeepSeekの画像入力 | 本文見出しの照合・ページ対応・手動指定 |
| 画像のみ（未OCR） | DeepSeekの画像入力 | 本文先頭のページ対応、または手動指定 |

ローカルOCRエンジンやモデルのインストールは不要です。本文画像の自動認識は行いません。画像PDFでは、目次の印刷ページと実際のPDFページの対応を指定してください。

## クイックスタート

[pixi](https://pixi.sh/latest/installation/) **0.84.1以上**とGitを用意します。Pythonと依存パッケージはpixiがインストールします。

```bash
git clone https://github.com/yuimaisa/pdfbookmark.git
cd pdfbookmark
pixi install --locked
pixi run --locked start
```

ブラウザで [http://127.0.0.1:8501](http://127.0.0.1:8501) を開きます。アプリは既定でローカル接続のみを受け付けます。起動コマンドはLinux・macOS・Windows共通です。

### APIキーなしで試す

「500ページのサンプルPDFで試す」→「サンプル目次を読み込む（API不要）」→「自動ページ照合を実行」→「ブックマーク付きPDFを生成」の順に操作します。サンプルはプログラムで生成され、実際の書籍やAPIキーは不要です。

### 自分のPDFを解析する

画面左側にDeepSeekのAPIキーを入力します。既定モデルは `deepseek-flash` です。キーは [DeepSeek Platform](https://platform.deepseek.com/) で取得できます。API利用料は各プロバイダーの料金に従います。

環境変数や `.env` でも設定できます。初回のみ `.env.example` をコピーし、空欄に自分のキーを設定してください。既存の `.env` は上書きしないでください。

```bash
# Linux / macOS
cp .env.example .env
```

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

| 変数 | 用途 | 既定値 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeekの認証 | なし |
| `DEEPSEEK_MODEL` | DeepSeekのモデル | `deepseek-flash` |
| `OPENAI_API_KEY` | OpenAI選択時の認証 | なし |
| `OPENAI_MODEL` | OpenAIのテキスト解析モデル | `gpt-4.1-mini` |

`.env` の変更後はアプリを再起動してください。画面の入力値は環境変数より優先され、環境変数は `.env` より優先されます。

## 基本の使い方

1. PDFを読み込み、目次の開始・終了ページを指定して「目次ページを設定」を押します。
2. テキストまたは画像を選び、「LLMで目次を解析」を押します。画像入力はDeepSeekで利用できます。
3. 必要に応じて本文先頭の印刷ページとPDFページの対応を指定し、「自動ページ照合を実行」を押します。
4. 移動先をプレビューし、表を編集した場合は「編集内容を保存」を押します。
5. 未解決の移動先を修正してPDFを生成・ダウンロードします。途中の作業はJSONで保存できます。

画面上のPDFページはすべて1始まりです。例えば印刷1ページがPDF12ページ目なら、その対応を設定します。元のPDFを上書きせず、別のPDFを出力します。

詳細は [使い方](docs/usage.md) を参照してください。APIを使わず、外部で用意した目次JSONを読み込むこともできます。

## データの取り扱い

「LLMで目次を解析」を押すと、目次テキスト、または選択した目次ページの画像を選択中のプロバイダーへ送信します。本文の検索とPDFの出力はローカルで処理します。送信する資料は、利用権限とプロバイダーのデータ取り扱い条件を確認して選んでください。

アプリはPDF・抽出結果をメモリに保持し、ディスクへ自動保存しません。ダウンロードしたPDF・JSONと、自分で設定した `.env` は利用者が管理します。JSONには見出し・候補・照合根拠が含まれるため、書籍や個人情報を含むファイルをIssueやPRに添付しないでください。

## 主な制約

- 認識・照合結果は確認が必要です。画像PDFのページ対応による推定は、本文見出しを検証した結果ではありません。
- 画像解析は1回12ページ・Base64合計24 MiB、テキスト解析は120,000文字までです。長い目次は範囲を絞ってください。
- パスワード保護されたPDFは事前に解除してください。PDFの再保存で電子署名は維持されません。
- 個人のローカル利用を想定しています。認証や複数人向けのセッション保護を備えていないため、そのままインターネットへ公開する構成には対応していません。

詳しい制約と接続エラーの対処は [使い方](docs/usage.md) に記載しています。

## 開発・コントリビューション

```bash
pixi run --locked check
```

通常テストは生成したPDFとAPIモックで動作し、APIキーは不要です。実APIの接続テストは明示的に有効化した場合だけ実行します。

[開発ガイド](docs/development.md) · [コントリビューションガイド](CONTRIBUTING.md) · [不具合報告・提案](https://github.com/yuimaisa/pdfbookmark/issues)

## ライセンス

本プロジェクトのライセンスは現在未設定です。依存する [PyMuPDF / MuPDFはAGPLまたは商用ライセンス](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright)で提供されます。依存ライブラリの利用条件は、それぞれのライセンスに従います。
