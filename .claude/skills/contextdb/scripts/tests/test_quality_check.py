# -*- coding: utf-8 -*-
"""quality_check.py（品質チェック）のテスト — 見出しの形・重複・表記ゆれを固定する。

リポジトリ (contextdb/tests/) とスキルバンドル (scripts/tests/) のどちらでも
「親ディレクトリに quality_check.py がある」前提で動く。

検出はすべて決定論なので LLM のモックは不要。誤検出しないこと（正しい見出しを
error にしない）を、検出できることと同じ重みで固定する。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from quality_check import run_checks  # noqa: E402

METAMODEL = """
version: 1
item_types:
  requirement:
    label: 要件
    label_field: name
    attributes:
      name:      { kind: string, required: true }
      statement: { kind: string, required: true }
  business-rule:
    label: 業務ルール
    label_field: statement
    attributes:
      statement: { kind: string, required: true }
  method:
    label: メソッド
    label_field: signature
    attributes:
      signature:   { kind: string, required: true }
      description: { kind: string, required: true }
  glossary-term:
    label: 用語
    label_field: term
    attributes:
      term:        { kind: string, required: true }
      description: { kind: string, required: true }
  data-item:
    label: データ項目
    label_field: name
    attributes:
      name:        { kind: string, required: true }
      description: { kind: string }
  open-issue:
    label: 課題
    label_field: title
    attributes:
      title:     { kind: string, required: true }
      statement: { kind: string, required: true }
relation_types: {}
"""

# 規約を満たす見出し（体言止め・短い・本文の先頭一致でない）
CLEAN = """
- id: req-0001
  name: 受注データの締め処理
  statement: 営業日の 18 時に当日分の受注データを締めて確定する。
  status: approved
- id: req-0002
  name: 在庫引当の自動化
  statement: 受注確定時に在庫を自動で引き当てる。
  status: approved
"""

# statement を見出しにする種別は対象外（切り詰めが起こりえない）
RULES = """
- id: br-0001
  statement: 与信限度額を超える受注は承認待ちにする。
  status: approved
"""

# 骨格（label_field が signature）は codescan の決定論命名なので対象外
METHODS = """
- id: mth-0001
  signature: closeOrders(date)
  description: 受注を締める。
  status: approved
"""


def build(tree: dict | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix="contextdb-quality-")) / "data"
    defaults = {
        "metamodel.yaml": METAMODEL,
        "items/requirement/core.yaml": CLEAN,
        "items/business-rule/core.yaml": RULES,
        "items/method/core.yaml": METHODS,
    }
    for rel, text in {**defaults, **(tree or {})}.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def find(report: dict, kind: str) -> list[dict]:
    return [f for f in report["findings"] if f["kind"] == kind]


# ── ① 対象種別の選び方 ─────────────────────────────────────────

def test_clean_store_has_no_findings():
    report = run_checks(build())
    assert report["findings"] == [], report["findings"]


def test_targets_only_name_labelled_types():
    report = run_checks(build())
    # label_field が statement / signature / term / title の種別は入らない。
    # data-item も name 見出しなので対象に入る（本文はテンプレ description）。
    assert report["types"] == ["data-item", "requirement"]
    assert report["checked"] == 2   # 既定ツリーに data-item アイテムは無い


def test_type_option_narrows_targets():
    report = run_checks(build(), types=["business-rule"])
    assert report["types"] == ["business-rule"]


# ── ② 切り詰め・語尾・長さ ─────────────────────────────────────

def test_name_prefix_of_statement_is_error():
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-cut
  name: 営業日の 18 時に当日分の
  statement: 営業日の 18 時に当日分の受注データを締めて確定する。
  status: review
"""}))
    hits = find(report, "QC-NAME-PREFIX")
    assert [f["where"] for f in hits] == ["req-cut"]
    assert hits[0]["level"] == "error"


