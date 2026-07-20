---
name: context-sync
description: "Sync implementation changes into .contextdb, the project's spec-as-data single source of truth: enumerate drift candidates, judge which are real spec changes, apply them as a reviewed mutate plan, pass machine validation (error 0), and regenerate the views. Use after implementing/changing/removing a feature, or when asked to \"contextdb を更新 / 仕様データに反映 / context-sync\". Run it as the final step of implementation work so the spec data never drifts behind the code."
---

# context-sync — 実装差分を .contextdb（設計データの正本）へ同期する

このプロジェクトの設計データの正本は `.contextdb/`（items/ + relations/、YAML）である。
実装だけ進んで正本が古くなるのを防ぐため、機能の追加・変更・廃止のあとに
この手順で `.contextdb` を更新する（実装作業の締めくくりに実行する）。

ライセンス（MIT）・変更履歴・依存・前提とする安全性の担保は
[package-meta/context-sync/](../../package-meta/context-sync/)（CHANGELOG.md /
dependencies.md / GOVERNANCE.md / threat-model.md）を参照。

手順は可能な限り機械化されている。判断が要るのは
**「この実装変更は仕様の変化か」の判定と、説明文・出典の文章化だけ**で、
候補の列挙は `sync_check.py`、YAML の追記・状態変更は `mutate.py` が行う。

前提:

- 正本は `.contextdb/items/` と `.contextdb/relations/`。`out/` は生成ビュー（直接編集しない）
- 使える語彙（種別・属性・関係）は `.contextdb/metamodel.yaml` の宣言がすべて
- コマンドは venv の console script `contextdb`（`--root` 省略時は自動でプロジェクトの
  `.contextdb` を使う。呼び出し形の詳細は contextdb スキル参照）
- 棚卸し・ドリフト検出の規則は `.contextdb/sync.yaml` に宣言されている

## 手順

### 1. 機械チェック — どこを見るべきかを列挙させる

```bash
contextdb sync-check          # 人が読む形式
contextdb sync-check --json   # 機械可読
contextdb sync-check --rev HEAD~3   # コミット済み分も対象
```

出力の見方（これが同期候補のリストになる）:

| 検出 | 意味 | 取るべき行動 |
| --- | --- | --- |
| `stale` | 変更ファイルを参照しているアイテム/関係 | 仕様が変わったか判定（手順 2） |
| `unregistered` | 実体があるのに対応アイテムが無い | `add-item` で登録 |
| `vanished` | アイテムが指す実体が消えた | `deprecate`（削除はしない） |
| `dead-path` / `dead-doc` | パス・出典文書が存在しない | パス修正 or 出典差し替え |
| `stale-evidence` | 出典の原文が文書に見つからない | evidence を現行の原文に更新 |

この会話で実装した内容（何を作った・変えた・消したか）を第一の入力として、
検出に漏れがないかを補う。

### 2. 影響判定 — 実装の変化を contextdb の語彙に写像する

使える語彙は `.contextdb/metamodel.yaml`（`extends` で継承した標準パックを含む）の
宣言がすべてで、**宣言に無い種別・関係は使えない**（`contextdb list --json` や
metamodel.yaml で確認する）。下表は標準パック jp-sier-std を前提にした対応。

| 実装の変化 | .contextdb での操作 |
| --- | --- |
| 利用者から見える機能の追加 | `requirement` 追加（`kind`: 機能/非機能、`statement` 必須。`req_id` は自動採番）+ 実現主体から `realizes` |
| モジュール / クラスの新設 | `module` 追加（`class_name` `description` 必須）+ 実現する要件へ `realizes` |
| メソッドの追加 | `method` 追加（`signature` 必須）+ 所属モジュールから `has-method` |
| 既存機能の振る舞い変更・拡張 | 該当アイテムの `statement` / `description` 等を更新（status は review に戻る） |
| 概要と実装詳細が併存する（粒度差） | 統合せず両方残し、詳細側から概要側へ `refines`（`child refines parent`） |
| 業務的な条件・区分値・制約の追加 | `business-rule` 追加（`statement` 必須）+ 対象へ `constrains` |
| テーブル / 項目の追加 | `entity`（`physical_name` 必須）/ `data-item`（`type` 必須）+ `has-column` |
| 画面・帳票の追加 | `screen` 追加 + 表示項目へ `displays` |
| 外部連携の追加 | `external-interface` 追加（`direction` 必須）+ 対象へ `interfaces` |
| 前提条件・技術/運用/法令上の制約の追加 | `constraint` 追加（`statement` 必須。`constraint_id` は自動採番）+ 縛る対象へ `constrains` |
| 用語の定義 | `glossary-term` 追加（`term` + `description` 必須。連番 ID は無い） |
| テストの追加 | `test-case` 追加 + 対象へ `verifies`（結果を残すなら `test-run` + `executes`） |
| 機能・構成要素の廃止 | `deprecate`（アイテムは削除しない） |

