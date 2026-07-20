---
name: fact-reconcile
description: "Reconcile extracted spec facts (facts.json) for semantic consistency before they become contextdb items: a deterministic blocking pass clusters duplicates, then an LLM adjudicates same-concept groups, flags contradictions (never auto-resolved), and proposes a canonical term map. Output is a review-only reconcile.json a human curates, then a deterministic contextdb mutate plan. LLM access reuses docsummary's provider/.env config (keys never printed). Use when asked to \"名寄せ / 重複ファクトを統合 / 意味的な一貫性 / reconcile / dedupe facts\". Needs extracted facts (docextract → docagent) and an LLM API key."
---

# fact-reconcile — 抽出ファクトを意味的に名寄せする

ライセンス（MIT）・変更履歴・依存・脅威モデル（秘密の扱い・外部送信・矛盾の非自動解決）は
[package-meta/fact-reconcile/](../../package-meta/fact-reconcile/)（CHANGELOG.md /
dependencies.md / GOVERNANCE.md / threat-model.md）を参照。

docextract → docagent で溜めた出典付きファクト（`facts.json`）は **文書ごとに独立** に
積まれるため、同じ概念が複数文書に出れば重複ファクトが蓄積される。contextdb エンジンは
参照整合性などの **構造的一貫性** は保証するが、「別 ID が同一概念を指す」「相反する値を
主張する」という **意味的一貫性** は検出しない。

このスキルは `facts.json` → contextdb の間に **提案生成ステップ** を挟み、意味的
一貫性を 1 箇所に封じ込める:

1. **ブロッキング**（決定的・LLM 不要）— 同一種別内で「同一かもしれない」候補を束ねる
2. **LLM 裁定**（`docsummary` の provider/.env を再利用）— 候補を「同一概念」「粒度差」「矛盾」に判定
3. **reconcile.json**（提案・人がレビュー）— concept / refinement / contradiction / term_map
4. **mutate plan**（決定的）— 承認済み concept を contextdb の `add-item`（status review）へ

さらに、正本化後の見出しを整える **命名パス**（`name` / `name-plan`）を同型の
独立パスとして持つ（決定論バッチ → LLM → `names.json` → 決定論 apply）。

①④が決定的（文書の提示順に依存しない）、②のみ非決定的だが **必ずレビューを
通る提案**。出力を勝手に適用せず、contextdb への反映は人の承認（`contextdb mutate
apply` → `approve`）を通す。

- venv コマンド **`fact-reconcile`**。同じプロジェクトに展開済みの
  **docextract / docsummary スキルが必要**（実行時に参照する）
- `plan` サブコマンドは PyYAML を使う（メタモデル読取）ため、**contextdb スキル**の
  展開も前提（同じ共有 venv に PyYAML を入れている）
- 秘密情報（API キー）は docsummary と共有の **`.env`（既定）または環境変数**で渡す。
  ツールは値を表示・保存しない

共有 venv の console script として任意のディレクトリから実行できる。venv 未 activate なら
`.venv/Scripts/<コマンド>`（Windows）/ `.venv/bin/<コマンド>`（macOS/Linux）で呼ぶ。venv 構築前は
`python .github/skills/fact-reconcile <サブコマンド>` でも同じ。

## 前提

1. 環境構築済み（@skill-setup。`python .github/skills/docextract setup --check` で確認）
2. ファクトが抽出・登録済み（@fact-extractor が `facts.json` に蓄積している）
3. LLM 接続設定済み（`fact-reconcile config --check`。設定は docsummary と共有）

## 接続設定（.env）

```bash
fact-reconcile config --check   # 設定状態の確認（キーの値は表示されない）
fact-reconcile config --init    # .env / .env.example の雛形を作成
```

`.env` の形式・対応プロバイダ（openai / azure / gemini / anthropic）は docsummary と同じ。

**秘密情報の扱い（エージェント向けの規律）**: `.env` は API キー等を含むため
**Read しない・cat しない・値を会話に出さない**。設定の有無は必ず `config --check` の
出力で判断し、キーの記入は利用者自身に依頼する。`.env` は `.gitignore` に追加する。

## 使い方 — 名寄せ → レビュー → plan

