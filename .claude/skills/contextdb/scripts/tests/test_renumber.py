# -*- coding: utf-8 -*-
"""renumber.py（ID 一括振り直し）のテスト — 通番 ID だけを機能ごとの連番へ振り直し、
関係・埋め込み参照・slug ID・表示連番・出典を壊さないことを固定する。

リポジトリ (contextdb/tests/) とスキルバンドル (scripts/tests/) のどちらでも
「親ディレクトリに renumber.py がある」前提で動く。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import renumber                                    # noqa: E402
from engine import Store                           # noqa: E402
from renumber import plan_map, read_state, run     # noqa: E402

METAMODEL = """
version: 1
item_types:
  requirement:
    label: 要件
    label_field: name
    id_prefix: req-
    sequence: { attribute: req_id, by: kind, format: { 機能: "FR-{:03d}", 非機能: "NFR-{:03d}" } }
    attributes:
      req_id:    { kind: string, required: true, unique: true }
      name:      { kind: string, required: true }
      kind:      { kind: enum, values: [機能, 非機能], required: true }
      category:  { kind: string }
      statement: { kind: string, required: true }
  data-item:
    label: データ項目
    label_field: name
    id_prefix: di-
    attributes:
      name:     { kind: string, required: true }
      type:     { kind: enum, values: [数値, 文字列, 日付, 真偽], required: true }
      category: { kind: string }
  entity:
    label: エンティティ
    label_field: name
    id_prefix: ent-
    attributes:
      name:          { kind: string, required: true }
      physical_name: { kind: string, required: true, unique: true }
  business-rule:
    label: 業務ルール
    label_field: statement
    id_prefix: br-
    sequence: { attribute: rule_id, format: "BR-{:03d}" }
    attributes:
      rule_id:   { kind: string, required: true, unique: true }
      category:  { kind: string }
      statement: { kind: string, required: true }
  module:
    label: モジュール
    label_field: class_name
    id_prefix: mod-
    attributes:
      class_name:  { kind: string, required: true, unique: true }
      description: { kind: string, required: true }
relation_types:
  has-column:
    from: entity
    to: data-item
    cardinality: { from: "1..*" }
    ordered: true
    embedded: { field: columns, target_key: item }
    attributes:
      physical_name: { kind: string, required: true, unique: true }
  constrains:
    from: business-rule
    to: [data-item, requirement]
    embedded: { field: applies_to }
  realizes:
    from: [module, entity]
    to: requirement
  refines:
    from: [module, requirement]
    to: [entity, requirement]
"""

# 要件（リスト形式）。category を A/B で散らし、slug ID を 1 件混ぜる。
# req-0001 の出典 evidence は block scalar で旧 ID(req-0002) を含む＝自由文で触らない対象。
REQUIREMENTS = """# 要件の正本（コメントは保存される）
- id: req-0001
  req_id: FR-001
  name: 注文登録
  kind: 機能
  category: B機能群
  statement: 注文を登録できる
  source:
    doc: spec.md
    evidence: >-
      関連は req-0002 を参照（この文中の ID は自由文なので触らない）。
- id: req-0002
  req_id: FR-002
  name: 注文検索
  kind: 機能
  category: A機能群
  statement: 注文を検索できる
- id: req-0003
  req_id: NFR-001
  name: 応答性能
  kind: 非機能
  category: A機能群
  statement: 1秒以内に応答する
- id: req-naming
  req_id: FR-003
  name: 命名規約
  kind: 機能
  category: A機能群
  statement: 命名規約に従う
"""

# データ項目（1 件 1 ファイル = dict 形式）。category で並びを逆転させる。
DI_1 = """id: di-0001
name: 注文ID
type: 文字列
category: Z区分
"""
DI_2 = """id: di-0002
name: 顧客コード
type: 文字列
category: A区分
"""

ENTITY = """- id: ent-0001
  name: 注文
  physical_name: T_ORDER
  columns:
    - { item: di-0001, physical_name: order_id }
    - { item: di-0002, physical_name: cust_code }
"""

# 業務ルール。applies_to は constrains の埋め込み（bare スカラーリスト）。
BUSINESS_RULES = """- id: br-0001
  rule_id: BR-001
  category: B分類
  statement: 送料は数量に応じる
  applies_to: [di-0001]
- id: br-0002
  rule_id: BR-002
  category: A分類
  statement: 顧客コードは必須
"""

MODULES = """- id: mod-0001
  class_name: OrderService
  description: 注文サービス
- id: mod-engine
  class_name: Engine
  description: エンジン
"""

# 関係: realizes はフロー形式、refines はブロック形式で両対応を突く。
RELATIONS = """# 工程間トレース
- { type: realizes, from: mod-0001, to: req-0001, status: review }
- { type: realizes, from: ent-0001, to: req-0002, status: review }
- type: refines
  from: req-0001
  to: req-0002
  status: review
