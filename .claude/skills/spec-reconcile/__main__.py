# -*- coding: utf-8 -*-
"""`python <スキルディレクトリ> <サブコマンド> ...` の入口。実体は scripts/__main__.py。

build_skill.py が生成する転送ファイル。直接編集しない。
"""
import runpy
import sys
from pathlib import Path

_main = Path(__file__).resolve().parent / "scripts" / "__main__.py"
sys.path.insert(0, str(_main.parent))
sys.argv[0] = str(_main)
runpy.run_path(str(_main), run_name="__main__")
