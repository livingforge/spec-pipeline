# 依存ライブラリとライセンス — contextdb

本スキルのライセンスは MIT ([LICENSE](LICENSE))。
実行時依存はすべて商用利用可能なライセンスで構成している。

## 実行時依存 (pip)

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| PyYAML (>=6.0) | 仕様データ (items/ + relations/ + metamodel.yaml) の読み書き | MIT |
| Jinja2 (>=3.1) | 設計書 (Markdown / Excel 風 HTML) の生成テンプレート | BSD-3-Clause |

追加のネットワークアクセス・学習済みモデル・外部バイナリは不要。共有 venv に上記 2 つを
入れるだけで動作する（`setup` が docextract の依存と一括で導入する）。

## 安全側の既定

- YAML は **`yaml.safe_load` のみ**で読む（任意 Python オブジェクトの復元＝コード実行に
  つながる `load` は使わない）。壊れた YAML は例外で中断せず **error として報告**する
  （[threat-model.md](threat-model.md) の T1）。
- HTML 生成の Jinja2 環境は **`.html` 出力に対して autoescape を有効化**しており、
  仕様データ中のテキストがそのままマークアップとして解釈されるのを防ぐ。
- 生成 HTML（対話型グラフ `out/contextdb.html`）は **CDN・外部依存なしの自己完結**
  （[GOVERNANCE.md](GOVERNANCE.md) / `dr-05`）。

## バージョン固定と再現性

- `requirements.txt` は floor-pin (`>=`) で許容範囲を示す宣言。ハッシュ固定ロックは
  docextract の共有 venv 運用に準じる（両スキルの依存を単一の共有環境へ導入する）。
