"""codescan (L0 骨格ファクト) と .py 抽出器の仕様を固定する。

コード→仕様の逆方向パイプラインの入口:
- extract_python: ソースファイルを文書として ExtractionResult へ (docextract 互換)
- codescan.scan:  ast だけで骨格ファクト (エンティティ/データ項目/モジュール/
  メソッド + has-column/has-method/refines) を決定論で洗い出す
- シャードは既存の FactStore.merge (facts-merge) へ無改造で統合できる
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docextract import SUPPORTED_EXTENSIONS, identity
from docextract.codescan import make_shard, scan
from docextract.extractors import extract_python
from docextract.extractors.base import ImageSaver
from docagent.facts import FactStore

SAMPLE_MODELS = '''\
"""モデル。"""

from dataclasses import dataclass
from datetime import date


@dataclass
class Order:
    """注文。"""

    order_id: str
    amount: int
    ordered_on: date
    is_express: bool
'''

SAMPLE_SERVICE = '''\
"""サービス。"""

THRESHOLD = 10000


class OrderService:
    """注文の登録を担う。"""

    def register_order(self, order_id, amount):
        """注文を登録する。"""
        return Order(order_id=order_id, amount=amount)

    def _private_helper(self):
        pass


def standalone_util(x):
    """ユーティリティ。"""
    return x
'''


@pytest.fixture()
def src_tree(tmp_path: Path) -> Path:
    root = tmp_path / "app"
    root.mkdir()
    (root / "models.py").write_text(SAMPLE_MODELS, encoding="utf-8")
    (root / "service.py").write_text(SAMPLE_SERVICE, encoding="utf-8")
    return root


# ── extract_python (.py 抽出器) ──────────────────────────────
def test_py_is_supported_extension():
    assert ".py" in SUPPORTED_EXTENSIONS


def test_extract_python_elements(src_tree: Path, tmp_path: Path):
    result = extract_python(src_tree / "service.py", ImageSaver(tmp_path))
    assert result.file_type == "py"
    styles = [(e.style, e.location.get("line")) for e in result.elements]
    # モジュール docstring → トップレベル定数 → クラス → 関数 (出現順)
    assert styles[0] == ("module_doc", 1)
    assert ("module_body", 3) in styles          # THRESHOLD = 10000 を落とさない
    assert ("class", 6) in styles
    assert ("function", 17) in styles
    cls = next(e for e in result.elements if e.style == "class")
    assert cls.location["name"] == "OrderService"
    assert "register_order" in cls.content       # クラス全文が本文に入る


def test_extract_python_syntax_error(tmp_path: Path):
    bad = tmp_path / "bad.py"
    bad.write_text("def broken(:\n", encoding="utf-8")
    with pytest.raises(ValueError, match="構文エラー"):
        extract_python(bad, ImageSaver(tmp_path))


# ── codescan.scan (L0 骨格ファクト) ──────────────────────────
def test_scan_skeleton_facts(src_tree: Path):
    facts, skipped, excluded = scan(src_tree)
    assert skipped == []
    assert excluded == []
    by_type: dict[str, list] = {}
    for f in facts:
        by_type.setdefault(f["type"], []).append(f)

    # dataclass → エンティティ + データ項目 (型注釈は日本語型へ写像)
    ent = by_type["エンティティ"][0]
    assert ent["statement"].startswith("Order: 注文。")
    di_types = {f["statement"]: f for f in by_type["データ項目"]}
    assert "Order.order_id: 文字列" in di_types
    assert "Order.amount: 数値" in di_types
    assert "Order.ordered_on: 日付" in di_types
    assert "Order.is_express: 真偽" in di_types
    # エンティティ → has-column で全フィールドを参照
    col_refs = {r["to_ref"] for r in ent["refs"] if r["rel"] == "has-column"}
    assert col_refs == {"Order.order_id", "Order.amount",
                        "Order.ordered_on", "Order.is_express"}

    # 通常クラス → モジュール・クラス + has-method (公開のみ) + refines
    mod = by_type["モジュール・クラス"][0]
    assert mod["statement"].startswith("OrderService: ")
    rels = {(r["rel"], r["to_ref"]) for r in mod["refs"]}
    assert ("has-method", "OrderService.register_order(order_id, amount)") in rels
    assert ("refines", "Order") in rels          # クラス本体の Order 参照から
    assert not any("_private_helper" in r["to_ref"] for r in mod["refs"])

    # メソッド: クラスの公開メソッド + トップレベル関数
    sigs = {f["statement"].split(":")[0] for f in by_type["メソッド"]}
    assert "OrderService.register_order(order_id, amount)" in sigs
    assert "standalone_util(x)" in sigs

    # 出典: doc_id は identity.doc_id と同一体系、location は行番号、evidence はソース行
    assert ent["doc_id"] == identity.doc_id(src_tree / "models.py")
    assert ent["location"] == {"line": 8}
    assert ent["evidence"] == "class Order:"


def test_scan_skips_syntax_error_and_excluded_dirs(src_tree: Path):
    (src_tree / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    cache = src_tree / "__pycache__"
    cache.mkdir()
    (cache / "junk.py").write_text("class Junk:\n    pass\n", encoding="utf-8")
    facts, skipped, _ = scan(src_tree)
    assert [s[0] for s in skipped] == ["bad.py"]
    assert not any("Junk" in f["statement"] for f in facts)


def test_scan_excludes_tests_and_entrypoints_by_default(src_tree: Path):
    """テスト・エントリポイントは骨格の対象外 (既定) で、除外一覧に必ず出る。"""
    (src_tree / "test_service.py").write_text(
        "class TestOrder:\n    pass\n", encoding="utf-8")
    (src_tree / "conftest.py").write_text("x = 1\n", encoding="utf-8")
    tdir = src_tree / "tests"
    tdir.mkdir()
    (tdir / "helper.py").write_text("class Helper:\n    pass\n", encoding="utf-8")
    (src_tree / "__main__.py").write_text("print('hi')\n", encoding="utf-8")
    (src_tree / "_bootstrap.py").write_text(
        "def ensure_env():\n    pass\n", encoding="utf-8")
    facts, skipped, excluded = scan(src_tree)
    assert skipped == []
    roles = dict(excluded)
    assert roles["test_service.py"] == "テスト"
    assert roles["conftest.py"] == "テスト"
    assert roles["tests/helper.py"] == "テスト"          # tests/ 配下はファイル名を問わない
    assert roles["__main__.py"] == "エントリポイント"
    assert roles["_bootstrap.py"] == "エントリポイント"
    assert not any("TestOrder" in f["statement"] or "Helper" in f["statement"]
                   for f in facts)
    # 明示オプトインでのみ対象へ含められる
    facts2, _, excluded2 = scan(src_tree, include_tests=True, include_entrypoints=True)
    assert excluded2 == []
    assert any("TestOrder" in f["statement"] for f in facts2)


SAMPLE_INFRA = '''\
"""インフラ層と業務エンティティ。"""

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class Status(Enum):
    """状態。"""

    DRAFT = "draft"
    REVIEW = "review"
    APPROVED = "approved"


@dataclass
class ItemStore:
    """ストア (I/O ラッパ)。"""

    path: str

    def load(self):
        """読み込む。"""
        return None


class ParseError(ValueError):
    """解析エラー。"""


@dataclass
class Item:
    """アイテム。"""

    item_id: str
    status: Status
    kind: Literal["a", "b"]
    tags: list[str]
    limit: int = 50
'''


def test_scan_infra_classes_are_not_entities_and_domains_survive(tmp_path: Path):
    """B-2: ストア/エラー型はエンティティにしない。B-3: 値域・既定値・構造を保全する。"""
    root = tmp_path / "app"
    root.mkdir()
    (root / "infra.py").write_text(SAMPLE_INFRA, encoding="utf-8")
    facts, skipped, excluded = scan(root)
    assert skipped == [] and excluded == []
    by_type: dict[str, list] = {}
    for f in facts:
        by_type.setdefault(f["type"], []).append(f["statement"])

    # 業務エンティティは Item だけ。ストア/エラー型はモジュール・クラス扱い
    assert [s.split(":")[0] for s in by_type["エンティティ"]] == ["Item"]
    assert any(s.startswith("ItemStore:") for s in by_type["モジュール・クラス"])
    assert any(s.startswith("ParseError:") for s in by_type["モジュール・クラス"])

    di = set(by_type["データ項目"])
    assert "Item.status: 文字列 (domain: draft|review|approved)" in di  # Enum 参照
    assert "Item.kind: 文字列 (domain: a|b)" in di                      # Literal
    assert "Item.limit: 数値 (既定: 50)" in di                          # 既定値
    assert any(s.startswith("Item.tags: 文字列 (構造: list[str];") for s in di)
    # インフラ dataclass のフィールドはデータ項目にしない
    assert not any(s.startswith("ItemStore.") for s in di)


# ── シャード → FactStore.merge (facts-merge 互換) ─────────────
def test_shard_merges_into_fact_store(src_tree: Path, tmp_path: Path):
    facts, _, _ = scan(src_tree)
    shard_path = tmp_path / "shard.json"
    shard_path.write_text(json.dumps(make_shard(facts), ensure_ascii=False),
                          encoding="utf-8")
    store = FactStore.load(tmp_path / "facts.json")
    report = store.merge([shard_path])
    assert report["added"] == len(facts)
    assert "エンティティ" in store.item_types    # 語彙拡張が和集合で入る
    again = store.merge([shard_path])            # 冪等: 再統合は全件スキップ
    assert again["added"] == 0
    assert again["skipped"] == len(facts)
