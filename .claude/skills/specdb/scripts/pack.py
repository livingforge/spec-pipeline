# -*- coding: utf-8 -*-
"""標準パックの補助操作

    python specdb/pack.py lock                 # pack.lock を解決結果から生成/更新
    python specdb/pack.py check <パックdir>      # パックのリリースチェック（block 規約等）
    python specdb/pack.py build <正本dir> --into <配布dir>  # 正本 specdb から配布物を生成
    python specdb/pack.py migrate --to 2.0 [--dry-run]      # パック改版の移行プランを適用

pack.lock は継承チェーンの解決結果（版・内容ハッシュ）を固定する。CI は
`specdb conform --frozen` で lock と実際の解決結果の一致を機械的に検査できる。
`pack check` はパック開発側のリリースチェック（設計メモ §6.3）: 文書テンプレートが
block 規約（cover / revision_history / toc / preface / chapters / appendix）を
満たすかを検査する。
"""
from __future__ import annotations

import sys
from pathlib import Path

import standard
from engine import Problem, parse_root

# block 規約（設計メモ §6.3）— 文書テンプレートが定義すべき標準 block
STD_BLOCKS = ("cover", "revision_history", "toc", "preface", "chapters", "appendix")


def _template_blocks(source: str) -> set[str]:
    """Jinja テンプレートが直接定義している block 名の集合（AST から）。"""
    from jinja2 import Environment, nodes
    ast = Environment().parse(source)
    return {n.name for n in ast.find_all(nodes.Block)}


def _cmd_lock(root: Path) -> int:
    problems: list[Problem] = []
    packs = standard.resolve_chain(root, problems)
    for p in problems:
        print(p, file=sys.stderr)
    if any(p.level == "error" for p in problems):
        print("チェーンを解決できないため lock を更新しなかった。", file=sys.stderr)
        return 1
    if not packs:
        print("extends が宣言されていない（lock は不要）。", file=sys.stderr)
        return 0
    lock = standard.write_lock(root, packs)
    chain = " → ".join(f"{p.name}@{p.version}" for p in packs)
    print(f"pack.lock を更新した: {lock}")
    print(f"  チェーン: {chain}")
    return 0


def _cmd_check(pack_dir: Path) -> int:
    problems: list[Problem] = []
    pack = standard._load_pack(pack_dir, None, str(pack_dir), problems)
    if pack is None:
        for p in problems:
            print(p, file=sys.stderr)
        return 1
    checked = 0
    for f in sorted(pack.documents_dir.glob("*.yaml")):
        import yaml
        with open(f, encoding="utf-8") as fh:
            doc = yaml.safe_load(fh) or {}
        tmpl = doc.get("template")
        tpath = pack.templates_dir / tmpl if tmpl else None
        if not tpath or not tpath.is_file():
            continue
        blocks = _template_blocks(tpath.read_text(encoding="utf-8"))
        # block 規約は「1 つでも標準 block を定義するテンプレート」に適用する
        # （Markdown 台帳のような非 block テンプレートは対象外）。
        if blocks & set(STD_BLOCKS):
            checked += 1
            missing = [b for b in STD_BLOCKS if b not in blocks]
            if missing:
                problems.append(Problem("warn", f"templates/{tmpl}",
                                        f"STD-W401 block 規約: {missing} が未定義"))
    for p in problems:
        print(p, file=sys.stderr)
    warns = sum(1 for p in problems if p.level == "warn")
    print(f"パック {pack.name}@{pack.version}: block 規約対象 {checked} 文書 / "
          f"警告 {warns} 件")
    return 1 if any(p.level == "error" for p in problems) else 0


def _cmd_build(authoring: Path, into: Path) -> int:
    """パック正本 specdb（authoring）から配布物 documents/ + conformance/ を生成し
    into（配布パックdir）へ配置する。§3.1 のパック自己正本化。"""
    import shutil
    import generate
    if not (authoring / "metamodel.yaml").is_file():
        print(f"{authoring} はパック正本 specdb ではない。", file=sys.stderr)
        return 2
    rc = generate.main(["--root", str(authoring)])
    if rc:
        return rc
    out = authoring / "out"
    copied = 0
    docs_out = out / "documents"
    if docs_out.is_dir():
        (into / "documents").mkdir(parents=True, exist_ok=True)
        for f in sorted(docs_out.glob("*.yaml")):
            shutil.copyfile(f, into / "documents" / f.name)
            copied += 1
    rules = out / "conformance" / "rules.yaml"
    if rules.is_file():
        (into / "conformance").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(rules, into / "conformance" / "rules.yaml")
        copied += 1
    print(f"配布物を更新した: {into}（{copied} ファイル）")
    return 0


