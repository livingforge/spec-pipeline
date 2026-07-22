# -*- coding: utf-8 -*-
"""resequence.py（案C: カテゴリ接頭辞つき表示連番）のテスト。

(category, kind) ごと 001 連番・安定格納・uniqueness、略号未定義/衝突の停止、
add-item の続き番号自動採番、id/関係/出典の不変、dry-run バイト保全、deprecated 除外を固定する。
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import resequence                                       # noqa: E402
from engine import Store                                # noqa: E402
from mutate import Editor                               # noqa: E402
from resequence import ResequenceError, plan_display, run   # noqa: E402

METAMODEL = """
version: 1
item_types:
  requirement:
    label: 要件
    label_field: name
    id_prefix: req-
    sequence:
      attribute: req_id
      by: kind
      prefix_from: category
      format: { 機能: "FR-{:03d}", 非機能: "NFR-{:03d}" }
    attributes:
      req_id:    { kind: string, required: true, unique: true }
      name:      { kind: string, required: true }
      kind:      { kind: enum, values: [機能, 非機能], required: true }
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
  realizes:
    from: module
    to: requirement
"""

REQUIREMENTS = """- id: req-0001
  req_id: FR-001
  name: 取込A
  kind: 機能
  category: 取込
  statement: s
  source:
    doc: x.md
    evidence: 出典の原文
- id: req-0002
  req_id: FR-002
  name: 基盤A
  kind: 機能
  category: 基盤
  statement: s
- id: req-0003
  req_id: FR-003
  name: 取込B
  kind: 機能
  category: 取込
  statement: s
- id: req-0004
  req_id: NFR-001
  name: 基盤NFR
  kind: 非機能
  category: 基盤
  statement: s
- id: req-dep
  req_id: FR-004
  name: 廃止機能
  kind: 機能
  category: 取込
  statement: s
  status: deprecated
"""

MODULE = """- id: mod-0001
  class_name: Svc
  description: サービス
"""
RELATIONS = "- { type: realizes, from: mod-0001, to: req-0001, status: review }\n"

DISPLAY = """version: 1
category_abbrev:
  取込: IMP
  基盤: CORE
