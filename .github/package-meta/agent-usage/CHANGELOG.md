# Changelog — agent-usage

バージョニング方針は [GOVERNANCE.md](GOVERNANCE.md)、依存とライセンスは
[dependencies.md](dependencies.md) を参照。

## Unreleased

### Added
- **package-meta の整備**。ライセンス（MIT）・変更履歴・依存・脅威モデルを
  `package-meta/agent-usage/`（LICENSE / CHANGELOG.md / dependencies.md /
  GOVERNANCE.md / threat-model.md）へ集約。スキルの実行時動作に直接関係しない
  ガバナンス/メタ文書を scripts/ から分離する方針に合わせた。

## 0.1.0 (2026-07-11)

初回リリース。**Python 3.10+ 標準ライブラリのみ**で動く自己完結スキル
（venv・追加依存なし）。

- **Claude Code の利用サマリ**。`~/.claude/projects/**/*.jsonl` を解析し、
  消費トークン（入力/出力/キャッシュ書込 5m・1h/キャッシュ読込）・利用モデル・
  ツール呼び出し・実行時間・**コスト（USD）**を集計。コストと実行時間はログに
  保存されないため、トークン×モデル別単価（`scripts/pricing.json`）と timestamp 差分
  から算出する。
- **サブエージェント（sidechain / child session）の集入**。
  `<プロジェクト>/<session>/subagents/agent-*.jsonl` を親会話の Agent 呼び出しへ
  ひも付けて総計へ算入。古いログ（`subagents/` の無い形式）では内部不明として
  HTML に過小評価の注意を出す。
- **GitHub Copilot 版レポート**（`--agent copilot`）。VS Code の Agent Debug Log
  （`workspaceStorage/<id>/GitHub.copilot-chat/debug-logs/`）を読み、同じ
  `summary.json` / `report.html` を出力。**コストは実測 AIU（AI Units,
  `copilotUsageNanoAiu`）**で表示し USD 換算はしない。`child_session_ref` の
  子ログも本体総計へ畳み込む。
- **出力**: 機械可読の `summary.json`（`totals` / `by_model` / `by_project` /
  `by_day` / `by_agent` / `by_tool` / `top_sessions` / `cost_unit`）、`conversations.csv`、
  および単一ファイル・外部依存なし・ライト/ダーク対応の `report.html`。
- **プロジェクト集約**は各メッセージの `cwd` を上方探索し Git リポジトリルート名に
  まとめる。`--top N` で会話タイムライン収録件数を制御（集計値自体は常に全会話で正確）。
