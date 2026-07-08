# -*- coding: utf-8 -*-
"""統一エントリポイント (__main__.py) のテスト — `python <ディレクトリ> <サブコマンド>` を固定する。

リポジトリ (contextdb/tests/) とスキルバンドル (scripts/tests/) のどちらでも
「親ディレクトリに __main__.py と各ツールがある」前提で動く。
"""
import os
import subprocess
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parents[1]

METAMODEL = """
version: 1
item_types:
  thing:
    label: モノ
    label_field: name
    attributes:
      name: { kind: string, required: true }
"""


def run_cli(*args, cwd=None):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(TOOLS_DIR), *args],
        capture_output=True, encoding="utf-8", errors="replace",
        cwd=cwd, env=env,
    )


def test_no_args_shows_usage_and_exits_2(tmp_path):
    r = run_cli(cwd=tmp_path)
    assert r.returncode == 2
    assert "サブコマンド" in r.stderr


def test_help_lists_commands_and_exits_0(tmp_path):
    r = run_cli("--help", cwd=tmp_path)
    assert r.returncode == 0
    for name in ("engine", "generate", "diff", "history",
                 "visualize", "sync-check", "mutate"):
        assert name in r.stdout


def test_unknown_subcommand_exits_2(tmp_path):
    r = run_cli("no-such-tool", cwd=tmp_path)
    assert r.returncode == 2
    assert "不明なサブコマンド" in r.stderr


def test_engine_dispatch_validates_data_root(tmp_path):
    (tmp_path / "metamodel.yaml").write_text(METAMODEL, encoding="utf-8")
    items = tmp_path / "items" / "thing"
    items.mkdir(parents=True)
    (items / "core.yaml").write_text("- { id: t1, name: A }\n", encoding="utf-8")
    r = run_cli("engine", "--root", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 0, r.stderr


def test_alias_validate_maps_to_engine(tmp_path):
    (tmp_path / "metamodel.yaml").write_text(METAMODEL, encoding="utf-8")
    items = tmp_path / "items" / "thing"
    items.mkdir(parents=True)
    (items / "core.yaml").write_text("- { id: t1, name: A }\n", encoding="utf-8")
    r = run_cli("validate", "--root", str(tmp_path), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