"""

TREE = {
    "metamodel.yaml": METAMODEL,
    "display.yaml": DISPLAY,
    "items/requirement/core.yaml": REQUIREMENTS,
    "items/module/core.yaml": MODULE,
    "relations/trace.yaml": RELATIONS,
}


def build_root(tmp_path: Path, display: str = DISPLAY) -> Path:
    for rel, text in TREE.items():
        content = display if rel == "display.yaml" else text
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="\n")
    return tmp_path


def _rq(store, iid):
    return store.items[iid].attrs["req_id"]


def test_baseline_error_free(tmp_path):
    assert not Store.load(build_root(tmp_path)).has_errors()


def test_resequence_per_bucket_contiguous_and_stable(tmp_path):
    root = build_root(tmp_path)
    run(root)
    after = Store.load(root)
    assert not after.has_errors()
    # (category, kind) ごと 001 連番・安定格納
    assert _rq(after, "req-0001") == "IMP-FR-001"
    assert _rq(after, "req-0003") == "IMP-FR-002"
    assert _rq(after, "req-0002") == "CORE-FR-001"
    assert _rq(after, "req-0004") == "CORE-NFR-001"
    # deprecated は現状維持
    assert _rq(after, "req-dep") == "FR-004"
    # uniqueness 維持
    vals = [i.attrs["req_id"] for i in after.items.values() if i.type == "requirement"]
    assert len(vals) == len(set(vals))


def test_ids_relations_sources_unchanged(tmp_path):
    root = build_root(tmp_path)
    before_ids = set(Store.load(root).items)
    before_src = Store.load(root).items["req-0001"].source
    run(root)
    after = Store.load(root)
    assert set(after.items) == before_ids                       # id 不変
    assert len(after.relations_of("realizes")) == 1             # 関係不変
    assert after.relations[0].dst == "req-0001"
    assert after.items["req-0001"].source == before_src         # 出典不変


def test_missing_abbrev_stops(tmp_path):
    root = build_root(tmp_path, display="version: 1\ncategory_abbrev:\n  取込: IMP\n")
    with pytest.raises(ResequenceError) as e:
        plan_display(Store.load(root))
    assert "略号が未定義" in str(e.value) and "基盤" in str(e.value)


def test_abbrev_collision_detected(tmp_path):
    root = build_root(tmp_path,
                      display="version: 1\ncategory_abbrev:\n  取込: DUP\n  基盤: DUP\n")
    with pytest.raises(ResequenceError) as e:
        plan_display(Store.load(root))
    assert "衝突" in str(e.value)


def test_kindcode_collision_detected(tmp_path):
    # 略号 FR は区分コード FR と衝突 → 停止（FRC を使えという趣旨）
    root = build_root(tmp_path,
                      display="version: 1\ncategory_abbrev:\n  取込: FR\n  基盤: CORE\n")
    with pytest.raises(ResequenceError) as e:
        plan_display(Store.load(root))
    assert "区分コード" in str(e.value)


def test_add_item_continues_bucket_number(tmp_path):
    root = build_root(tmp_path)
    run(root)                                    # 取込/機能 は IMP-FR-002 まで
    ed = Editor(root)
    ed.add_item("requirement",
                {"name": "取込C", "kind": "機能", "category": "取込", "statement": "s"},
                {"doc": "x.md", "evidence": "e"})
    new = next(i for i in Store.load(root).items.values() if i.attrs.get("name") == "取込C")
    assert new.attrs["req_id"] == "IMP-FR-003"   # その (category,kind) の続き番号


def test_add_item_fallback_without_display(tmp_path):
    """display.yaml が無ければ接頭辞なしの従来挙動（既存データ非破壊）。"""
    root = tmp_path
    (root / "metamodel.yaml").write_text(METAMODEL, encoding="utf-8", newline="\n")
    p = root / "items/requirement/core.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(REQUIREMENTS, encoding="utf-8", newline="\n")
    ed = Editor(root)
    ed.add_item("requirement",
                {"name": "新規", "kind": "機能", "category": "取込", "statement": "s"},
                {"doc": "x.md", "evidence": "e"})
    new = next(i for i in Store.load(root).items.values() if i.attrs.get("name") == "新規")
    assert new.attrs["req_id"] == "FR-005"       # 接頭辞なし・kind 別グローバル通し


def test_dry_run_preserves_bytes(tmp_path):
    root = build_root(tmp_path)
    before = {rel: (tmp_path / rel).read_bytes() for rel in TREE}
    res = run(root, dry_run=True)
    assert res.value_map and res.dry_run and not res.applied
    for rel, data in before.items():
        assert (tmp_path / rel).read_bytes() == data
    assert not (root / "resequence-map.json").exists()


def test_dry_run_preserves_crlf(tmp_path):
    root = build_root(tmp_path)
    for rel in TREE:                             # CRLF 化
        p = tmp_path / rel
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    before = {rel: (tmp_path / rel).read_bytes() for rel in TREE}
    run(root, dry_run=True)
    for rel, data in before.items():
        assert (tmp_path / rel).read_bytes() == data


def test_apply_preserves_crlf(tmp_path):
    root = build_root(tmp_path)
    for rel in TREE:
        p = tmp_path / rel
        p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))
    run(root)
    after = Store.load(root)
    assert not after.has_errors()
    body = (tmp_path / "items/requirement/core.yaml").read_bytes()
    assert b"IMP-FR-001" in body and b"\r\n" in body
    assert body.replace(b"\r\n", b"").count(b"\n") == 0        # LF 混入なし


def test_idempotent(tmp_path):
    root = build_root(tmp_path)
    run(root)
    assert plan_display(Store.load(root)) == {}                # 2 回目は空
