# -*- coding: utf-8 -*-
"""agent-usage ツールの統一エントリポイント — ディレクトリごと実行する

    python <agent-usage のディレクトリ> <サブコマンド> [オプション...]

例（プロジェクトルートで）:
    python .claude/skills/agent-usage report
    python .github/skills/agent-usage report --days 30 --out out/

サブコマンドは同ディレクトリの各ツール *.py に委譲する。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _force_utf8_io() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


_force_utf8_io()

COMMANDS: dict[str, tuple[str, str]] = {
    "report": ("report.py", "Claude Code の利用実績を集計し summary.json + report.html を出力"),
}


def _usage(stream) -> None:
    print(f"使い方: python {HERE.name} <サブコマンド> [オプション...]", file=stream)
    print("サブコマンド:", file=stream)
    for name, (_, desc) in COMMANDS.items():
        print(f"  {name:<9} {desc}", file=stream)
    print(f"各サブコマンドの詳細: python {HERE.name} <サブコマンド> --help", file=stream)


def main(argv: list[str]) -> int:
    if not argv:
        _usage(sys.stderr)
        return 2
    if argv[0] in ("-h", "--help"):
        _usage(sys.stdout)
        return 0
    if argv[0] not in COMMANDS:
        print(f"不明なサブコマンド: {argv[0]}", file=sys.stderr)
        _usage(sys.stderr)
        return 2
    script = HERE / COMMANDS[argv[0]][0]
    sys.path.insert(0, str(HERE))
    sys.argv = [str(script)] + argv[1:]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
