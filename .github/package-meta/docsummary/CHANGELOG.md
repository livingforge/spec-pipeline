# Changelog — docsummary

バージョニング方針は [GOVERNANCE.md](GOVERNANCE.md)、依存とライセンスは
[dependencies.md](dependencies.md) を参照。

## Unreleased

### Added
- **package-meta の整備**。ライセンス（MIT）・変更履歴・依存・脅威モデルを
  `package-meta/docsummary/`（LICENSE / CHANGELOG.md / dependencies.md /
  GOVERNANCE.md / threat-model.md）へ集約し、スキルの実行時動作に直接関係しない
  ガバナンス/メタ文書を scripts/ から分離した。

## 1.0.0 (2026-07-05)

初回リリース。docextract → docagent で索引化した文書を LLM で要約する独立スキル。

- **LLM 要約**。docagent の `library.json` を入力に、同梱の要約フォーマット
  （メタデータ表 + カテゴリ + 本文の固定構造）に従って要約し、
  `.docextract/summaries/<doc_id>.md` と集約 `store/summaries.json` に保存する。
  利用者が定義するのは**視点**（`summary_guide.md`）と**カテゴリ分類**
  （`summary_categories.json`。LLM が 1 つ選ぶ）。
- **プロバイダ**。OpenAI / Azure OpenAI / Gemini / Anthropic に対応。**標準ライブラリ
  のみ**（`urllib`）で呼び出し、SDK に依存しない。API キーは `.env`・環境変数で渡し、
  コード・ストア・ログには保存しない。
- **対象選択**。文書 ID / 元フォルダ（`--dir`）/ 未要約・陳腐化（`--pending`）/ 全件
  （`--all`）。鮮度は内容ハッシュ + 仕様ハッシュで追跡。
- **独立スキル化**。要約の実体（docsummary パッケージ）のみ同梱し、依存する
  docextract / docagent と requirements は兄弟スキル docextract を実行時参照で解決
  （重複同梱を廃止、共有 venv を共用）。
