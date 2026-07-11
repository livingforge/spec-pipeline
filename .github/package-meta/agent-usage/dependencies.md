# 依存ライブラリとライセンス — agent-usage

本スキルのライセンスは MIT ([LICENSE](LICENSE))。

## 実行時依存 (pip)

**なし。** agent-usage は **Python 3.10+ の標準ライブラリのみ**で動作する
（`json` / `pathlib` / `argparse` / `datetime` / `html` / `collections` など）。
venv も追加パッケージも不要で、`python <スキルdir>/scripts report` でそのまま動く。

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| （なし） | — | — |

外部依存を持たないため、依存の脆弱性・ライセンス起因のリスク・供給網（サプライ
チェーン）汚染の面は原理的に発生しない。ハッシュ固定ロック（`requirements.lock`）も
不要。

## 外部前提（インストール対象ではない）

ツール自体は何も導入しないが、**入力データの出どころ**として次の 2 つを前提にする。
いずれも本スキルが用意・自動導入するものではなく、利用環境に既に存在するログを
**読み取るだけ**である。

| 対象 | 役割 | 備考 |
|------|------|------|
| Claude Code のセッションログ | `~/.claude/projects/**/*.jsonl` | Claude Code が通常運用で書き出すもの。`--claude-dir` で場所を上書き可 |
| VS Code の Agent Debug Log | `%APPDATA%/Code/User/workspaceStorage/<id>/GitHub.copilot-chat/debug-logs/` | GitHub Copilot 版（`--agent copilot`）用。VS Code 側で Agent Debug Log を有効化しておく必要がある |

## データ契約（単価表）

`scripts/pricing.json` は Claude 版のコスト算出に使うモデル別単価の**正本**であり、
外部依存ではなく本スキル同梱のデータファイル。最新の公開価格に対して検証する運用と
し、`verified: false` の間は HTML 出力にも警告が出る（[GOVERNANCE.md](GOVERNANCE.md)
の「単価表の棚卸し」参照）。Copilot 版は実測 AIU（`copilotUsageNanoAiu`）を使うため
`pricing.json` は参照しない。

## 学習済みモデル・ネットワークアクセス

- **なし。** 一切のモデルダウンロード・ネットワークアクセスを行わない。集計は
  ローカルのログファイルを読むだけで完結する（オフラインで動作）。