```bash
# ① 名寄せ提案を作る（対象は既定で全ファクト。--doc / --dir で絞れる）
fact-reconcile analyze --out reconcile.json
fact-reconcile analyze --dry-run           # 候補クラスタだけ表示（LLM 未呼び出し・API キー不要）

# ② 提案を人間可読でレビュー（統合提案・矛盾・用語）
fact-reconcile review reconcile.json

# ③ 承認した提案から contextdb mutate plan を作る（--root で対象 .contextdb を指定）
fact-reconcile plan --in reconcile.json --root .contextdb --out plan.json

# ④ 既存の contextdb ツールで検証 → 適用 → 承認
contextdb mutate apply plan.json --dry-run    # engine error 0 を確認（適用は巻き戻る）
contextdb mutate apply plan.json              # status: review で登録
contextdb mutate approve <id>                 # レビュー後、承認は人が行う
```

- `analyze` は facts が前回と同一なら再生成しない（内容ハッシュ + プロンプト版で
  鮮度判定）。作り直すときは `--force`
- `--block-threshold`（既定 0.5）で候補クラスタの緩さを調整（低いほど束ねやすい）
- すべてのサブコマンドは `--json` で機械可読出力（エージェント向け）

## 外部 LLM キー無しで裁定する（Claude 経路）

`.env` の外部プロバイダを使わず、**呼び出し元エージェント（Claude Code 等）自身が
裁定**して回せる。採番・出典写経・接地の安全弁・`facts_hash` は LLM 経路と同一の
正規コードを通る（scratchpad ドライバや内部への手当ては不要）:

```bash
# ① 候補クラスタを本文（type/statement/evidence/location）付きで書き出す（API キー不要）
fact-reconcile analyze --emit-clusters clusters.json

# ② 呼び出し元エージェントが clusters.json を読んで各クラスタを裁定し、裁定 JSON を書く
#    形式: {"verdicts":[{"cluster_id":"cl001",
#             "concepts":[{"member_fact_ids":[...],"canonical_term":"…",
#                          "canonical_statement":"…","variants":[…]}],
#             "contradictions":[{"fact_ids":[...],"issue":"…","claims":[…]}]}, …]}
#    cluster_id は clusters.json のものをそのまま使う（裁定の無いクラスタは統合なし扱い）

# ③ 外部裁定を正規 build 経路で reconcile.json に組む（API キー不要・LLM 未呼び出し）
fact-reconcile analyze --verdicts verdicts.json --out reconcile.json
```

以降は通常どおり `review` → `plan` → `contextdb mutate apply` → `approve`。
`.env` を設定している場合の自動裁定（引数なしの `analyze`）は従来どおり並存する。

## 出力の性質（重要）

- **concepts**: 2 件以上を「同一概念」と判定した統合提案。plan で 1 アイテムに畳む。
  単独に割れたファクト（重複でない）は載せない — 通常どおり @doc-author が扱う
- **refinements**: 「同一物ではないが一方が他方の概要」と判定した粒度差。
  **統合せず両アイテムを残し** `child refines parent` のエッジを張る（下記参照）
- **contradictions**: 値が食い違うメンバー。**自動でどちらも選ばず両論併記**し、
  人の判断に委ねる（plan には載せない）
- **term_map**: 表記ゆれ → 正準用語の対応
- plan は **全必須属性を facts から埋められた concept だけ** を `add-item` にする。
  enum の型・一意な physical_name など facts に無い属性が要る種別は `plan` が
  **保留（skipped）** として理由付きで報告する → @doc-author で補完する

## 粒度差（refinement）— 統合ではなく階層にする

同じ機能の **概要と実装詳細** は「同じことの言い換え」ではないので統合すると情報が
落ちる。例（概要は SKILL.md 由来、詳細は実装コード由来）:

- 親: 「抽出ファクトの意味的な名寄せ・矛盾検出ができる」
- 子: 「候補クラスタを LLM で裁定し、同一概念グループと矛盾に判定できる」

これらは **両方を残して** `child refines parent` で階層にする。判定は LLM 裁定が
`refinements` として返し、doc-author が `refines` エッジを張る（`merge` = 統合、
`refine` = 両方残してエッジ、`contradiction` = 報告のみ、の三分岐）。

粒度差ペアは語彙が離れやすく（上の例は実質「矛盾」しか共有しない）統合候補の
ブロッキングでは同じクラスタに入らないため、**専用の再現率重視パス**が候補を作る:

- 内容語（漢字/カタカナ/英数の連なり）の **包含率**で測る。助詞・語尾を含む素の
  文字 2-gram だとノイズで薄まるが、内容語ベースなら無関係ペアは 0.0 に落ちる