標準パックに受け皿の無い概念（スキル・エージェント・依存ライブラリ・生成物など）は、
**既存種別へ無理に丸めない**。`module` の `description` に書いて残すか、
プロジェクトの metamodel.yaml へ種別・関係を追加することを利用者に提案する
（勝手に語彙を増やさない・緩めない）。

**仕様に影響しない変更**（タイポ修正・リファクタ・テストのみ・コメント等）なら、
`.contextdb` は触らず「仕様影響なし」とその理由を報告して終了してよい
（sync_check の `stale` はその判断の裏付けとして報告に添える）。

### 3. 正本の更新 — plan を書いて mutate.py に適用させる

判断結果を操作リスト（plan.json）に書き、一括適用する。YAML を手で編集しない。

```bash
contextdb mutate apply plan.json --dry-run   # まず確認
contextdb mutate apply plan.json
```

plan.json の例:

```json
{"ops": [
  {"op": "add-item", "type": "requirement", "slug": "sync-check",
   "attrs": {"name": "同期チェック", "kind": "機能", "category": "仕様管理",
             "statement": "利用者は実装差分と仕様データのドリフト候補を列挙できる。"},
   "source": {"doc": "contextdb/sync_check.py", "location": {"section": "docstring"},
              "evidence": "根拠の原文"}},
  {"op": "add-relation", "type": "realizes", "from": "mod-contextdb", "to": "req-sync-check"},
  {"op": "set-attr", "ref": "req-visualize", "attr": "statement", "value": "新しい説明"},
  {"op": "deprecate", "ref": "req-old"}
]}
```

ツールが強制してくれる規約（手で守る必要がなくなったもの）:

- ID の接頭辞と採番、連番属性（`req_id` `rule_id` `module_id` 等の次番）は自動。
  **plan に書かない**（書くと自動採番が働かない）
- 新規・変更は必ず `status: review`（**approved に上げるのは人がレビューした後、
  指示があったときに `approve` 操作で行う。自分で approved にしない**）
- `source`（出典）必須。`doc` は実在パス、`evidence` はその文書にある原文を書く
- 適用後に全体を再検証し、新たな error が生まれる plan は自動で巻き戻される

書くのは日本語・である調で、既存項目と同じ粒度に合わせる（既存の core.yaml を
少し読んで文体を確認する）。1〜2 件だけの単純な変更なら plan を作らず
`add-item` / `set-attr` / `deprecate` のサブコマンド直接実行でもよい。

### 4. 機械検証（ゲート）

```bash
contextdb engine --frozen   # 検証 + 集計。error か pack.lock のずれがあれば exit 1
contextdb sync-check   # 検出が残っていないか再確認
```

error 0 になり、sync_check の検出（意図して残す stale-evidence 等を除く）が
消えるまで修正してから先へ進む。

### 5. ビュー再生成

```bash
contextdb visualize  # .contextdb/out/contextdb.html（対話型グラフ）
contextdb generate   # .contextdb/documents/ に定義された設計書
```

### 6. 報告 — 変更一覧は履歴ツールに作らせる

```bash
contextdb history --uncommitted   # 未コミットの意味的変更一覧
```

- 上記の出力（追加・変更したアイテム / 関係の一覧）をそのまま報告に使う
- `status: review` で登録した件数。ビューア（contextdb.html）の「レビュー中」ボタンで
  レビュー対象とその隣接だけの関係グラフを確認できることを添える
- 仕様影響なしと判断した場合は、その判断根拠（sync_check の結果を添える）

承認（`approve`）・CI ゲート（`sync-check --strict` で exit 1）の運用は contextdb スキル参照。
