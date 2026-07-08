"""fact-reconcile (抽出ファクトの意味的名寄せ) のエントリポイント。

fact-reconcile スキルに同梱された factreconcile パッケージを sys.path に載せて
CLI を起動する。factreconcile は import 時に **docextract / docagent / docsummary
パッケージへ依存する** (docagent.facts / docagent.store / docextract.paths /
docsummary.providers / docsummary.settings)。これらはこのスキルには同梱せず、
同じプロジェクトに展開された **兄弟スキル docextract / docsummary** の scripts から
解決して sys.path に載せる (コピー同梱はせず実行時参照する)。初回は共有仮想環境
(プロジェクトルート直下の .venv) を uv で用意し、その python で実行し直す
(_bootstrap 参照)。共有 venv・依存インストールのマーカーは docextract と共用する
(skill="docextract") ので、docextract のセットアップ済み環境をそのまま再利用する。

PyYAML は `plan` サブコマンドでのみ必要で、通常は contextdb スキルが同じ共有 venv に
入れている (contextdb の requirements)。plan を使うには contextdb スキルの展開が前提。

使い方: python run_fact_reconcile.py <サブコマンド> [オプション]
"""

import sys
from pathlib import Path

_scripts = Path(__file__).resolve().parent
sys.path.insert(0, str(_scripts))  # factreconcile パッケージ (同梱) + _bootstrap

from _bootstrap import _project_root, ensure_env  # noqa: E402


def _add_sibling(root: Path, packages: tuple[str, ...], skill: str) -> Path | None:
    """packages がすべて揃う最初の場所を sys.path に載せて返す (開発リポジトリ /
    配布物の両対応)。開発リポジトリではトップレベル、配布物では兄弟スキルの
    scripts 配下に本体パッケージが同梱される。"""
    candidates = [
        root,
        root / ".claude" / "skills" / skill / "scripts",
        root / ".github" / "skills" / skill / "scripts",
    ]
    for base in candidates:
        if all((base / p / "__init__.py").is_file() for p in packages):
            sys.path.insert(0, str(base))
            return base
    return None


_root = _project_root(Path(__file__))
_dx = _add_sibling(_root, ("docextract", "docagent"), "docextract")
if _dx is None:
    raise SystemExit(
        "fact-reconcile: 依存する docextract / docagent が見つからない。"
        "同じプロジェクトに docextract スキルが展開されている必要がある。"
    )
if _add_sibling(_root, ("docsummary",), "docsummary") is None:
    raise SystemExit(
        "fact-reconcile: 依存する docsummary が見つからない (LLM 接続に使う)。"
        "同じプロジェクトに docsummary スキルが展開されている必要がある。"
    )

# requirements・依存インストールのマーカーは兄弟 docextract スキルと共用する。
ensure_env(Path(__file__), _dx / "requirements.txt", skill="docextract")

from factreconcile.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
