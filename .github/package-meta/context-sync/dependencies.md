# 依存ライブラリとライセンス — context-sync

本スキルのライセンスは MIT ([LICENSE](LICENSE))。

## 実行時依存 (pip)

**なし。** context-sync は `SKILL.md` 単体の**手順スキル**であり、自前のスクリプト・
パッケージ・依存を一切持たない（`scripts/` を同梱しない）。

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| （なし） | — | — |

## 実処理は contextdb スキルへ委譲

手順中のコマンド操作（乖離検出・変更適用・検証・ビュー再生成）はすべて**兄弟スキル
contextdb のツール**に委譲する。SKILL.md はプレースホルダ（`{{contextdb_dir}}` 等）で
そのスキルの起動パスを指すため、context-sync 自身は実行体を持たない
（[GOVERNANCE.md](GOVERNANCE.md) / `dr-07`：スキル単位の実行体同梱と兄弟スキル参照）。

| 手順 | 委譲先（contextdb） |
|------|--------------------|
| 乖離候補の列挙 | `sync-check`（`--strict` で CI ゲート） |
| 正本の追記・状態変更 | `mutate`（`add-item` / `add-relation` / `set-attr` / `deprecate` / `approve` / `apply`） |
| 機械検証 | `engine`（error で exit 1） |
| ビュー再生成 | `visualize` / `generate` |
| 変更一覧 | `history --uncommitted` |

したがって Python の要件・共有 venv は contextdb スキルのもの（PyYAML / Jinja2）に従う。
context-sync 自体に追加要件はない。
