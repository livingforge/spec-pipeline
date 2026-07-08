# -*- coding: utf-8 -*-
"""横断集計 — 複数プロジェクトの仕様データを標準パック基準でまとめる

    python contextdb/aggregate.py <root1> <root2> ...        # Markdown をstdoutへ
    python contextdb/aggregate.py <root...> --out 台帳.md      # ファイルへ
    python contextdb/aggregate.py <root...> --type data-item  # 特定種別だけ

標準パック（jp-sier-std 等）を共有する複数プロジェクトが揃って初めて可能に
なる成果物 — 共通データ項目辞書・システム間IF台帳・拡張列挙の全社集計 — を
生成する。extensible enum は**標準宣言に無い値を「その他」に丸め**、元値の
内訳を付録に出す（設計メモ §6.1 決定事項 2）。

各プロジェクトは Store.load で読む（extends があれば実効メタモデルで検証済み）。
検証 error があるプロジェクトも集計対象には含めるが、冒頭に警告を出す。
"""
from __future__ import annotations

import sys
from pathlib import Path

from engine import Store

OTHER = "その他"


def _load_projects(roots: list[Path]) -> list[tuple[Path, Store]]:
    out = []
    for r in roots:
        if not (r / "metamodel.yaml").is_file():
            print(f"警告: {r} に metamodel.yaml が無い（スキップ）", file=sys.stderr)
            continue
        out.append((r, Store.load(r)))
    return out


def _standard_enums(projects: list[tuple[Path, Store]]) -> dict[tuple[str, str], set]:
    """チェーン上のパックが宣言する extensible enum の標準語彙 {(種別,属性): 値集合}。

    パックが宣言した値のみが「標準」。プロジェクトが独自拡張した値は含まれず、
    集計時に「その他」へ丸められる対象になる。
    """
    vocab: dict[tuple[str, str], set] = {}
    seen: set[str] = set()
    for _root, store in projects:
        for pack in store.packs:
            if pack.name in seen:
                continue
            seen.add(pack.name)
            for t, tdef in (pack.metamodel().get("item_types") or {}).items():
                for a, spec in (tdef.get("attributes") or {}).items():
                    if spec.get("kind") == "enum" and spec.get("extensible"):
                        vocab.setdefault((t, a), set()).update(spec.get("values") or [])
    return vocab


def _proj_name(root: Path) -> str:
    return root.resolve().name


def _types_present(projects, only: str | None) -> list[str]:
    types: list[str] = []
    for _r, store in projects:
        for t in store.mm.item_types:
            if t not in types and (only is None or t == only):
                types.append(t)
    return types


def _census(projects, item_type: str) -> list[str]:
    """1 種別の横断台帳。ラベルで束ね、複数プロジェクトに現れるものを共通とみなす。"""
    lines = []
    mm = projects[0][1].mm
    label = mm.item_types.get(item_type, {}).get("label", item_type)
    # label_value -> { project -> item }
    grouped: dict[str, dict[str, object]] = {}
    for root, store in projects:
        for it in store.items_of(item_type):
            grouped.setdefault(it.label(store.mm), {})[_proj_name(root)] = it
    if not grouped:
        return lines
    lines.append(f"### {label}（{item_type}）\n")
    lines.append("| 名称 | 出現プロジェクト | 共通 | 備考 |")
    lines.append("|------|------------------|:----:|------|")
    for name in sorted(grouped):
        projs = grouped[name]
        shared = "○" if len(projs) >= 2 else ""
        note = _conflict_note(projs, projects[0][1].mm)
        lines.append(f"| {name} | {'、'.join(sorted(projs))} | {shared} | {note} |")
    lines.append("")
    return lines


def _conflict_note(projs: dict, mm) -> str:
    """同名アイテムが属性で食い違う場合に指摘（型ゆらぎ等の横断品質チェック）。"""
    sigs = {}
    for pname, it in projs.items():
        sig = tuple(sorted((k, str(v)) for k, v in it.attrs.items()
                           if k not in ("name",)))
        sigs.setdefault(sig, []).append(pname)
    return "属性が不一致（要確認）" if len(sigs) > 1 else ""


def _enum_rollup(projects, vocab) -> list[str]:
    lines = []
    if not vocab:
        return lines
    lines.append("## 拡張列挙の全社集計\n")
    lines.append("標準宣言に無い値は「その他」に丸めています（元値は内訳に掲出）。\n")
    for (t, a), std in sorted(vocab.items()):
        counts: dict[str, int] = {}
        other_detail: dict[str, list[str]] = {}   # 元値 -> プロジェクト
        for root, store in projects:
            for it in store.items_of(t):
                v = it.attrs.get(a)
                if v is None:
                    continue
                if v in std:
                    counts[v] = counts.get(v, 0) + 1
                else:
                    counts[OTHER] = counts.get(OTHER, 0) + 1
                    other_detail.setdefault(str(v), []).append(_proj_name(root))
        if not counts:
            continue
        label_t = projects[0][1].mm.item_types.get(t, {}).get("label", t)
        lines.append(f"### {label_t}.{a}\n")
        lines.append("| 値 | 件数 |")
        lines.append("|----|-----:|")
        for v in sorted(std):
            lines.append(f"| {v} | {counts.get(v, 0)} |")
        if OTHER in counts:
            lines.append(f"| {OTHER} | {counts[OTHER]} |")
        lines.append("")
        if other_detail:
            lines.append(f"**「その他」の内訳（{label_t}.{a}）** — "
                         "標準への昇格候補:\n")
            lines.append("| 元値 | 件数 | 出現プロジェクト |")
            lines.append("|------|-----:|------------------|")
            for v in sorted(other_detail):
                ps = other_detail[v]
                lines.append(f"| {v} | {len(ps)} | {'、'.join(sorted(set(ps)))} |")
            lines.append("")
    return lines


def build_report(projects, only: str | None = None) -> str:
    lines = ["# 横断集計台帳\n",
             f"対象プロジェクト {len(projects)} 件。標準パックを共有する仕様データを"
             "横串で集計する。\n"]
    lines.append("## 対象プロジェクト\n")
    lines.append("| プロジェクト | 継承チェーン | アイテム | 検証 |")
    lines.append("|--------------|--------------|---------:|------|")
    for root, store in projects:
        chain = " → ".join(f"{p.name}@{p.version}" for p in store.packs) or "（なし）"
        health = "error あり" if store.has_errors() else "OK"
        lines.append(f"| {_proj_name(root)} | {chain} | {len(store.items)} | {health} |")
    lines.append("")

    lines.append("## 種別ごとの横断台帳\n")
    for t in _types_present(projects, only):
        lines += _census(projects, t)

    if only is None:
        lines += _enum_rollup(projects, _standard_enums(projects))
    return "\n".join(lines) + "\n"


def main() -> int:
    argv = sys.argv[1:]
    only = None
    out_path = None
    roots: list[Path] = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--type":
            only = argv[i + 1]; i += 2
        elif a == "--out":
            out_path = Path(argv[i + 1]); i += 2
        else:
            roots.append(Path(a)); i += 1
    if len(roots) < 1:
        print("使い方: contextdb aggregate <root1> <root2> ... [--type 種別] [--out file]",
              file=sys.stderr)
        return 2
    projects = _load_projects(roots)
    if not projects:
        print("集計対象のプロジェクトがない。", file=sys.stderr)
        return 1
    report = build_report(projects, only)
    if out_path:
        out_path.write_text(report, encoding="utf-8", newline="\n")
        print(f"生成しました: {out_path}（プロジェクト {len(projects)} 件）")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
