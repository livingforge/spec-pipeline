# -*- coding: utf-8 -*-
"""pack migrate — パック改版の移行プラン適用（設計メモ §8）を固定する。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pack as packmod  # noqa: E402
from engine import Store  # noqa: E402

PACK_YAML = """pack: corp-std
version: "2.0.0"
description: 全社標準 v2
migrations:
  - { from: "2.*", to: "2.0", plan: migrations/to-2.0.json }
"""
MM = """version: 1
item_types:
  screen:
    label: 画面
    label_field: name
    id_prefix: scr-
    attributes:
      name:     { kind: string, required: true }
      priority: { kind: enum, values: [高, 中, 低], required: true }
"""
PLAN = ('{"ops": [{"op": "set-attr", "ref": "scr-0001", "attr": "priority", '
        '"value": "中", "to_review": false}]}')


def _proj() -> Path:
    root = Path(tempfile.mkdtemp(prefix="pack-migrate-"))
    for rel, text in {
        "packs/corp-std/pack.yaml": PACK_YAML,
        "packs/corp-std/metamodel/core.yaml": MM,
        "packs/corp-std/migrations/to-2.0.json": PLAN,
        "metamodel.yaml": "version: 1\nextends: corp-std@2.0\n",
        "items/screen/core.yaml":
            "- id: scr-0001\n  name: 一覧\n  priority: 低\n  status: approved\n",
    }.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def test_dry_run_does_not_change_data():
    root = _proj()
    rc = packmod._cmd_migrate(root, to_version="2.0", plan_name=None, dry_run=True)
    assert rc == 0
    assert Store.load(root).items["scr-0001"].attrs["priority"] == "低"


def test_apply_transforms_data():
    root = _proj()
    rc = packmod._cmd_migrate(root, to_version="2.0", plan_name=None, dry_run=False)
    assert rc == 0
    assert Store.load(root).items["scr-0001"].attrs["priority"] == "中"


def test_unknown_target_version_fails():
    root = _proj()
    rc = packmod._cmd_migrate(root, to_version="9.9", plan_name=None, dry_run=False)
    assert rc == 1


def test_named_plan_resolved_from_migrations_dir():
    root = _proj()
    rc = packmod._cmd_migrate(root, to_version=None, plan_name="to-2.0.json",
                              dry_run=False)
    assert rc == 0
    assert Store.load(root).items["scr-0001"].attrs["priority"] == "中"
