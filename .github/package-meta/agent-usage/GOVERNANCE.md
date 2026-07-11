# Governance — agent-usage

このスキルのバージョニング・互換性・廃止（deprecation）方針。
変更履歴は [CHANGELOG.md](CHANGELOG.md)、依存とライセンスは
[dependencies.md](dependencies.md)、脅威と防御は [threat-model.md](threat-model.md)
を参照。

## バージョニング（SemVer）

[Semantic Versioning 2.0.0](https://semver.org/) に準じる。追加依存を持たない
自己完結スキルのため、互換性の対象は「入力ログの読み取り」「CLI」「出力の構造」の
3 面。

- **MAJOR** — 後方非互換な変更（`summary.json` スキーマの破壊的変更、`--agent` 既定
  対象の変更、既存 CLI フラグの意味変更、対応ログ形式の削除）。
- **MINOR** — 後方互換な機能追加（新しい `--agent` 対象、新しい CLI フラグ、
  `summary.json` への任意フィールド追加、新しいログ形式への対応）。
- **PATCH** — 後方互換なバグ修正・集計精度の改善・単価表更新・ドキュメント修正。

`0.y.z`（初期開発期）の間は MINOR で非互換が入りうる。

## 公開インターフェースと互換性の範囲

安定を保証する面（＝互換性ポリシーの対象）:

- CLI: `report` サブコマンドと既存フラグ（`--agent` `--claude-dir` `--pricing`
  `--out` `--days` `--since` `--until` `--project` `--top` `--json-only`、Copilot 用の
  `--storage-root` `--workspace` `--workspace-id`）。
- 出力: `summary.json` のトップレベル構造（`totals` / `by_model` / `by_project` /
  `by_day` / `by_agent` / `by_tool` / `top_sessions` / `cost_unit`）と
  `conversations.csv` の列。`report.html` は人間向けビューで、構造そのものは契約対象外。
- 単価表: `pricing.json` のスキーマ（モデル名 → 単価）。

`_` 始まりの内部関数・`collect.py` / `copilot_collect.py` / `render.py` 内部の
補助構造は**非公開**で、予告なく変わる。

## 廃止（Deprecation）方針

後方非互換にしたい面は、**削除前に最低 1 つの MINOR リリースで非推奨**として残す。
CHANGELOG に `Deprecated` として代替手段とともに明記し、実際の削除は次の **MAJOR**
で行う。`summary.json` を読む側は未知の新しいフィールドを無視できる前提で作る。

## 棚卸し（inventory）とレビュー周期

| 対象 | 周期 | 作業 |
|---|---|---|
| 単価表（`pricing.json`） | モデル/価格改定時 + 四半期ごと | 最新の公開価格と突合し `verified` を更新（未検証は HTML に警告が出る）。表に無いモデルはコスト unknown（`*`）で出る |
| 対応ログ形式 | Claude Code / VS Code Copilot のログ形式変更時 | セッション JSONL・Agent Debug Log のスキーマ変化に追随（`collect.py` / `copilot_collect.py`） |
| 脅威モデル（`threat-model.md`） | 半期ごと + 出力・入力面の追加時 | 脅威・防御・検証テストの対応表を更新 |

- 棚卸しの結果は CHANGELOG に記録する。
