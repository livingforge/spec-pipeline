"""docagent コマンドラインインターフェース (データ操作 API の入口)。

    python -m docagent <サブコマンド> [オプション]

サブコマンド一覧:
  init            ストアと categories.json を初期化
  add             docextract の result.json を取り込み登録
  set-category    カテゴリを設定 (固定タクソノミー内)
  set-summary     要約・キーワードを設定
  set             カテゴリ/要約/キーワードをまとめて更新
  get             1 文書を表示
  text            本文テキストのみを出力 (座標等を除いた要約用の軽量ビュー)
  list            全文書を一覧
  query           条件で絞り込み
  stats           カテゴリ別・ステータス別の集計
  export          集約 JSON 全体を出力
  remove          文書を削除
  categories      タクソノミーの表示・追加・削除

すべてのサブコマンドは ``--json`` で機械可読な JSON を出力する
(エージェントはこれをパースして次の操作を決める)。``--store`` で保存先を変更できる。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .store import (
    DEFAULT_CATEGORIES,
    DEFAULT_STORE,
    DocAgentError,
    Library,
)


def _load(args: argparse.Namespace) -> Library:
    return Library.load(args.store, args.categories)


def _emit(obj, as_json: bool, human) -> None:
    if as_json:
        print(json.dumps(obj, ensure_ascii=False, indent=2))
    else:
        human(obj)


def _doc_line(d: dict) -> str:
    cat = d.get("category") or "—"
    status = d.get("status", "registered")
    summ = (d.get("summary") or "").replace("\n", " ")
    if len(summ) > 40:
        summ = summ[:40] + "…"
    return f"[{status:10}] {d['id']:24} {cat:12} {summ}"


# ── サブコマンド実装 ─────────────────────────────────────────
def cmd_init(args):
    lib = _load(args)
    lib.save()
    lib.save_categories()
    _emit(
        {"store": str(lib.path), "categories": lib.categories, "documents": len(lib.documents)},
        args.json,
        lambda o: print(
            f"初期化しました。\n  ストア: {o['store']}\n  カテゴリ: {', '.join(o['categories'])}\n"
            f"  登録済み文書: {o['documents']} 件"
        ),
    )


def cmd_add(args):
    lib = _load(args)
    entry = lib.add_from_result(args.result, overwrite=args.overwrite)
    lib.save()
    _emit(
        entry,
        args.json,
        lambda o: print(
            f"登録しました: {o['id']} (source={o['source']}, type={o['file_type']}, "
            f"status={o['status']})"
        ),
    )


def cmd_set_category(args):
    lib = _load(args)
    doc = lib.set_category(args.id, args.category, force=args.force)
    lib.save()
    _emit(doc, args.json, lambda o: print(f"カテゴリを設定: {o['id']} -> {o['category']} (status={o['status']})"))


def cmd_set_summary(args):
    lib = _load(args)
    keywords = _split_keywords(args.keywords)
    doc = lib.set_summary(args.id, args.text, keywords)
    lib.save()
    _emit(doc, args.json, lambda o: print(f"要約を設定: {o['id']} (status={o['status']})"))


def cmd_set(args):
    lib = _load(args)
    keywords = _split_keywords(args.keywords) if args.keywords is not None else None
    doc = lib.update(
        args.id,
        category=args.category,
        summary=args.summary,
        keywords=keywords,
        force=args.force,
    )
    lib.save()
    _emit(doc, args.json, lambda o: print(f"更新しました: {o['id']} (category={o['category']}, status={o['status']})"))


def cmd_get(args):
    lib = _load(args)
    doc = lib.get(args.id)
    _emit(doc, args.json, lambda o: print(json.dumps(o, ensure_ascii=False, indent=2)))


def cmd_text(args):
    lib = _load(args)
    doc = lib.extract_text(args.id, max_chars=args.max_chars)
    _emit(doc, args.json, lambda o: print(o["text"]))


def cmd_list(args):
    lib = _load(args)
    docs = lib.documents
    _emit(
        docs,
        args.json,
        lambda o: (
            print(f"登録文書 {len(o)} 件:")
            or [print("  " + _doc_line(d)) for d in o]
            or (print("  (なし)") if not o else None)
        ),
    )


def cmd_query(args):
    lib = _load(args)
    docs = lib.query(
        category=args.category, status=args.status, keyword=args.keyword, text=args.text
    )
    _emit(
        docs,
        args.json,
        lambda o: (
            print(f"該当 {len(o)} 件:") or [print("  " + _doc_line(d)) for d in o]
        ),
    )


def cmd_stats(args):
    lib = _load(args)
    s = lib.stats()

    def human(o):
        print(f"合計: {o['total']} 件")
        print("カテゴリ別:")
        for k, v in sorted(o["by_category"].items(), key=lambda x: -x[1]):
            print(f"  {k:14} {v}")
        print("ステータス別:")
        for k, v in o["by_status"].items():
            print(f"  {k:14} {v}")

    _emit(s, args.json, human)


def cmd_export(args):
    lib = _load(args)
    data = lib.export()
    if args.output:
        Path(args.output).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"書き出しました: {args.output} ({len(data['documents'])} 件)")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))


def cmd_remove(args):
    lib = _load(args)
    doc = lib.remove(args.id)
    lib.save()
    _emit(doc, args.json, lambda o: print(f"削除しました: {o['id']}"))


def cmd_categories(args):
    lib = _load(args)
    if args.action == "add" and args.name:
        lib.add_category(args.name)
        lib.save_categories()
        lib.save()
    elif args.action == "remove" and args.name:
        lib.remove_category(args.name)
        lib.save_categories()
        lib.save()
    _emit(
        lib.categories,
        args.json,
        lambda o: print("カテゴリ (固定タクソノミー):\n" + "\n".join(f"  - {c}" for c in o)),
    )


# ── 補助 ─────────────────────────────────────────────────────
def _split_keywords(value: str | None) -> list[str] | None:
    if value is None:
        return None
    return [k.strip() for k in value.split(",") if k.strip()]


def build_parser() -> argparse.ArgumentParser:
    # 共通オプションは親パーサにまとめ、各サブコマンドにも継承させることで
    # `--json` / `--store` をサブコマンドの前後どちらに置いても効くようにする。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=str(DEFAULT_STORE), help=f"集約 JSON の保存先 (既定 {DEFAULT_STORE})")
    common.add_argument("--categories", default=str(DEFAULT_CATEGORIES), help="タクソノミー定義ファイル")
    common.add_argument("--json", action="store_true", help="機械可読な JSON で出力")

    p = argparse.ArgumentParser(
        prog="docagent", description="集約 JSON ストアのデータ操作 API", parents=[common]
    )
    sub = p.add_subparsers(dest="command", required=True)

    def add(name, help_):
        return sub.add_parser(name, help=help_, parents=[common])

    add("init", "ストアと categories.json を初期化").set_defaults(func=cmd_init)

    sp = add("add", "docextract の result.json を取り込み登録")
    sp.add_argument("result", help="result.json のパス")
    sp.add_argument("--overwrite", action="store_true", help="同一 ID を上書き")
    sp.set_defaults(func=cmd_add)

    sp = add("set-category", "カテゴリを設定")
    sp.add_argument("id")
    sp.add_argument("category")
    sp.add_argument("--force", action="store_true", help="タクソノミー外でも許可")
    sp.set_defaults(func=cmd_set_category)

    sp = add("set-summary", "要約・キーワードを設定")
    sp.add_argument("id")
    sp.add_argument("--text", required=True, help="要約本文")
    sp.add_argument("--keywords", help="カンマ区切りのキーワード")
    sp.set_defaults(func=cmd_set_summary)

    sp = add("set", "カテゴリ/要約/キーワードをまとめて更新")
    sp.add_argument("id")
    sp.add_argument("--category")
    sp.add_argument("--summary")
    sp.add_argument("--keywords", help="カンマ区切りのキーワード")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_set)

    sp = add("get", "1 文書を表示")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_get)

    sp = add("text", "本文テキストのみを出力 (座標等を除いた要約用の軽量ビュー)")
    sp.add_argument("id")
    sp.add_argument("--max-chars", type=int, help="出力する最大文字数 (省略時は全文)")
    sp.set_defaults(func=cmd_text)

    add("list", "全文書を一覧").set_defaults(func=cmd_list)

    sp = add("query", "条件で絞り込み")
    sp.add_argument("--category")
    sp.add_argument("--status", choices=["registered", "analyzed"])
    sp.add_argument("--keyword")
    sp.add_argument("--text", help="要約・メタデータ・抜粋への部分一致")
    sp.set_defaults(func=cmd_query)

    add("stats", "カテゴリ別・ステータス別の集計").set_defaults(func=cmd_stats)

    sp = add("export", "集約 JSON 全体を出力")
    sp.add_argument("-o", "--output", help="書き出し先ファイル (省略時は標準出力)")
    sp.set_defaults(func=cmd_export)

    sp = add("remove", "文書を削除")
    sp.add_argument("id")
    sp.set_defaults(func=cmd_remove)

    sp = add("categories", "タクソノミーの表示・追加・削除")
    sp.add_argument("action", nargs="?", default="list", choices=["list", "add", "remove"])
    sp.add_argument("name", nargs="?")
    sp.set_defaults(func=cmd_categories)

    return p


def main(argv: list[str] | None = None) -> int:
    # Windows コンソール (cp932) でも日本語・記号を安全に出力するため UTF-8 に固定。
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except DocAgentError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
