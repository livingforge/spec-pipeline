# Changelog — fact-reconcile

バージョニング方針は [GOVERNANCE.md](GOVERNANCE.md)、依存とライセンスは
[dependencies.md](dependencies.md) を参照。

## Unreleased

### Added
- **package-meta の整備**。ライセンス（MIT）・変更履歴・依存・脅威モデルを
  `package-meta/fact-reconcile/`（LICENSE / CHANGELOG.md / dependencies.md /
  GOVERNANCE.md / threat-model.md）へ集約し、スキルの実行時動作に直接関係しない
  ガバナンス/メタ文書を scripts/ から分離した。

## 1.0.0 (2026-07-06)

初回リリース。抽出ファクト（`facts.json`）を contextdb アイテムにする前に、意味的な
一貫性を担保する提案生成ステップを挟む独立スキル。

- **ブロッキング（`blocking`）** — LLM を使わない**決定的**な処理で「同一かもしれない」
  ファクトを束ねる。順序非依存で、投入順に結果が揺れない。
- **LLM 裁定（`adjudicate`）** — 候補クラスタを「同一概念」と「矛盾」に判定し、正規化用の
  `term_map` を提案する。接続設定（プロバイダ・`.env`）は **docsummary を再利用**。
- **`reconcile.json`（レビュー専用）** — `concept` / `contradiction` / `term_map` の提案を
  人がレビューする。**矛盾は自動解決しない**（相反する値は人に委ねる）。
- **mutate プラン（`plan`）** — 承認済み concept を決定的に contextdb の
  `add-item`（`status: review`）へ落とし、`mutate apply --dry-run` で検証してから適用。
- contextdb の構造検証が拾わない**意味的一貫性のギャップ**を埋め、名寄せを順序非依存にする。
- 名寄せ実体（factreconcile パッケージ）のみ同梱し、docextract / docagent / docsummary は
  兄弟スキルを実行時参照で解決（共有 venv を共用）。
