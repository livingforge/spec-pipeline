"""eval ハーネス (run_eval.py) のスモークテスト。

同梱テストと配布バンドルの両レイアウトで run_eval.py を探し、宣言済みケースが
すべて pass する (終了コード 0) ことを確認する。eval 資産が壊れていないことの
最低限の回帰ガード。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()


def _find_run_eval() -> Path | None:
    # リポジトリ: <root>/src/skills/docextract/scripts/eval/run_eval.py
    # バンドル:   <root>/scripts/eval/run_eval.py (このテストは scripts/tests/)
    candidates = [
        _HERE.parents[1] / "src" / "skills" / "docextract" / "scripts" / "eval" / "run_eval.py",
        _HERE.parents[1] / "eval" / "run_eval.py",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def test_eval_cases_all_pass():
    run_eval = _find_run_eval()
    if run_eval is None:
        pytest.skip("run_eval.py が見つからない (このレイアウトには eval 資産が無い)")
    proc = subprocess.run(
        [sys.executable, str(run_eval), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, f"eval が失敗:\n{proc.stdout}\n{proc.stderr}"
