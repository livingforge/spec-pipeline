# -*- coding: utf-8 -*-
"""ID 振り直し CLI — レビュー後に一度だけ「通番 ID」を機能ごとの連番へ振り直す

    python contextdb/renumber.py --dry-run          # マップと検証結果だけ表示（無書込み）
    python renumber.py                              # 実行（既定で全種別）
    python renumber.py --types requirement,module   # 対象種別を限定
    python renumber.py --root <データディレクトリ> …

背景と設計判断:
    ID 本体（id_prefix + 通番）と表示連番（req_id 等の sequence 属性）は別系統。
    コードからの逆抽出では ID が抽出順に振られ「機能ごとの連番」にならない。
    そこで AI レビュー後に一度だけ ID 本体を (category, kind, 表示キー) 順の連番へ
    振り直す。関係の from/to・埋め込み参照・トレーサビリティは一切壊さない。

    mutate は op 毎に検証してロールバックするため、ID を 1 件ずつ変えると中間状態が
    必ず dangling（新規 error）になり巻き戻る。よって ID 全置換は独立サブコマンドで、
    ストア全体を 1 トランザクションとして変換し、最終状態だけを検証する。

対象と非対象（重要な設計方針）:
    対象は「現 ID が id_prefix + 数字のみ」に厳密一致する通番 ID **だけ**。
    slug ID（例 mod-reconcile）やサブ系列 ID（例 mod-code-0001）は、人間が意味を
    込めた安定 ID なので触らない（全単射マップにも載せない）。engine は ID 書式を
    検査しないため両者は同一ストア内で正当に共存する。

安全弁:
    1. 先に全単射マップ old->new を完成させる（部分適用しない）。
    2. 参照サイト（items の id / relations の from・to / 埋め込み参照）を
       単一パスで同時置換する（循環 a->b, b->a でも unique 違反を起こさない）。
       出典 evidence・自由文（open-issue.positions 等の block scalar）・コメント中の
       ID は触らず、置換後に旧 ID が残存していれば「手動確認」として警告する。
    3. その場で書き換え → engine で再検証。error が増えず warn 件数・種別別の
       アイテム/関係本数が振り直し前と一致するときだけ確定。ずれたら巻き戻す
       （mutate.Editor と同じ snapshot/rollback。Windows でディレクトリ atomic swap に
        依存しないための実装選択）。
    4. 実行後 renumber.state.yaml に記録し、再実行は既定で拒否（--force で無視）。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

from engine import Store, parse_root

STATE_FILE = "renumber.state.yaml"
DEFAULT_MAP = "renumber-map.json"


class RenumberError(Exception):
    pass


# ---------- 採番（全単射マップの構築） ----------

def _sort_key(store: Store, item) -> tuple:
    """並び順キー = (deprecated は末尾, category 無しは末尾, category, 現在の表示キー)。

    同一 category の active アイテムが ID 連番で連続することを最小要件とする。
    kind（機能/非機能）の並びは表示キー（req_id が FR-/NFR- を持つ）から従属的に決まる。
    """
    cat = item.attrs.get("category")
    return (item.status == "deprecated", cat is None, cat or "",
            store._display_key(item))


def plan_map(store: Store, types: set[str] | None = None) -> dict[str, str]:
    """通番 ID（id_prefix + 数字のみ）を種別ごとに連番へ振り直す全単射 old->new。

    - slug / サブ系列 ID は対象外（マップに載せない）。
    - old == new は除く（冪等・no-op 検出）。
    - ゼロ埋め桁は既存の最大桁に合わせ、件数増で不足するなら全体を広い桁へ統一する。
    """
    mapping: dict[str, str] = {}
    for t, tdef in store.mm.item_types.items():
        prefix = tdef.get("id_prefix")
        if not prefix or (types is not None and t not in types):
            continue
        pat = re.compile(re.escape(prefix) + r"(\d+)$")
        eligible: list[tuple] = []
        for item in store.items_of(t):
            m = pat.fullmatch(item.id)
            if m:                                  # 通番 ID のみ。slug/サブ系列は除外
                eligible.append((item, len(m.group(1))))
        if not eligible:
            continue
        eligible.sort(key=lambda pair: _sort_key(store, pair[0]))
        width = max(max(w for _, w in eligible), len(str(len(eligible))))
        for i, (item, _w) in enumerate(eligible, start=1):
            new = f"{prefix}{i:0{width}d}"
            if new != item.id:
                mapping[item.id] = new
    # 全単射の健全性: 値の重複は設計上起こらない（種別内で index は一意、種別間は
    # 接頭辞が異なる）。念のため裏取りする。
    if len(set(mapping.values())) != len(mapping):
        raise RenumberError("内部エラー: 新 ID に重複がある（全単射が壊れている）")
    return mapping


# ---------- テキスト置換（参照位置のみ・書式温存） ----------

_SCALAR_OPEN = re.compile(r":\s*[|>][0-9]*[-+]?\s*(#.*)?$")


def _value_re(mapping: dict[str, str]) -> re.Pattern:
    """参照位置（マッピングの値・フロー列挙・seq 要素）の ID にだけ当たる正規表現。

    lead でマッピング値の区切り（``:`` / ``[`` / ``,`` / 行頭 ``- ``）を捕まえて
    再出力する。左は区切りで、右はトークン境界 ``(?![\\w-])`` で仕切るので、
    ``mod-0001`` が ``mod-00011`` の一部に当たったり、prose 中の ID に当たったりしない。
    """
    alt = "|".join(re.escape(o) for o in sorted(mapping, key=len, reverse=True))
    return re.compile(
        r"(?P<lead>(?:[:\[,]|(?<=\s)-)[ \t]*)"
        r"(?P<id>" + alt + r")(?![\w-])")


def _token_re(mapping: dict[str, str]) -> re.Pattern:
    return re.compile(r"(?<![\w-])(" + "|".join(
        re.escape(o) for o in sorted(mapping, key=len, reverse=True)) + r")(?![\w-])")


def _transform(text: str, mapping: dict[str, str]) -> tuple[str, list[tuple[int, str]]]:
    """1 ファイル分を (置換後テキスト, 残存旧IDの [(行, 旧ID)…]) に変換する。

    - 置換は参照位置（マッピング値・フロー列挙・seq 要素）の ID のみ。書式温存。
    - block scalar（``>-`` / ``|`` の本文）とコメント・引用中など**参照でない位置**の
      旧 ID は触らず、その出現を stale として返す（open-issue.positions・evidence 等）。
    - stale は必ず**元テキスト**で判定する。循環（a->b, b->a）では新 ID が別の旧 ID と
      一致しうるため、置換後テキストを走査すると正当な新 ID を誤検出してしまう。
    """
    if not mapping:
        return text, []
    val, tok = _value_re(mapping), _token_re(mapping)
    out: list[str] = []
    stale: list[tuple[int, str]] = []
    scalar_indent: int | None = None       # block scalar 本文の最中なら基準インデント
    for lineno, raw in enumerate(text.splitlines(keepends=True), start=1):
        line = raw.rstrip("\r\n")           # 行末（\n / \r\n）は eol に退避して温存する
        eol = raw[len(line):]
        indent = len(line) - len(line.lstrip(" "))
        if scalar_indent is not None:
            if line.strip() == "" or indent > scalar_indent:
                stale += [(lineno, m.group(1)) for m in tok.finditer(line)]
                out.append(raw)             # スカラー本文 → 触らない
                continue
            scalar_indent = None            # デデント = スカラー終了。この行は通常処理
        replaced: list[tuple[int, int]] = []

        def sub(m: re.Match) -> str:
            replaced.append(m.span("id"))
            return m.group("lead") + mapping[m.group("id")]

        newline = val.sub(sub, line)
        # 置換されなかった（参照でない位置の）旧 ID を stale に
        stale += [(lineno, m.group(1)) for m in tok.finditer(line)
                  if not any(s <= m.start() < e for s, e in replaced)]
        out.append(newline + eol)
        if _SCALAR_OPEN.search(line):       # この行が block scalar を開く（値が >- 等）
            scalar_indent = indent
    return "".join(out), stale


def _rewrite_text(text: str, mapping: dict[str, str]) -> str:
    """参照位置の ID だけ置換して返す（stale を捨てる薄いラッパ）。"""
    return _transform(text, mapping)[0]


def _spec_files(root: Path) -> list[Path]:
    return (sorted((root / "items").rglob("*.yaml"))
            + sorted((root / "relations").rglob("*.yaml")))


# ---------- 実行（snapshot / 検証 / rollback） ----------

@dataclass
class Result:
    mapping: dict[str, str] = field(default_factory=dict)
    per_type: dict[str, int] = field(default_factory=dict)   # 種別別の振り直し件数
    warn: int = 0
    stale: list = field(default_factory=list)                # 手動確認が要る旧ID残存
    applied: bool = False
    dry_run: bool = False
    out_map: Path | None = None


def _counts(store: Store) -> tuple[dict, dict]:
    items = {t: len(store.items_of(t)) for t in store.mm.item_types}
    rels = {rn: len(store.relations_of(rn)) for rn in store.mm.relation_types}
    return items, rels


def run(root: Path, types: set[str] | None = None, dry_run: bool = False,
        out_map: Path | None = None) -> Result:
    """マップ構築 → その場置換 → 再検証 → 確定 or 巻き戻し。"""
    store = Store.load(root)
    if store.mm.namespaces and any(":" in iid for iid in store.items):
        raise RenumberError(
            "名前空間付きストアは未対応（items/<名前空間>/ を使うデータ）")
    base_errors = {str(p) for p in store.problems if p.level == "error"}
    base_warn = sum(1 for p in store.problems if p.level == "warn")
    base_items, base_rels = _counts(store)

    mapping = plan_map(store, types)
    if not mapping:
        return Result(mapping={}, warn=base_warn, dry_run=dry_run)   # no-op（冪等）

    per_type: dict[str, int] = {}
    for old, new in mapping.items():
        t = store.items[old].type
        per_type[t] = per_type.get(t, 0) + 1

    # snapshot（変更ファイルのみ）→ その場書き換え。stale は元テキストで判定して集める。
    # 改行コードは温存する: read_text の既定（ユニバーサル改行）は CRLF→LF に潰すため、
    # バイトで読み書きする。これで「dry-run は 1 バイトも書かない」「実適用も元の改行を
    # 保つ」を満たす（rollback は元バイト列をそのまま復元する）。
    snapshots: dict[Path, bytes] = {}
    stale: list[tuple[str, int, str]] = []
    for f in _spec_files(root):
        old_bytes = f.read_bytes()
        old_text = old_bytes.decode("utf-8")       # \r\n を潰さずに保持
        new_text, file_stale = _transform(old_text, mapping)
        stale += [(f.relative_to(root).as_posix(), n, old) for n, old in file_stale]
        if new_text != old_text:
            snapshots[f] = old_bytes
            f.write_bytes(new_text.encode("utf-8"))   # eol を含むテキストを無変換で書く

    def rollback() -> None:
        for f, b in snapshots.items():
            f.write_bytes(b)                          # 元バイト列をそのまま復元

    # 再検証: 新規 error 0・warn 件数一致・種別別の本数不変・ID 集合が期待どおり
    # （循環では新 ID が別の旧 ID と一致しうるので、集合の一致で全単射適用を裏取りする）
    expected_ids = (set(store.items) - set(mapping)) | set(mapping.values())
    try:
        after = Store.load(root)
        new_errors = [str(p) for p in after.problems
                      if p.level == "error" and str(p) not in base_errors]
        warn = sum(1 for p in after.problems if p.level == "warn")
        a_items, a_rels = _counts(after)
        reasons: list[str] = []
        if new_errors:
            reasons += [f"新規 error: {e}" for e in new_errors]
        if warn != base_warn:
            reasons.append(f"warn 件数が変化した（{base_warn} → {warn}）")
        if a_items != base_items:
            reasons.append(f"アイテム本数が変化した（{base_items} → {a_items}）")
        if a_rels != base_rels:
            reasons.append(f"関係本数が変化した（{base_rels} → {a_rels}）")
        if set(after.items) != expected_ids:
            missing = expected_ids - set(after.items)
            extra = set(after.items) - expected_ids
            reasons.append(f"ID 集合が期待と不一致（欠落 {sorted(missing)} / 余分 {sorted(extra)}）")
    except Exception as e:            # 検証中の想定外も巻き戻す
        rollback()
        raise RenumberError(f"再検証に失敗したため巻き戻した: {e}") from e
    if reasons:
        rollback()
        raise RenumberError(
            "振り直しで整合が崩れるため巻き戻した:\n  " + "\n  ".join(reasons))

    if dry_run:
        rollback()
        return Result(mapping, per_type, warn, stale, applied=False, dry_run=True)

    out_map = out_map or (root / DEFAULT_MAP)
    out_map.write_text(json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
    _write_state(root, mapping, out_map)
    return Result(mapping, per_type, warn, stale, applied=True, out_map=out_map)


# ---------- 「一度だけ」マーカー ----------

def _state_path(root: Path) -> Path:
    return root / STATE_FILE


def read_state(root: Path) -> dict:
    p = _state_path(root)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.is_file() else {}


def _write_state(root: Path, mapping: dict[str, str], out_map: Path) -> None:
    state = {
        "version": 1,
        "renumbered_at": datetime.now(timezone.utc).isoformat(),
        "renumber_map": out_map.name,
        "count": len(mapping),
    }
    _state_path(root).write_text(
        "# renumber（ID 一括振り直し）の実行記録。存在すると既定で再実行を拒否する。\n"
        + yaml.safe_dump(state, allow_unicode=True, sort_keys=False),
        encoding="utf-8")


# ---------- baseline タグ検出 ----------

def _baseline_tags(root: Path) -> list[str]:
    try:
        r = subprocess.run(["git", "tag", "--list", "baseline/*"],
                           cwd=str(root), capture_output=True,
                           text=True, encoding="utf-8")
    except (OSError, ValueError):
        return []
    if r.returncode != 0:
        return []
    return [t for t in r.stdout.split() if t]


# ---------- CLI ----------

def _report(res: Result) -> None:
    if not res.mapping:
        print("振り直し対象なし（既に希望順・通番 ID なし・冪等）。変更しない。")
        return
    print("振り直し件数（種別別）:")
    for t, n in sorted(res.per_type.items()):
        print(f"  {t}: {n} 件")
    print(f"  合計: {len(res.mapping)} 件")
    print(f"検証: 新規 error 0 件 / warn {res.warn} 件（振り直し前と一致）")
    if res.stale:
        print("手動確認が必要（自由文・コメントに旧 ID が残存。自動置換しない）:")
        for path, line, old in res.stale:
            print(f"  {path}:{line} に旧 ID '{old}'")
    else:
        print("自由文・コメントに旧 ID の残存なし。")
    if res.dry_run:
        print("--dry-run のため一切書き込んでいない（上記は適用した場合の結果）。")
    elif res.applied:
        print(f"map を書き出した: {res.out_map}")
        print(f"実行を記録した: {_state_path(res.out_map.parent) if res.out_map else ''}")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_root, rest = parse_root(sys.argv[1:])

    ap = argparse.ArgumentParser(
        prog="renumber.py",
        description="レビュー後に一度だけ、通番 ID を機能ごとの連番へ振り直す")
    ap.add_argument("--dry-run", action="store_true",
                    help="変更を書かず、マップと検証結果だけ表示する")
    ap.add_argument("--out-map", metavar="PATH",
                    help=f"全単射マップの出力先（既定: <root>/{DEFAULT_MAP}）")
    ap.add_argument("--types", metavar="t1,t2,…",
                    help="対象種別を限定（既定は id_prefix を持つ全種別）")
    ap.add_argument("--force", action="store_true",
                    help="「一度だけ」マーカーを無視して再実行する")
    ap.add_argument("--allow-baseline-break", action="store_true",
                    help="baseline タグがあるとき必須（ID 振り直しは全 diff を壊す）")
    args = ap.parse_args(rest)

    types = ({t.strip() for t in args.types.split(",") if t.strip()}
             if args.types else None)
    out_map = Path(args.out_map) if args.out_map else None

    # 安全弁: 一度だけ
    if not args.force and not args.dry_run and read_state(data_root):
        print("既に振り直し済み（renumber.state.yaml が存在）。再実行は --force。",
              file=sys.stderr)
        return 1
    # 安全弁: baseline より前に一度だけ
    if not args.dry_run:
        tags = _baseline_tags(data_root)
        if tags and not args.allow_baseline_break:
            print("baseline タグが存在するため中止した（ID 振り直しは前後比較を全て壊す）:",
                  file=sys.stderr)
            for t in tags:
                print(f"  {t}", file=sys.stderr)
            print("実行するなら --allow-baseline-break を付け、実行後は baseline を"
                  "打ち直すこと。", file=sys.stderr)
            return 1

    try:
        res = run(data_root, types=types, dry_run=args.dry_run, out_map=out_map)
    except RenumberError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    _report(res)
    return 0


if __name__ == "__main__":
    sys.exit(main())
