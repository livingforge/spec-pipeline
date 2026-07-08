"""スキル統一エントリポイント (scripts/__main__.py) のディスパッチ仕様を固定する。

`python <スキルディレクトリ> <サブコマンド>` 形式の入口。ツール本体への委譲は
ブートストラップ (venv 準備) を伴うため、ここではディスパッチャ自身の挙動
（usage・終了コード・サブコマンド表）だけを検証する。
リポジトリ (tests/) とスキルバンドル (scripts/tests/) の両レイアウトに対応する。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve()
_CANDIDATES = [
    _here.parents[1],  # バンドル: scripts/tests/ -> scripts/
    _here.parents[1] / "src" / "skills" / "docextract" / "scripts",  # リポジトリ
]
SCRIPTS_DIR = next((p for p in _CANDIDATES if (p / "__main__.py").is_file()), None)

pytestmark = pytest.mark.skipif(
    SCRIPTS_DIR is None, reason="scripts/__main__.py が見つからない"
)


def run_cli(*args):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPTS_DIR), *args],
        capture_output=True, encoding="utf-8", errors="replace", env=env,
    )


def test_no_args_shows_usage_and_exits_2():
    r = run_cli()
    assert r.returncode == 2
    assert "サブコマンド" in r.stderr


def test_help_lists_commands_and_exits_0():
    r = run_cli("--help")
    assert r.returncode == 0
    assert "extract" in r.stdout
    assert "docagent" in r.stdout
    assert "setup" in r.stdout


def test_setup_check_reports_state():
    """setup --check は無変更の状態確認。構築済み環境（テストは共有 venv で
    走る前提）では exit 0 と各項目の報告を返す。"""
    venv_scripts = Path(sys.executable).parent
    if not (venv_scripts / ("contextdb.exe" if sys.platform == "win32" else "contextdb")).is_file():
        pytest.skip("共有 venv が未構築（venv コマンド不在）")
    r = run_cli("setup", "--check")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "venv コマンド contextdb" in r.stdout
    assert "構築済み" in r.stdout


def test_unknown_subcommand_exits_2():
    r = run_cli("no-such-tool")
    assert r.returncode == 2
    assert "不明なサブコマンド" in r.stderr
