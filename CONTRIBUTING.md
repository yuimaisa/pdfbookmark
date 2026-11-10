# コントリビューションガイド

不具合報告・改善提案は [Issues](https://github.com/yuimaisa/pdfbookmark/issues)、コード・ドキュメントの変更はPull Requestで受け付けます。UIとドキュメントは日本語を基本としています。

## 開発環境

Gitと [pixi 0.84.1以上](https://pixi.sh/latest/installation/) を用意し、リポジトリをクローンして実行します。

```bash
pixi install --locked
pixi run --locked start
pixi run --locked check
```

通常のテスト・サンプル操作にAPIキーや実際の書籍は必要ありません。詳しい構成と実APIテストの実行方法は [開発ガイド](docs/development.md) を参照してください。

## 不具合報告

期待した動作・実際の動作・再現手順・OS・`pixi --version`・使用したモデルを記載してください。PDFに依存する問題は、可能であれば最小限の生成PDFや匿名化した目次JSONで再現してください。

APIキー、`.env`、実際の書籍PDF、本文・個人情報を含むJSONやログを公開のIssue・PRに貼らないでください。APIエラーを共有する場合は、HTTPステータスと画面上のエラーメッセージだけを記載してください。

## Pull Request

1. 変更の目的と、利用者にとっての動作の変化を説明してください。
2. 変更に応じた確認を行い、実行したコマンドと結果を記載してください。動作の変更には必要なテストを追加し、ドキュメントのみの変更には不要なテストを追加しないでください。
3. APIリクエストは通常テストではモックし、有料の実API呼び出しを必須にしないでください。
4. 依存関係を変更した場合は `pixi lock` で更新し、`pyproject.toml` と `pixi.lock` を同じPRに含めてください。
5. 合成したテストデータを使い、APIキー・利用者の資料・ローカル環境の生成物をコミットしないでください。

現在のライセンス状態は [README](README.md#ライセンス) に記載しています。
