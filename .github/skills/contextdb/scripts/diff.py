# -*- coding: utf-8 -*-
"""ベースライン差分ビュー — 2 つのリビジョン間で仕様データの差分レポートを作る

    python contextdb/diff.py <基準リビジョン> [対象リビジョン]
    python contextdb/diff.py baseline/R1.0        # ベースライン → 作業ツリー
    python contextdb/diff.py --baselines          # ベースライン一覧
    python diff.py --root <データディレクトリ> …  # ツールとデータを分離して使う場合

ベースラインの作成は Git タグでよい:  git tag baseline/R1.0
対象リビジョンを省略すると作業ツリー（未コミットの変更を含む）と比較する。
レポートは Markdown で標準出力へ。ファイルにしたい場合はリダイレクトする。

エンジン同様、特定のアイテム種別の知識は持たない。
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

from engine import ROOT, Item, Relation, Store, parse_root


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd,
                          capture_output=True, text=True, encoding="utf-8")


def store_at(rev: str | None, data_root: Path) -> Store:
    """指定リビジョンの仕様データを読む。None なら作業ツリー。"""
    if rev is None:
        return Store.load(data_root)
    top = _git("rev-parse", "--show-toplevel", cwd=data_root).stdout.strip()
    if not top:
        sys.exit("Git リポジトリではないためリビジョン比較できない。")
    top = Path(top)
    # 以降の git はリポジトリルートで実行する（パス指定をルート相対に揃える）
    prefix = data_root.resolve().relative_to(top).as_posix()
    ls = _git("ls-tree", "-r", "--name-only", rev, "--", prefix, cwd=top)
    if ls.returncode != 0:
        sys.exit(f"リビジョン '{rev}' を読めない: {ls.stderr.strip()}")
    tmp = Path(tempfile.mkdtemp(prefix="contextdb-rev-"))
    for name in ls.stdout.splitlines():
        rel = name[len(prefix) + 1:]
        if rel != "metamodel.yaml" and not rel.startswith(("items/", "relations/")):
            continue
        show = _git("show", f"{rev}:{name}", cwd=top)
        if show.returncode != 0:
            sys.exit(f"git show 失敗 ({name}): {show.stderr.strip()}")
        dest = tmp / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(show.stdout, encoding="utf-8", newline="\n")
    if not (tmp / "metamodel.yaml").is_file():
        sys.exit(f"リビジョン '{rev}' に仕様データが無い"
                 f"（{prefix}/ が Git 管理されているか確認する。.gitignore の除外に注意）。")
    return Store.load(tmp)


# ---------- 差分計算 ----------

def _fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)


def _state(obj: Item | Relation) -> dict:
    """比較対象となる全フィールド（コア属性 + 宣言属性）。"""
    return {"status": obj.status, "source": obj.source, **obj.attrs}


def _field_changes(old: dict, new: dict) -> list[str]:
    return [f"`{k}`: {_fmt(old.get(k))} → {_fmt(new.get(k))}"
            for k in sorted(set(old) | set(new)) if old.get(k) != new.get(k)]


def _rel_key(r: Relation) -> tuple:
    return (r.type, r.src, r.dst)


def _label(store: Store, iid: str) -> str:
    item = store.items.get(iid)
    return item.label(store.mm) if item else iid


def _rel_desc(store: Store, r: Relation) -> str:
    rdef = store.mm.relation_types.get(r.type) or {}
    return (f"{_label(store, r.src)} —{rdef.get('label', r.type)}→ "
            f"{_label(store, r.dst)} (`{r.type}: {r.src} -> {r.dst}`)")


def diff_report(a: Store, b: Store, rev_a: str, rev_b: str) -> str:
    lines = ["# 仕様データ差分レポート", "",
             f"基準: `{rev_a}` → 対象: `{rev_b}`", ""]
    for name, s in ((rev_a, a), (rev_b, b)):
        errs = sum(1 for p in s.problems if p.level == "error")
        if errs:
            lines.append(f"> ⚠ `{name}` 側に検証 error が {errs} 件ある。")

    added = [b.items[i] for i in b.items.keys() - a.items.keys()]
    removed = [a.items[i] for i in a.items.keys() - b.items.keys()]
    changed = [(a.items[i], b.items[i]) for i in sorted(a.items.keys() & b.items.keys())
               if _state(a.items[i]) != _state(b.items[i]) or a.items[i].type != b.items[i].type]

    rels_a = {_rel_key(r): r for r in a.relations}
    rels_b = {_rel_key(r): r for r in b.relations}
    r_added = [rels_b[k] for k in sorted(rels_b.keys() - rels_a.keys())]
    r_removed = [rels_a[k] for k in sorted(rels_a.keys() - rels_b.keys())]
    r_changed = [(rels_a[k], rels_b[k]) for k in sorted(rels_a.keys() & rels_b.keys())
                 if _state(rels_a[k]) != _state(rels_b[k])]

    lines += ["",
              f"アイテム: 追加 {len(added)} / 削除 {len(removed)} / 変更 {len(changed)} — "
              f"関係: 追加 {len(r_added)} / 削除 {len(r_removed)} / 変更 {len(r_changed)}", ""]

    def type_label(store: Store, t: str) -> str:
        return (store.mm.item_types.get(t) or {}).get("label", t)

    if added:
        lines.append("## アイテムの追加")
        for i in sorted(added, key=lambda x: x.id):
            lines.append(f"- **{i.id}**（{type_label(b, i.type)}）: {i.label(b.mm)}")
        lines.append("")
    if removed:
        lines.append("## アイテムの削除")
        for i in sorted(removed, key=lambda x: x.id):
            lines.append(f"- **{i.id}**（{type_label(a, i.type)}）: {i.label(a.mm)}")
        lines.append("")
    if changed:
        lines.append("## アイテムの変更")
        for old, new in changed:
            lines.append(f"- **{new.id}**（{type_label(b, new.type)}）: {new.label(b.mm)}")
            if old.type != new.type:
                lines.append(f"  - 種別: {old.type} → {new.type}")
            lines += [f"  - {c}" for c in _field_changes(_state(old), _state(new))]
        lines.append("")
    if r_added:
        lines.append("## 関係の追加")
        lines += [f"- {_rel_desc(b, r)}" for r in r_added]
        lines.append("")
    if r_removed:
        lines.append("## 関係の削除")
        lines += [f"- {_rel_desc(a, r)}" for r in r_removed]
        lines.append("")
    if r_changed:
        lines.append("## 関係の変更")
        for old, new in r_changed:
            lines.append(f"- {_rel_desc(b, new)}")
            lines += [f"  - {c}" for c in _field_changes(_state(old), _state(new))]
        lines.append("")

    if not any((added, removed, changed, r_added, r_removed, r_changed)):
        lines.append("差分なし。")
    return "\n".join(lines) + "\n"


def main() -> int:
    # Windows の cp932 コンソールでは em-dash 等が書けないため UTF-8 で出す
    # (レポートはリダイレクトしてファイル化する使い方が主)。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_root, args = parse_root(sys.argv[1:])
    if args and args[0] == "--baselines":
        tags = _git("tag", "-l", "baseline/*", "--sort=-creatordate",
                    cwd=data_root).stdout.strip()
        print(tags or "ベースラインなし。作成: git tag baseline/<名前>")
        return 0
    if not args:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    rev_a = args[0]
    rev_b = args[1] if len(args) > 1 else None
    a = store_at(rev_a, data_root)
    b = store_at(rev_b, data_root)
    sys.stdout.write(diff_report(a, b, rev_a, rev_b or "作業ツリー"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
