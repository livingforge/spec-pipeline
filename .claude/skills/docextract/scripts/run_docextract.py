"""docextract スキルのエントリポイント。

スキル内に同梱された docextract パッケージを sys.path に追加して CLI を起動する。
使い方: python run_docextract.py <入力ファイル...> -o <出力ディレクトリ>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docextract.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
