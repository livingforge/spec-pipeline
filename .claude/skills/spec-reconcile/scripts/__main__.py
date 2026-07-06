# -*- coding: utf-8 -*-
"""spec-reconcile スキルの統一エントリポイント — ディレクトリごと実行する

    python <スキルディレクトリ> analyze [オプション]   # facts を名寄せして reconcile.json を生成
    python <スキルディレクトリ> review  [reconcile.json] # 提案を人間可読で一覧
    python <スキルディレクトリ> plan    [オプション]     # reconcile.json → specdb mutate plan.json
    python <スキルディレクトリ> config  --check | --init # LLM 接続設定

サブコマンド (analyze/review/plan/config) は specreconcile パッケージ自身の CLI が
解釈する。ここでは実体のランチャー run_spec_reconcile.py へそのまま委譲する。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    script = HERE / "run_spec_reconcile.py"
    sys.path.insert(0, str(HERE))
    sys.argv = [str(script), *argv]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