- 閾値は低く（既定 0.10）、代わりに **1 アンカーあたり上位 K 件**（既定 4）に絞って
  コストを抑える。**絞り込みは LLM 裁定に委ねる**分業
- 対象は意図の層（機能要件・非機能要件・業務ルール・制約・前提・外部インターフェース）のみ。
  骨格（メソッド/データ項目等）は codescan の決定論抽出と `has-method` に任せる

```bash
fact-reconcile analyze --dry-run          # 候補を kind（merge / refine）付きで確認
fact-reconcile analyze --refine-threshold 0.05   # 取りこぼすなら下げる（recall 増）
fact-reconcile analyze --no-refine        # 粒度差パスを止めて従来どおり統合だけ
```

`refines` エッジの両端は contextdb のアイテム ID なので、対応が決まるまで plan には
載らない（`--fact-map <fact_id→item_id の JSON>` を渡せば `add-relation` op になり、
渡さなければ「doc-author で張る」として保留報告になる）。

## 命名パス（name / name-plan）— 見出しだけを整える

ファクトから起こしたアイテムの `name` は `statement` の先頭を区切りで切っただけの
ことがあり、見出しとして独立していない（「候補クラスタを LLM で裁定し」で切れる）。
名寄せで統合された concept には `canonical_term` が付くが、**大多数を占める単独
アイテムは素通りする**ため、専用の命名パスで補う。

```bash
# ① 種別ごとのバッチを書き出す（決定論・API キー不要）
#    --reconcile を渡すと統合済み concept の canonical_term を初期値として引き継ぐ
fact-reconcile name --root .contextdb --reconcile reconcile.json \
    --emit-batches batches.json

# ② 呼び出し元エージェントが命名し、命名 JSON を書く（外部 LLM 経路なら ① ③ は不要）
#    形式: {"verdicts":[{"batch_id":"nb001",
#             "names":[{"id":"req-0014","canonical_name":"クラスタLLM裁定",
#                       "rationale":"…"}]}, …]}

# ③ names.json に組む → mutate plan（name の set-attr のみ）
fact-reconcile name --root .contextdb --verdicts verdicts.json --out names.json
fact-reconcile name-plan --in names.json --out plan.json
```

守っていること:

- **触るのは `name` だけ**。`statement` / `source` は不変で、書き換え op も作らない。
  LLM が statement を返してきても接地の段階で捨てる
- **種別ごとにバッチ化**する。同種を一度に見せないと粒度・文体が揃わず、相互に
  重複しない名前も付けられない
- **同種内で name は一意**。重複したぶんは黙って改名せず、`conflict` を立てて plan
  から外し人のレビューに回す（比較相手は改名しない既存アイテムの名前も含む）
- `name` 属性を持たない種別は対象外。例えば business-rule は `label_field` が
  `statement` なので、命名すると statement 不変の約束を破ることになる → 触らない
- 骨格（メソッド/データ項目/クラス/エンティティ）は codescan の決定論命名を尊重し
  **命名対象外**（既定の対象は requirement / external-interface / screen）

## トラブルシューティング

| 症状 | 対処 |
| --- | --- |
| `LLM の接続設定が見つかりません` | `fact-reconcile config --init` で雛形を作り、キーを記入してもらう |
| `複数のプロバイダが設定済み` | `--provider` か `DOCSUMMARY_PROVIDER` で選択 |
| 候補クラスタが 0 個 | facts が少ない/重複が無い。`analyze --dry-run` で候補を確認。閾値を下げる |
| 粒度差が検出されない | `analyze --dry-run` で kind=refine の候補が出ているか確認。0 なら `--refine-threshold` を下げるか `--refine-top-k` を増やす |
| 粒度差の誤検出が多い | `--refine-threshold` を上げる。裁定側で落とすのが本筋（候補は recall 重視の設計） |
| `命名対象のアイテムがありません` | `--root` が正しいか、対象種別に `name` 属性を持つアイテムがあるか確認（`--type` で種別を指定できる） |
| 命名が重複により保留される | `name-plan` の保留一覧を見て人が名前を決め、`names.json` を直してから再度 `name-plan` |
| `メタモデルが見つかりません` | `plan --root <.contextdb>` か `--metamodel <path>` を指定。contextdb スキルが必要 |
| plan の ops が 0・保留ばかり | facts に構造化属性が無い種別（データ項目の型等）。@doc-author で補完する |
| 依存 docsummary が見つからない | docsummary スキルを同じプロジェクトに展開する（build_skill.py で生成） |
