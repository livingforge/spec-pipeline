"""codescan のエントリポイント — ソースコードから骨格ファクトを決定論で洗い出す (L0)。

スキル内に同梱された docextract パッケージを sys.path に追加して CLI を起動する。
初回は共有仮想環境 (プロジェクトルート直下の .venv) を uv で用意し、その python で
実行し直す (_bootstrap 参照)。使い方: python run_codescan.py --dir <ソースルート>
"""

import sys
from pathlib import Path

_scripts = Path(__file__).resolve().parent
sys.path.insert(0, str(_scripts))

from _bootstrap import ensure_env

ensure_env(Path(__file__), _scripts / "requirements.txt")

from docextract.codescan import main

if __name__ == "__main__":
    raise SystemExit(main())
