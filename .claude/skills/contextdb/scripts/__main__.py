# -*- coding: utf-8 -*-
"""contextdb ツール群の統一エントリポイント — ディレクトリごと実行する

    python <contextdbツールのディレクトリ> <サブコマンド> [オプション...]

例（プロジェクトルートで）:
    python contextdb engine                      # 検証レポート + 統計
    python .claude/skills/contextdb engine       # スキル展開先でも同じ形式
    python .github/skills/contextdb sync-check   # プラットフォームはパス先頭だけの差

サブコマンドは同じディレクトリの各ツール *.py にそのまま委譲するので、
従来の `python <dir>/engine.py ...` 形式も引き続き使える。
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _force_utf8_io() -> None:
    """非 UTF-8 コンソール (Windows 既定の cp932 等) でも非 ASCII 出力で
    クラッシュしないよう、標準出力/標準エラーを UTF-8・エラー耐性つきに再設定する。

    em-dash (—) など cp932 に無い文字を print した際の UnicodeEncodeError を防ぐ。
    ``PYTHONIOENCODING=utf-8`` を毎回外から設定するのと同じ効果を、利用者に
    意識させずコード側で恒常的に効かせる。各 contextdb ツールはこの統一エントリ
    (または venv コマンド contextdb) 経由で実行されるため、ここで一度適用すれば覆える。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="backslashreplace")
        except (ValueError, OSError):
            pass


_force_utf8_io()

# サブコマンド -> (ツールファイル, 一行説明)。usage の表示順を兼ねる。
COMMANDS: dict[str, tuple[str, str]] = {
    "init": ("init.py", "消費側プロジェクトに空の .contextdb seed を作る"),
    "engine": ("engine.py", "検証レポート + 統計（error で exit 1）"),
    "generate": ("generate.py", "設計書を out/ に生成"),
    "conform": ("conform.py", "標準パック準拠検証（L1+L2+lock）"),
    "pack": ("pack.py", "標準パック補助操作（pack lock / check / build）"),
    "aggregate": ("aggregate.py", "複数プロジェクトの横断集計台帳"),
    "diff": ("diff.py", "ベースライン差分"),
    "history": ("history.py", "変更履歴（Git から意味的に再構成）"),
    "list": ("list.py", "アイテム/関係を status 等で絞って列挙"),
    "visualize": ("visualize.py", "対話型グラフビューア out/contextdb.html"),
    "sync-check": ("sync_check.py", "実装と正本の乖離を検出"),
    "quality": ("quality_check.py", "見出し・本文の品質を検出（命名/重複）"),
    "mutate": ("mutate.py", "アイテム/関係の追加・変更・承認"),
    "renumber": ("renumber.py", "通番 ID をレビュー後に一度だけ機能ごとの連番へ振り直す"),
    "resequence": ("resequence.py", "表示連番をカテゴリ接頭辞つき安定連番へ再採番（案C）"),
}
ALIASES = {"validate": "engine", "sync_check": "sync-check"}


def _usage(stream) -> None:
    print(f"使い方: python {HERE.name} <サブコマンド> [オプション...]", file=stream)
    print("サブコマンド:", file=stream)
    for name, (_, desc) in COMMANDS.items():
        print(f"  {name:<11} {desc}", file=stream)
    print("各サブコマンドの詳細: python "
          f"{HERE.name} <サブコマンド> --help", file=stream)


def main(argv: list[str]) -> int:
    if not argv:
        _usage(sys.stderr)
        return 2
    if argv[0] in ("-h", "--help"):
        _usage(sys.stdout)
        return 0
    cmd = ALIASES.get(argv[0], argv[0])
    if cmd not in COMMANDS:
        print(f"不明なサブコマンド: {argv[0]}", file=sys.stderr)
        _usage(sys.stderr)
        return 2
    script = HERE / COMMANDS[cmd][0]
    # ツール同士の import（sync_check -> engine 等）を場所に依らず成立させる
    sys.path.insert(0, str(HERE))
    sys.argv = [str(script)] + argv[1:]
    runpy.run_path(str(script), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
