"""docagent (集約 JSON のデータ操作 API) のエントリポイント。

スキル内に同梱された docagent パッケージを sys.path に追加して CLI を起動する。
カレントディレクトリに依存せず、どこから実行しても動く。
使い方: python run_docagent.py <サブコマンド> [オプション]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from docagent.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