def test_name_prefix_ignores_short_names():
    # 短い見出しが偶然 statement の書き出しと一致しても切り詰めとは見なさない
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-short
  name: 締め
  statement: 締め処理を営業日の 18 時に実行する。
  status: review
"""}))
    assert find(report, "QC-NAME-PREFIX") == []


def test_particle_ending_is_error():
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-part
  name: 受注データを
  statement: まったく異なる本文をここに置く。
  status: review
"""}))
    hits = find(report, "QC-NAME-CUT")
    assert [f["where"] for f in hits] == ["req-part"]
    assert hits[0]["level"] == "error"


def test_noun_ending_in_shi_is_not_flagged():
    # 「見出し」「送り」等は正当な名詞語尾なので連用中止として拾わない
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-noun
  name: 帳票の見出し
  statement: まったく異なる本文をここに置く。
  status: review
"""}))
    assert find(report, "QC-NAME-CUT") == []


def test_yogen_ending_is_warn():
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-yogen
  name: 在庫引当を自動化する
  statement: まったく異なる本文をここに置く。
  status: review
"""}))
    hits = find(report, "QC-NAME-YOGEN")
    assert [f["where"] for f in hits] == ["req-yogen"]
    assert hits[0]["level"] == "warn"


def test_long_name_is_warn_and_threshold_is_configurable():
    tree = {"items/requirement/core.yaml": """
- id: req-long
  name: 受注データの締め処理と在庫引当および与信判定をまとめて実行する一括バッチ機能
  statement: まったく異なる本文をここに置く。
  status: review
"""}
    assert len(find(run_checks(build(tree)), "QC-NAME-LEN")) == 1
    tree["quality.yaml"] = "max_name_length: 100\n"
    assert find(run_checks(build(tree)), "QC-NAME-LEN") == []


# ── ③ 重複 ───────────────────────────────────────────────────

def test_duplicate_names_within_type_are_error():
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-a
  name: 受注の締め処理
  statement: 営業日の 18 時に受注を締める。
  status: review
- id: req-b
  name: 受注の締め処理
  statement: 月末に受注を締める。
  status: review
"""}))
    hits = find(report, "QC-NAME-DUP")
    # 先頭 (id 順) は残し、以降を指摘する
    assert [f["where"] for f in hits] == ["req-b"]
    assert hits[0]["level"] == "error"


def test_duplicate_detection_folds_width_and_case():
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-a
  name: CSV 取込
  statement: 取引先マスタを CSV で取り込む。
  status: review
- id: req-b
  name: ＣＳＶ取込
  statement: 商品マスタを CSV で取り込む。
  status: review
"""}))
    assert len(find(report, "QC-NAME-DUP")) == 1


def test_near_duplicate_statements_are_warn():
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-a
  name: 受注の締め処理
  statement: 営業日の 18 時に当日分の受注データを締めて確定する。
  status: review
- id: req-b
  name: 受注データの確定
  statement: 営業日の 18 時に当日分の受注データを締めて確定する。
  status: review
"""}))
    hits = find(report, "QC-STMT-NEAR-DUP")
    assert [f["where"] for f in hits] == ["req-b"]
    assert hits[0]["level"] == "warn"


def test_unrelated_statements_are_not_near_duplicates():
    assert find(run_checks(build()), "QC-STMT-NEAR-DUP") == []


def test_template_descriptions_do_not_fire_near_dup():
    # 型から機械生成された定型 description を持つ data-item が大量に完全一致しても、
    # 近似重複は statement 属性のみを見るので誤検出しない（P1-2）。
    rows = "\n".join(f"""
