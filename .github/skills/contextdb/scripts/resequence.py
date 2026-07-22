# -*- coding: utf-8 -*-
"""表示連番の再採番 CLI — 要件番号などを「カテゴリ接頭辞つき安定連番」へ揃える

    python contextdb/resequence.py --dry-run           # 再採番結果と検証だけ表示（無書込み）
    python resequence.py                               # 実行（対象は prefix_from 宣言のある種別）
    python resequence.py --types requirement            # 対象種別を限定
    python resequence.py --root <データディレクトリ> …

案C: 表示連番（sequence 属性。例 requirement.req_id）を
``<カテゴリ略号>-<区分コード>-<NNN>``（例 CORE-FR-001）へ、**(category, kind) ごとに
001 から連番**・**安定格納**する。id 本体・関係・埋め込み参照・出典は一切変えない
（req_id は誰も参照しないため純粋な値の付け替え）。

有効化は opt-in: 標準パックの sequence が ``prefix_from: category`` を宣言し、消費側が
``.contextdb/display.yaml`` に ``category_abbrev`` を置いたときだけ接頭辞がつく。
略号が引けないカテゴリがあれば**明示エラーで停止**（どのカテゴリに略号が要るか表示）。

安全弁は renumber と同じ: snapshot → その場書き換え → engine 再検証（新規 error 0・
warn 件数一致・種別別本数不変・値の一意）→ 崩れれば巻き戻す。改行コードはバイトで
温存し、``--dry-run`` は 1 バイトも書かない。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from engine import Store, display_abbrev, parse_root

DEFAULT_MAP = "resequence-map.json"
_FMT = re.compile(r"(.*)\{:0?(\d*)d\}(.*)")


class ResequenceError(Exception):
    pass


def _target_types(store: Store, types: set[str] | None):
    """prefix_from を宣言した sequence を持つ種別 [(種別, seq)…]。--types で限定可。"""
    out = []
    for t, tdef in store.mm.item_types.items():
        seq = tdef.get("sequence") or {}
        if seq.get("prefix_from") and (types is None or t in types):
            out.append((t, seq))
    return out


def _parse_fmt(fmt: str) -> tuple[str, int, str]:
    m = _FMT.fullmatch(fmt)
    if not m:
        raise ResequenceError(f"sequence format '{fmt}' を解釈できない")
    return m.group(1), int(m.group(2) or 1), m.group(3)


def plan_display(store: Store, types: set[str] | None = None) -> dict[str, str]:
    """対象種別の表示連番を再計算する全単射 {旧値: 新値}（旧==新は除く）。厳格。

    - active のみ採番（deprecated は現値を保持しつつ、同バケットの番号を予約して衝突回避）。
    - 略号が引けないカテゴリ／略号衝突／区分コードとの衝突があれば ResequenceError。
    """
    display = store.display
    value_map: dict[str, str] = {}
    missing: set = set()
    abbr_of: dict[str, object] = {}      # 略号 -> カテゴリ（衝突検査）
    kindcodes: set = set()               # format の区分コード（FR/NFR 等）
    collisions: list[str] = []

    for t, seq in _target_types(store, types):
        attr, by, pf = seq["attribute"], seq.get("by"), seq["prefix_from"]
        fmt_decl = seq["format"]
        items_by_type = [i for i in store.items_of(t)]

        def fmt_for(item):
            if isinstance(fmt_decl, dict):
                return fmt_decl.get(item.attrs.get(by), fmt_decl.get("default"))
            return fmt_decl

        # バケット (略号, 区分キー) ごとに active を集める
        buckets: dict = {}
        deprecated: dict = {}
        for it in items_by_type:
            cat = it.attrs.get(pf)
            abbr = display_abbrev(display, cat)
            if not abbr:
                missing.add("（未分類/None）" if cat in (None, "", "未分類") else str(cat))
                continue
            prev = abbr_of.setdefault(abbr, cat)
            if prev != cat:
                collisions.append(f"略号 '{abbr}' が '{prev}' と '{cat}' で重複")
            fmt = fmt_for(it)
            if fmt is None:
                raise ResequenceError(
                    f"種別 '{t}': {by}='{it.attrs.get(by)}' に対応する書式が format に無い")
            pre, width, post = _parse_fmt(fmt)
            kindcodes.add(pre.rstrip("-"))
            key = (abbr, fmt, pre, width, post)
            (deprecated if it.status == "deprecated" else buckets).setdefault(
                key, []).append(it)

        for key, items in buckets.items():
            abbr, fmt, pre, width, post = key
            # 同バケットの deprecated が使用中の番号を予約して衝突回避
            reserved = set()
            for it in deprecated.get(key, []):
                g = re.fullmatch(re.escape(f"{abbr}-{pre}") + r"(\d+)" + re.escape(post),
                                 str(it.attrs.get(attr, "")))
                if g:
                    reserved.add(int(g.group(1)))
            items.sort(key=store._display_key)
            n = 0
            for it in items:
                n += 1
                while n in reserved:
                    n += 1
                new = f"{abbr}-{pre}{n:0{width}d}{post}"
                old = str(it.attrs.get(attr, ""))
                if new != old:
                    value_map[old] = new

    if missing:
        raise ResequenceError(
            "略号が未定義のカテゴリがある（display.yaml の category_abbrev に追加する）:\n  "
            + "\n  ".join(sorted(missing)))
    bad = {a for a in abbr_of if a in kindcodes}
    if bad:
        collisions.append(f"略号が区分コードと衝突: {sorted(bad)}（別の略号にする。例 FR→FRC）")
    if collisions:
        raise ResequenceError("略号の衝突:\n  " + "\n  ".join(collisions))
    if len(set(value_map.values())) != len(value_map):
        raise ResequenceError("内部エラー: 新しい表示連番に重複がある")
    return value_map


# ---------- テキスト置換（表示連番属性の値のみ・書式/改行温存） ----------

def _value_re(attrs: set[str], value_map: dict[str, str]) -> re.Pattern:
    attr_alt = "|".join(re.escape(a) for a in sorted(attrs))
    val_alt = "|".join(re.escape(v) for v in sorted(value_map, key=len, reverse=True))
    return re.compile(
        r"(?P<lead>(?<![\w-])(?:" + attr_alt + r"):[ \t]*)(?P<v>" + val_alt + r")(?![\w-])")


def _item_files(root: Path) -> list[Path]:
    return sorted((root / "items").rglob("*.yaml"))


@dataclass
class Result:
    value_map: dict[str, str] = field(default_factory=dict)
    per_type: dict[str, int] = field(default_factory=dict)
    warn: int = 0
    applied: bool = False
    dry_run: bool = False
    out_map: Path | None = None


def run(root: Path, types: set[str] | None = None, dry_run: bool = False,
        out_map: Path | None = None) -> Result:
    store = Store.load(root)
    base_errors = {str(p) for p in store.problems if p.level == "error"}
    base_warn = sum(1 for p in store.problems if p.level == "warn")
    base_items = {t: len(store.items_of(t)) for t in store.mm.item_types}
    base_rels = {rn: len(store.relations_of(rn)) for rn in store.mm.relation_types}

    value_map = plan_display(store, types)
    per_type: dict[str, int] = {}
    attrs = {seq["attribute"] for _t, seq in _target_types(store, types)}
    # 種別別件数（どの type の値が動いたか）
    val_type = {str(i.attrs.get(seq["attribute"], "")): t
                for t, seq in _target_types(store, types) for i in store.items_of(t)}
    for old in value_map:
        t = val_type.get(old)
        if t:
            per_type[t] = per_type.get(t, 0) + 1

    if not value_map:
        return Result({}, warn=base_warn, dry_run=dry_run)

    val = _value_re(attrs, value_map)

    def sub(m: re.Match) -> str:
        return m.group("lead") + value_map[m.group("v")]

    snapshots: dict[Path, bytes] = {}
    for f in _item_files(root):
        old_bytes = f.read_bytes()
        new_text = val.sub(sub, old_bytes.decode("utf-8"))   # 値だけ置換・改行温存
        if new_text.encode("utf-8") != old_bytes:
            snapshots[f] = old_bytes
            f.write_bytes(new_text.encode("utf-8"))

    def rollback() -> None:
        for f, b in snapshots.items():
            f.write_bytes(b)

    # 再検証: 新規 error 0・warn 一致・件数不変・旧値が残らない
    try:
        after = Store.load(root)
        new_errors = [str(p) for p in after.problems
                      if p.level == "error" and str(p) not in base_errors]
        warn = sum(1 for p in after.problems if p.level == "warn")
        a_items = {t: len(after.items_of(t)) for t in after.mm.item_types}
        a_rels = {rn: len(after.relations_of(rn)) for rn in after.mm.relation_types}
        after_vals = {str(i.attrs.get(seq["attribute"], ""))
                      for t, seq in _target_types(after, types) for i in after.items_of(t)}
        reasons = []
        if new_errors:
            reasons += [f"新規 error: {e}" for e in new_errors]
        if warn != base_warn:
            reasons.append(f"warn 件数が変化した（{base_warn} → {warn}）")
        if a_items != base_items:
            reasons.append("アイテム本数が変化した")
        if a_rels != base_rels:
            reasons.append("関係本数が変化した")
        stuck = [old for old in value_map if old in after_vals]
        if stuck:
            reasons.append(f"旧い表示連番が残っている: {stuck[:5]}")
    except Exception as e:
        rollback()
        raise ResequenceError(f"再検証に失敗したため巻き戻した: {e}") from e
    if reasons:
        rollback()
        raise ResequenceError("再採番で整合が崩れるため巻き戻した:\n  " + "\n  ".join(reasons))

    if dry_run:
        rollback()
        return Result(value_map, per_type, warn, applied=False, dry_run=True)

    out_map = out_map or (root / DEFAULT_MAP)
    out_map.write_text(json.dumps(value_map, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    return Result(value_map, per_type, warn, applied=True, out_map=out_map)


def _report(res: Result) -> None:
    if not res.value_map:
        print("再採番の対象なし（既に希望どおり・prefix_from 宣言なし・冪等）。変更しない。")
        return
    print("再採番件数（種別別）:")
    for t, n in sorted(res.per_type.items()):
        print(f"  {t}: {n} 件")
    print(f"  合計: {len(res.value_map)} 件")
    print(f"検証: 新規 error 0 件 / warn {res.warn} 件（再採番前と一致）")
    if res.dry_run:
        print("--dry-run のため一切書き込んでいない（上記は適用した場合の結果）。")
    elif res.applied:
        print(f"map を書き出した: {res.out_map}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_root, rest = parse_root(sys.argv[1:])

    ap = argparse.ArgumentParser(
        prog="resequence.py",
        description="表示連番をカテゴリ接頭辞つき安定連番へ再採番する（案C）")
    ap.add_argument("--dry-run", action="store_true",
                    help="変更を書かず、再採番結果と検証だけ表示する")
    ap.add_argument("--out-map", metavar="PATH",
                    help=f"旧→新の対応マップ出力先（既定: <root>/{DEFAULT_MAP}）")
    ap.add_argument("--types", metavar="t1,t2,…",
                    help="対象種別を限定（既定は prefix_from 宣言のある全種別）")
    args = ap.parse_args(rest)

    types = ({t.strip() for t in args.types.split(",") if t.strip()}
             if args.types else None)
    out_map = Path(args.out_map) if args.out_map else None
    try:
        res = run(data_root, types=types, dry_run=args.dry_run, out_map=out_map)
    except ResequenceError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _report(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
