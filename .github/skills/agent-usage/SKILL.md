---
name: agent-usage
description: Analyze coding-agent telemetry from VS Code / local session logs and produce a token, model, tool-call, run-time, and cost summary as summary.json + a self-contained report.html. Parses Claude Code session records (~/.claude/projects/**/*.jsonl) - including subagent (sidechain) activity - and computes cost from an editable per-model pricing table (cost and duration are not stored, so they are derived). Use when asked to "エージェントの利用状況 / トークン消費 / コスト集計 / 利用モデル / ツール呼び出し / 実行時間 / usage レポート / Claude Code の使用量". Requires Python 3.10+, standard library only.
---

# agent-usage — コーディングエージェントの利用サマリ

コーディングエージェント（まず Claude Code）のローカルセッション記録を解析し、
**消費トークン・利用モデル・ツール呼び出し・実行時間・コスト**を集計して
`summary.json` と自己完結の `report.html` に出力するスキル。メインエージェントに
加えサブエージェント（sidechain）も対象にする。

## データの出どころと重要な前提

- **Claude Code** はセッションを 1 メッセージ 1 行の JSONL で
  `~/.claude/projects/<プロジェクト>/*.jsonl` に保存する。各 assistant 行の
  `message.usage` に入力/出力/キャッシュ書込(5m・1h)/キャッシュ読込トークンと
  `model` が入っている。
- **コストと実行時間は保存されていない**ため算出する:
  - コスト = トークン × モデル別単価（`scripts/pricing.json`）。キャッシュは別単価
    （読込 = 入力×0.1 / 5m 書込 = 入力×1.25 / 1h 書込 = 入力×2）。
  - 実行時間 = セッション内 timestamp の差分。ツール別時間は `tool_use` →
    対応する `tool_result` のタイムスタンプを挟んで算出。
- **サブエージェント（Task/Agent）**の内部は、新しい Claude Code（VS Code 版を含む）では
  親の `<session>.jsonl` に混ざらず `<プロジェクト>/<session>/subagents/agent-*.jsonl`
  に分離記録される（各行 `isSidechain: true`・`usage` 付き）。本スキルはこのサブ
  ディレクトリも読み、隣の `agent-*.meta.json`（`toolUseId` / `agentType`）で**親会話の
  当該 Agent 呼び出しにひも付け**、内部トークン・コストを総計へ算入する。古いログ
  （`subagents/` が無い形式）ではサブエージェント内部は不明のままで、`by_agent.subagent`
  が空・HTML に過小評価の注意が出る。モデルティアの混在（opus/sonnet/haiku/fable）も
  モデル別集計で見える。
- **プロジェクト名**は各メッセージの `cwd` を上方探索し **Git リポジトリルート
  （.git を持つ最上位）の名前**に集約する。サブフォルダ（`.contextdb` / `out` 等）で
  作業しても同じリポジトリにまとまる。`.git` が見つからなければ cwd の basename。

> `pricing.json` は単価の**正本**。`verified: false` の間は最新の公開価格に対して
> 検証する運用にすること（未検証だと HTML 上にも警告が出る）。表に無いモデルは
> トークンだけ集計しコストは unknown（「*」印）になる。

## 使い方

セットアップ不要（Python 3.10+ 標準ライブラリのみ、venv 依存なし）。

```bash
# 直近すべてを集計して out/ に summary.json + report.html を出力
python .github/skills/agent-usage/scripts report --out out/

# 直近 30 日、特定プロジェクトだけ、JSON だけ
python .github/skills/agent-usage/scripts report --days 30 --project ai-ready-pipeline --json-only

# 期間指定・別の Claude ディレクトリ・別単価表
python .github/skills/agent-usage/scripts report --since 2026-06-01 --until 2026-06-30 \
    --claude-dir /path/to/.claude/projects --pricing my-pricing.json
```

主なオプション: `--claude-dir` `--pricing` `--out` `--days` `--since` `--until`
`--project` `--top` `--json-only`。詳細は `report --help`。

### `--top N` — 会話タイムラインの収録件数（既定 100）

`--top` は**会話詳細モーダルにツール/Agent タイムライン（時系列の呼び出し履歴）を
収録する会話数**を、コスト降順の上位 N 件に限定するオプション。既定は **100**。

- **会話行・詳細モーダル自体は常に全会話ぶん**出力される（`--top` では減らない）。
  上位 N 件を超える会話のモーダルでは、タイムラインの代わりに
  「タイムラインは上位会話のみ収録しています」と表示される。
- タイムラインは 1 会話あたり最大 500 イベント（`report.py` の `MAX_TIMELINE`）に
  なり得るため、全会話ぶん埋め込むと `report.html` が肥大化する。既定 100 はその
  肥大化防止と実用上の網羅性のバランス。
- `--top 0` で**全会話にタイムラインを収録**する（大規模ログでは巨大化に注意）。
- タイムライン以外の集計（`tool_calls` などの件数・コスト・時間）は `--top` に
  よらず**全会話で常に正確**。`summary.json` / `conversations.csv` も常に全件。

## 出力

- `summary.json` — `totals`（トークン内訳・コスト・キャッシュ節約額・総時間）、
  `by_model` / `by_project` / `by_day` / `by_agent`(main/subagent) / `by_tool`
  （呼出数・所要時間・エラー数）、`top_sessions`（コスト上位）。機械可読の正本。
- `report.html` — 単一ファイル・外部依存なし・ライト/ダーク対応。カード＋日次コスト
  グラフ＋モデル/ツール/プロジェクト/セッションの各表。

## 拡張の方針（フェーズ2）

GitHub Copilot は VS Code の Workspace Storage
（`state.vscdb` / `chatSessions/*.jsonl`）に記録されるが、**設計上トークン数も
コストも保持しない**（リクエスト課金）。取れるのはリクエスト回数・モデル・時刻・
agent モードのツール呼び出しまで。同じ画面に載せる場合は「実測(Claude) vs 概算(Copilot)」
を明示し、Copilot は `copilot_premium_request_usd` × 回数で概算する。