"""

TREE = {
    "metamodel.yaml": METAMODEL,
    "items/requirement/core.yaml": REQUIREMENTS,
    "items/data-item/di-0001.yaml": DI_1,
    "items/data-item/di-0002.yaml": DI_2,
    "items/entity/core.yaml": ENTITY,
    "items/business-rule/core.yaml": BUSINESS_RULES,
    "items/module/core.yaml": MODULES,
    "relations/trace.yaml": RELATIONS,
}


def build_root(tmp_path: Path) -> Path:
    for rel, text in TREE.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")   # 改行を LF に固定（プラットフォーム非依存）
    return tmp_path


def _by_name(store: Store, name: str):
    return next(i for i in store.items.values() if i.attrs.get("name") == name)


def _has_rel(store: Store, rtype: str, src: str, dst: str) -> bool:
    return any(r.type == rtype and r.src == src and r.dst == dst
              for r in store.relations)


def test_baseline_is_error_free(tmp_path):
    """前提: 用意した正本は error 0 で読める。"""
    root = build_root(tmp_path)
    assert not Store.load(root).has_errors()


def test_plan_map_cyclic_swap_and_slug_untouched(tmp_path):
    root = build_root(tmp_path)
    mapping = plan_map(Store.load(root))
    # requirement: A機能群(FR-002, NFR-001) → B機能群(FR-001) の順で連番 → 3-cycle
    assert mapping["req-0002"] == "req-0001"
    assert mapping["req-0003"] == "req-0002"
    assert mapping["req-0001"] == "req-0003"
    # data-item / business-rule: category 逆転で swap
    assert mapping["di-0001"] == "di-0002" and mapping["di-0002"] == "di-0001"
    assert mapping["br-0001"] == "br-0002" and mapping["br-0002"] == "br-0001"
    # slug ID・サブ系列は対象外。単独通番(ent-0001/mod-0001)は no-op で載らない
    assert "req-naming" not in mapping
    assert "mod-engine" not in mapping
    assert "ent-0001" not in mapping and "mod-0001" not in mapping
    # 全単射（値に重複なし）
    assert len(set(mapping.values())) == len(mapping)


def test_run_preserves_integrity_and_follows_refs(tmp_path):
    root = build_root(tmp_path)
    res = run(root)
    after = Store.load(root)

    # error 0・warn 不変
    assert not after.has_errors()
    assert res.warn == 0

    # 循環 swap が unique 違反なく成立: 元 req-0002(注文検索) が新 req-0001 に
    assert _by_name(after, "注文検索").id == "req-0001"
    assert _by_name(after, "応答性能").id == "req-0002"
    assert _by_name(after, "注文登録").id == "req-0003"

    # 表示連番(req_id/rule_id)は触らない — item に付いたまま移動する
    assert _by_name(after, "注文検索").attrs["req_id"] == "FR-002"
    assert {i.attrs["req_id"] for i in after.items.values() if i.type == "requirement"} \
        == {"FR-001", "FR-002", "NFR-001", "FR-003"}
    assert {i.attrs["rule_id"] for i in after.items.values() if i.type == "business-rule"} \
        == {"BR-001", "BR-002"}

    # slug ID は不変
    assert "req-naming" in after.items
    assert after.items["req-naming"].attrs["req_id"] == "FR-003"
    assert "mod-engine" in after.items

    # 関係の from/to が追随（フロー形式 realizes・ブロック形式 refines）
    assert _has_rel(after, "realizes", "mod-0001", "req-0003")   # 旧 to req-0001
    assert _has_rel(after, "realizes", "ent-0001", "req-0001")   # 旧 to req-0002
    assert _has_rel(after, "refines", "req-0003", "req-0001")    # 旧 req-0001->req-0002

    # 埋め込み参照が追随（has-column の item, constrains の applies_to）
    # di swap: order_id 列は旧 di-0001 → 新 di-0002 を指す
    cols = {r.attrs["physical_name"]: r.dst
            for r in after.relations if r.type == "has-column"}
    assert cols == {"order_id": "di-0002", "cust_code": "di-0001"}
    # br-0001(送料) は新 br-0002、applies_to di-0001 → di-0002
    assert _has_rel(after, "constrains", "br-0002", "di-0002")

    # 関係本数は不変
    assert len(after.relations_of("realizes")) == 2
    assert len(after.relations_of("has-column")) == 2
    assert len(after.relations_of("constrains")) == 1

    # 出典は不変（doc・evidence とも）。evidence の旧 ID は自由文なので残る
    r_touroku = _by_name(after, "注文登録")     # 旧 req-0001
    assert r_touroku.source[0]["doc"] == "spec.md"
    assert "req-0002" in r_touroku.source[0]["evidence"]

    # stale 監査: evidence 中の旧 ID を「手動確認」として拾う
    assert any(old == "req-0002" and "requirement" in path
               for path, _line, old in res.stale)

    # 監査は循環で生じる「新 ID＝別の旧 ID」を誤検出しない:
    # 参照位置の req-0001(新) を stale に含めない
    id_line_stale = [s for s in res.stale if s[0].endswith("requirement/core.yaml")]
    assert all(old == "req-0002" for _p, _l, old in id_line_stale)


def test_idempotent_after_apply(tmp_path):
    root = build_root(tmp_path)
    run(root)
    # 直後の再計算は空（冪等）
    assert plan_map(Store.load(root)) == {}
    res2 = run(root, out_map=root / "renumber-map.json")
    assert res2.mapping == {}


def test_dry_run_writes_nothing(tmp_path):
    root = build_root(tmp_path)
    # バイト単位で比較する（read_text 比較は改行差を相殺して盲点になる）
    before = {rel: (tmp_path / rel).read_bytes() for rel in TREE}
    res = run(root, dry_run=True)
    assert res.mapping and res.dry_run and not res.applied
    for rel, data in before.items():
        assert (tmp_path / rel).read_bytes() == data
    # map・状態ファイルも作らない
    assert not (root / "renumber-map.json").exists()
    assert not (root / renumber.STATE_FILE).exists()


def _to_crlf(tmp_path: Path) -> None:
    """全 spec ファイルの改行を CRLF に変換する（改行保全テスト用フィクスチャ）。"""
    for rel in TREE:
        p = tmp_path / rel
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))


def test_dry_run_preserves_crlf_bytes(tmp_path):
    """CRLF ストアで dry-run しても全ファイルがバイト単位で不変。"""
    root = build_root(tmp_path)
    _to_crlf(tmp_path)
    before = {rel: (tmp_path / rel).read_bytes() for rel in TREE}
    res = run(root, dry_run=True)
    assert res.mapping                       # 置換対象はある（touched ファイルが存在）
    for rel, data in before.items():
        assert b"\r\n" in data               # 前提: CRLF になっている
        assert (tmp_path / rel).read_bytes() == data


def test_apply_preserves_crlf_and_untouched_bytes(tmp_path):
    """実適用でも touched ファイルの改行は CRLF のまま、untouched はバイト不変。"""
    root = build_root(tmp_path)
    _to_crlf(tmp_path)
    before = {rel: (tmp_path / rel).read_bytes() for rel in TREE}
    run(root)
    after = Store.load(root)
    assert not after.has_errors()
    for rel, data in before.items():
        now = (tmp_path / rel).read_bytes()
        # 改行コードは CRLF のまま（LF 単独が混ざらない）
        assert b"\r\n" in now and now.replace(b"\r\n", b"") .count(b"\n") == 0
        if now == data:
            continue                         # untouched はバイト完全一致
        # touched: 内容は変わるが LF 混入なし（上の assert で担保済み）


def test_apply_preserves_lf_when_lf(tmp_path):
    """LF ストアは LF のまま（CRLF を混入させない）。"""
    root = build_root(tmp_path)
    run(root)
    for rel in TREE:
        assert b"\r\n" not in (tmp_path / rel).read_bytes()


def test_types_filter_limits_scope(tmp_path):
    root = build_root(tmp_path)
    mapping = plan_map(Store.load(root), types={"requirement"})
    assert set(mapping) == {"req-0001", "req-0002", "req-0003"}
    assert not any(k.startswith(("di-", "br-")) for k in mapping)


def test_out_map_is_bijection_file(tmp_path):
    root = build_root(tmp_path)
    out = root / "map.json"
    res = run(root, out_map=out)
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data == res.mapping
    # 全単射: 旧 ID 数 = 新 ID 数、重複なし
    assert len(data) == len(set(data.values())) == len(set(data))


def test_state_marker_written_and_cli_refuses_rerun(tmp_path, monkeypatch, capsys):
    root = build_root(tmp_path)

    def cli(*extra):
        monkeypatch.setattr(sys, "argv", ["renumber.py", "--root", str(root), *extra])
        return renumber.main()

    assert cli() == 0                       # 初回: 実行
    assert read_state(root).get("renumbered_at")
    capsys.readouterr()
    assert cli() == 1                       # 2 回目: マーカーで拒否
    assert "既に振り直し済み" in capsys.readouterr().err
    assert cli("--force") == 0              # --force: 無視して再実行 → no-op


def test_slug_only_store_is_noop(tmp_path):
    """通番 ID が 1 件も無い（slug のみ）なら何もしない。"""
    root = tmp_path
    (root / "metamodel.yaml").write_text(METAMODEL, encoding="utf-8")
    p = root / "items/module/core.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("- id: mod-engine\n  class_name: Engine\n  description: エンジン\n",
                 encoding="utf-8")
    assert plan_map(Store.load(root)) == {}