def _cmd_migrate(root: Path, to_version: str | None, plan_name: str | None,
                 dry_run: bool) -> int:
    """パック改版の移行プラン（mutate プラン）をプロジェクト正本へ適用する。

    プランはパックの migrations/ に同梱し、pack.yaml の `migrations:` で
    { from, to, plan } を宣言する。--to で対象版を指定すると、現在のパック版に
    from がマッチするエントリの plan を選ぶ。--dry-run は適用後に巻き戻す。
    """
    import fnmatch
    import json
    from mutate import Editor, MutateError, apply_plan

    problems: list[Problem] = []
    packs = standard.resolve_chain(root, problems)
    for p in problems:
        print(p, file=sys.stderr)
    if not packs:
        print("extends が無い（移行対象のパックがない）。", file=sys.stderr)
        return 2
    pack = packs[0]
    plan_path = None
    if plan_name:
        cand = pack.dir / plan_name
        plan_path = cand if cand.is_file() else pack.dir / "migrations" / plan_name
    elif to_version:
        for m in pack.meta.get("migrations") or []:
            if str(m.get("to")) == to_version and fnmatch.fnmatch(pack.version,
                                                                  str(m.get("from", "*"))):
                plan_path = pack.dir / m["plan"]
                break
        if plan_path is None:
            print(f"{pack.name}@{pack.version} から --to {to_version} への移行プランが"
                  "pack.yaml の migrations に無い。", file=sys.stderr)
            return 1
    else:
        print("--to <版> か <プラン名> を指定する。", file=sys.stderr)
        return 2
    if not plan_path or not plan_path.is_file():
        print(f"移行プランが見つからない: {plan_path}", file=sys.stderr)
        return 1

    editor = Editor(root)
    try:
        with open(plan_path, encoding="utf-8") as f:
            apply_plan(editor, json.load(f))
    except (MutateError, json.JSONDecodeError, OSError) as e:
        editor.rollback()
        print(f"error: {e}", file=sys.stderr)
        return 1
    new_errors, _ = editor.validate()
    for op in editor.log:
        print(f"適用: {op}")
    if new_errors or dry_run:
        editor.rollback()
        if new_errors:
            for p in new_errors:
                print(p, file=sys.stderr)
            print("この移行で新たな error が生まれるため巻き戻した。", file=sys.stderr)
            return 1
        print(f"--dry-run のため巻き戻した（{len(editor.log)} 操作）。")
    else:
        print(f"移行を適用した（{len(editor.log)} 操作）。extends を @{to_version} 系へ"
              "更新し、conform で確認すること。")
    return 0


def main() -> int:
    argv = sys.argv[1:]
    # pack はサブアクションが先頭に来るため、--root <dir> をどこに書いても拾える
    # よう先頭へ寄せてから parse_root に渡す。
    if "--root" in argv:
        i = argv.index("--root")
        argv = argv[i:i + 2] + argv[:i] + argv[i + 2:]
    root, args = parse_root(argv)
    action = args[0] if args else None
    if action == "lock":
        return _cmd_lock(root)
    if action == "migrate":
        to_version = plan_name = None
        dry = False
        rest = args[1:]
        i = 0
        while i < len(rest):
            if rest[i] == "--to":
                to_version = rest[i + 1]; i += 2
            elif rest[i] == "--dry-run":
                dry = True; i += 1
            else:
                plan_name = rest[i]; i += 1
        return _cmd_migrate(root, to_version, plan_name, dry)
    if action == "build":
        if "--into" not in args:
            print("使い方: specdb pack build <正本dir> --into <配布dir>", file=sys.stderr)
            return 2
        authoring = Path(args[1]) if len(args) > 1 and not args[1].startswith("-") else root
        into = Path(args[args.index("--into") + 1])
        return _cmd_build(authoring, into)
    if action == "check":
        # check の対象パックは引数指定（無ければ解決チェーンの直近パック）
        if len(args) >= 2:
            return _cmd_check(Path(args[1]))
        problems: list[Problem] = []
        packs = standard.resolve_chain(root, problems)
        if not packs:
            print("check 対象のパックを指定するか、extends を宣言する。", file=sys.stderr)
            return 2
        return _cmd_check(packs[0].dir)
    print("使い方: specdb pack lock | specdb pack check <パックdir>", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
