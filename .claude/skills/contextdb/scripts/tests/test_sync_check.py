# -*- coding: utf-8 -*-
"""sync_check.py（同期チェック）のテスト — ドリフト検出・棚卸し・出典鮮度を固定する。

リポジトリ (contextdb/tests/) とスキルバンドル (scripts/tests/) のどちらでも
「親ディレクトリに sync_check.py がある」前提で動く。
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sync_check import run_checks  # noqa: E402

METAMODEL = """
version: 1
item_types:
  module:
    label: モジュール
    label_field: name
    attributes:
      name: { kind: string, required: true }
      path: { kind: string, required: true }
  skill:
    label: スキル
    label_field: name
    attributes:
      name: { kind: string, required: true }
relation_types: {}
"""

MODULES = """
- id: mod-a
  name: モジュールA
  path: src/mod_a/
  status: approved
  source:
    doc: docs/design.md
    evidence: "モジュールAは | 表で説明 | されている"
- id: mod-gone
  name: 消えたモジュール
  path: src/gone/
  status: deprecated
  source: { doc: docs/design.md }
"""

SKILLS = """
- id: sk-a
  name: skill-a
  status: approved
  source: { doc: docs/design.md, evidence: "断片その1 … 断片その2" }
"""

DESIGN_MD = """# 設計
| モジュールAは | 表で説明 | されている |
断片その1 という文と、あいだに何か挟まって、断片その2 という文。
"""

SYNC_YAML = """
path_attributes: [path]
check_exists: ["module.path"]
inventory:
  - type: skill
    glob: "skills/*/skill.yaml"
    key: name
    value: "yaml:name"
"""


def build(tree: dict | None = None) -> Path:
    """base/ 直下に data/（仕様データ）とリポジトリのファイルを組み立てる。"""
    base = Path(tempfile.mkdtemp(prefix="contextdb-sync-"))
    defaults = {
        "data/metamodel.yaml": METAMODEL,
        "data/sync.yaml": SYNC_YAML,
        "data/items/module/core.yaml": MODULES,
        "data/items/skill/core.yaml": SKILLS,
        "docs/design.md": DESIGN_MD,
        "src/mod_a/a.py": "print()\n",
        "skills/skill-a/skill.yaml": "name: skill-a\n",
    }
    for rel, text in {**defaults, **(tree or {})}.items():
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return base


def kinds(report: dict) -> dict:
    return report["counts"]


def find(report: dict, kind: str) -> list[dict]:
    return [f for f in report["findings"] if f["kind"] == kind]


def test_clean_tree_has_no_findings():
    base = build()
    report = run_checks(base / "data", files=[])
    assert report["findings"] == [], report["findings"]


def test_drift_flags_items_referencing_changed_files():
    base = build()
    report = run_checks(base / "data", files=["src/mod_a/a.py", "docs/design.md"])
    stale = find(report, "stale")
    wheres = {f["where"] for f in stale}
    assert "mod-a" in wheres        # path 属性経由（配下のファイル変更）
    assert "sk-a" in wheres         # source.doc 経由


def test_drift_ignores_changes_inside_data_root():
    base = build()
    report = run_checks(base / "data", files=["data/items/module/core.yaml"])
    assert find(report, "stale") == []


def test_inventory_unregistered_and_vanished():
    base = build({
        "skills/skill-new/skill.yaml": "name: skill-new\n",   # 実体のみ → 未登録
        "data/items/skill/core.yaml": SKILLS + """
- id: sk-ghost
  name: skill-ghost
  status: review
  source: { doc: docs/design.md }
""",                                                          # アイテムのみ → 実体なし
    })
    report = run_checks(base / "data", files=[])
    assert {f["where"] for f in find(report, "unregistered")} \
        == {"skills/skill-new/skill.yaml"}
    assert {f["where"] for f in find(report, "vanished")} == {"sk-ghost"}


def test_vanished_skips_deprecated_and_empty_observation():
    # mod-gone は deprecated なので dead-path / vanished の対象外。
    # また glob が何も観測しないとき（設定ミス）は vanished を出さない
    base = build({"data/sync.yaml": SYNC_YAML.replace("skills/*", "nowhere/*")})
    report = run_checks(base / "data", files=[])
    assert find(report, "vanished") == []


def test_dead_path_only_for_configured_types():
    base = build({"data/items/module/core.yaml":
                  MODULES.replace("src/mod_a/", "src/renamed/")})
    report = run_checks(base / "data", files=[])
    assert {f["where"] for f in find(report, "dead-path")} == {"mod-a"}


def test_dead_doc_and_stale_evidence():
    base = build({
        "data/items/skill/core.yaml": SKILLS + """
- id: sk-b
  name: skill-b
  status: review
  source: { doc: docs/missing.md }
- id: sk-c
  name: skill-c
  status: review
  source: { doc: docs/design.md, evidence: "文書に存在しない引用" }
""",
        "skills/skill-b/skill.yaml": "name: skill-b\n",
        "skills/skill-c/skill.yaml": "name: skill-c\n",
    })
    report = run_checks(base / "data", files=[])
    assert {f["where"] for f in find(report, "dead-doc")} == {"sk-b"}
    assert {f["where"] for f in find(report, "stale-evidence")} == {"sk-c"}


def test_evidence_matching_tolerates_markdown_and_ellipsis():
    # sk-a の evidence は表記号入り＋省略記号付きだが design.md に照合できる
    # （clean ツリーで stale-evidence が出ないことが正規化の仕様）
    base = build()
    assert find(run_checks(base / "data", files=[]), "stale-evidence") == []