- id: di-{i:03d}
  name: 項目{i}
  description: 文字列型のデータ項目。
  status: review""" for i in range(6))
    report = run_checks(build({"items/data-item/core.yaml": rows}))
    assert find(report, "QC-STMT-NEAR-DUP") == []


def test_near_dup_catches_word_order_difference():
    # 文字 2-gram Jaccard だけでは閾値に届かない語順違いの実重複を、
    # SequenceMatcher 併用で拾う（P1-2）。
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-a
  name: 受注の締め処理
  statement: 営業日の 18 時に当日分の受注データを締めて確定し、在庫を引き当てる。
  status: review
- id: req-b
  name: 締めと在庫引当
  statement: 在庫を引き当て、営業日の 18 時に当日分の受注データを締めて確定する。
  status: review
"""}))
    hits = find(report, "QC-STMT-NEAR-DUP")
    assert [f["where"] for f in hits] == ["req-b"], report["findings"]


# ── ⑥ 「の仕様」接尾（QC-NAME-SUFFIX） ─────────────────────────

def test_no_spec_suffix_on_verb_is_error():
    # 動詞に「の仕様」を接いだ破綻見出し（P2-3）
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-suf
  name: LLM を呼ばの仕様
  statement: まったく異なる本文をここに置く。
  status: review
"""}))
    hits = find(report, "QC-NAME-SUFFIX")
    assert [f["where"] for f in hits] == ["req-suf"]
    assert hits[0]["level"] == "error"


def test_spec_suffix_on_noun_is_not_flagged():
    # 名詞＋の＋仕様（正当）と、「〜の仕様書」（末尾が書）は誤検出しない
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-ok1
  name: 決済処理の仕様
  statement: まったく異なる本文をここに置く一。
  status: review
- id: req-ok2
  name: 受注管理の仕様書
  statement: まったく異なる本文をここに置く二。
  status: review
"""}))
    assert find(report, "QC-NAME-SUFFIX") == []


# ── ⑦ 検査できない種別の指定（QC-TYPE-SKIP） ──────────────────

def test_uncheckable_type_request_is_surfaced():
    # label_field が name でない種別を --type で渡すと黙って 0 件にせず理由を出す
    report = run_checks(build(), types=["open-issue"])
    hits = find(report, "QC-TYPE-SKIP")
    assert [f["where"] for f in hits] == ["open-issue"]
    assert hits[0]["level"] == "warn"
    assert "label_field" in hits[0]["message"]


def test_unknown_type_request_is_surfaced():
    report = run_checks(build(), types=["no-such-type"])
    hits = find(report, "QC-TYPE-SKIP")
    assert [f["where"] for f in hits] == ["no-such-type"]


def test_valid_name_type_request_has_no_skip_finding():
    report = run_checks(build(), types=["requirement"])
    assert find(report, "QC-TYPE-SKIP") == []


# ── ④ 用語の表記ゆれ ───────────────────────────────────────────

def test_term_variant_is_warn():
    report = run_checks(build({
        "items/glossary-term/core.yaml": """
- id: term-csv
  term: CSV 取込
  description: 外部ファイルからの一括登録。
  status: approved
""",
        "items/requirement/core.yaml": """
- id: req-var
  name: 取引先マスタの一括登録
  statement: ＣＳＶ取込により取引先マスタを一括登録する。
  status: review
""",
    }))
    hits = find(report, "QC-TERM-VARIANT")
    assert [f["where"] for f in hits] == ["req-var"]
    assert hits[0]["level"] == "warn"


def test_term_in_canonical_form_is_not_flagged():
    report = run_checks(build({
        "items/glossary-term/core.yaml": """
- id: term-csv
  term: CSV 取込
  description: 外部ファイルからの一括登録。
  status: approved
""",
        "items/requirement/core.yaml": """
- id: req-ok
  name: 取引先マスタの一括登録
  statement: CSV 取込により取引先マスタを一括登録する。
  status: review
""",
    }))
    assert find(report, "QC-TERM-VARIANT") == []


# ── ⑤ status の扱い ───────────────────────────────────────────

def test_deprecated_items_are_skipped():
    report = run_checks(build({"items/requirement/core.yaml": """
- id: req-old
  name: 受注データを
  statement: まったく異なる本文をここに置く。
  status: deprecated
"""}))
    assert report["findings"] == []
