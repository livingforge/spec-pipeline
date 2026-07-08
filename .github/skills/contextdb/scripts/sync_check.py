# -*- coding: utf-8 -*-
"""同期チェック — 実装と仕様データ（正本）の乖離を機械的に検出する

    python contextdb/sync_check.py                  # 作業ツリーの変更 (git) を照合
    python sync_check.py --rev HEAD~3            # 指定リビジョン以降の変更も対象
    python sync_check.py --files a.py b.md       # git を使わず変更ファイルを明示
    python sync_check.py --json                  # 機械可読 (JSON)
    python sync_check.py --strict                # error 級の検出があれば exit 1
    python sync_check.py --root <データディレクトリ> …

context-sync（実装差分の正本への同期）の入力を作る。LLM や人の判断が要るのは
「この変更は仕様の変化か」だけで、どこを見るべきかは本ツールが列挙する:

  stale          変更されたファイルを参照しているアイテム/関係（要確認・情報）
  unregistered   リポジトリに実体があるのに対応アイテムが無い（error）
  vanished       アイテムが指す実体が観測されない（error。deprecated は対象外）
  dead-path      path 属性のパスが存在しない（error）
  dead-doc       source.doc の文書が存在しない（error）
  stale-evidence source.evidence の原文が doc 内に見つからない（warn）

棚卸し（unregistered / vanished）の規則はデータルートの sync.yaml に書く:

    path_attributes: [path]          # リポジトリパスを持つ属性名（既定: path）
    check_exists: ["module.path"]    # 実在検査する 種別.属性（実行時成果物は書かない）
    inventory:
      - type: skill                              # 突合するアイテム種別
        glob: "src/skills/*/frontmatter.common.yaml"
        key: name                                # 突合に使うアイテム属性
        value: "yaml:name"   # 実体名の取り方: parent | stem | yaml:<キー> |
                             #   frontmatter:<キー> | lines
        match: exact         # exact | contains（アイテム側の値が実体名を含めば一致）

sync.yaml が無ければ棚卸しは行わず、ドリフト検出と出典鮮度だけ検査する。
エンジン・diff 同様、特定のアイテム種別の知識は持たない。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from engine import Store, parse_root
from diff import _git

CONFIG_NAME = "sync.yaml"

# evidence の照合をあきらめるバイナリ拡張子
_BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".zip", ".xlsx", ".xls",
                ".docx", ".doc", ".pptx", ".ppt", ".pdf", ".ico", ".exe"}


# ---------- 設定 ----------

def load_config(data_root: Path) -> dict:
    path = data_root / CONFIG_NAME
    cfg = {}
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    cfg.setdefault("path_attributes", ["path"])
    cfg.setdefault("check_exists", [])
    cfg.setdefault("inventory", [])
    return cfg


# ---------- 変更ファイルの列挙 (git) ----------

def repo_base(data_root: Path) -> Path:
    """パス参照の基準ディレクトリ。Git のトップレベル、無ければ親。"""
    top = _git("rev-parse", "--show-toplevel", cwd=data_root).stdout.strip()
    return Path(top) if top else data_root.resolve().parent


def changed_files(base: Path, rev: str | None) -> list[str]:
    """未コミットの変更（+ rev 以降のコミット済み変更）。Git 不使用なら空。"""
    if not _git("rev-parse", "--show-toplevel", cwd=base).stdout.strip():
        return []
    files: set[str] = set()
    for line in _git("status", "--porcelain", cwd=base).stdout.splitlines():
        p = line[3:].strip().strip('"')
        if " -> " in p:                      # リネームは新パス側を採る
            p = p.split(" -> ", 1)[1].strip('"')
        if p:
            files.add(p)
    if rev:
        out = _git("diff", "--name-only", rev, "HEAD", cwd=base).stdout
        files.update(p for p in out.splitlines() if p)
    return sorted(files)


# ---------- 検出結果 ----------

def _finding(level: str, kind: str, where: str, message: str) -> dict:
    return {"level": level, "kind": kind, "where": where, "message": message}


def _norm(p: str) -> str:
    p = str(p).replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.rstrip("/")


def _rel_where(r) -> str:
    return f"{r.type}:{r.src}->{r.dst}"


def _iter_sources(store: Store):
    """(参照元の表示名, 出典エントリ) を全アイテム・全関係にわたって列挙する。"""
    for item in store.items.values():
        for src in item.source or []:
            yield item.id, src
    for r in store.relations:
        for src in r.source or []:
            yield _rel_where(r), src


# ---------- 1. ドリフト検出 — 変更ファイル → 参照しているアイテム/関係 ----------

def check_drift(store: Store, cfg: dict, changed: list[str],
                data_root: Path, base: Path) -> list[dict]:
    # 逆引き索引: 正規化パス → [(参照元, 参照の種類)]
    index: list[tuple[str, str, str]] = []   # (path, where, why)
    for item in store.items.values():
        for attr in cfg["path_attributes"]:
            v = item.attrs.get(attr)
            if isinstance(v, str) and v:
                index.append((_norm(v), item.id, f"{attr} 属性"))
    for where, src in _iter_sources(store):
        index.append((_norm(src["doc"]), where, "source.doc"))

    # データルート配下の変更は仕様データそのものなので照合対象外
    try:
        root_prefix = _norm(str(data_root.resolve().relative_to(base.resolve())))
    except ValueError:
        root_prefix = None

    findings, seen = [], set()
    for f in changed:
        fn = _norm(f)
        if root_prefix and (fn == root_prefix or fn.startswith(root_prefix + "/")):
            continue
        for p, where, why in index:
            if fn == p or fn.startswith(p + "/"):
                key = (where, fn)
                if key in seen:
                    continue
                seen.add(key)
                findings.append(_finding(
                    "check", "stale", where,
                    f"変更ファイル '{f}' を {why} として参照している — 仕様への影響を確認する"))
    return findings


# ---------- 2. 棚卸し — リポジトリの実体 ↔ 登録済みアイテム ----------

def _extract_names(path: Path, mode: str) -> list[str]:
    """ファイル 1 つから実体名を取り出す。"""
    if mode == "parent":
        return [path.parent.name]
    if mode == "stem":
        return [path.stem]
    if mode.startswith("yaml:"):
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        v = data.get(mode.split(":", 1)[1]) if isinstance(data, dict) else None
        return [str(v)] if v else []
    if mode.startswith("frontmatter:"):
        key = mode.split(":", 1)[1]
        text = path.read_text(encoding="utf-8", errors="ignore")
        if text.startswith("---"):
            block = text[3:].split("\n---", 1)[0]
            data = yaml.safe_load(block) or {}
            v = data.get(key) if isinstance(data, dict) else None
            return [str(v)] if v else []
        return []
    if mode == "lines":
        names = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            for sep in ("==", ">=", "<=", "~=", ">", "<", "[", ";", " "):
                line = line.split(sep, 1)[0]
            if line:
                names.append(line)
        return names
    raise ValueError(f"未知の value モード '{mode}'（parent/stem/yaml:/frontmatter:/lines）")


def _matches(observed: str, registered: str, match: str) -> bool:
    o, r = observed.casefold(), registered.casefold()
    return o in r if match == "contains" else o == r


def check_inventory(store: Store, cfg: dict, base: Path) -> list[dict]:
    findings = []
    # 同じ種別に複数の規則があるとき（例: requirements が 2 ファイル）、
    # vanished 判定は全規則の観測結果を合算してから行う
    by_type: dict[str, list[tuple[str, str, str]]] = {}   # 種別 → (名前, 由来, match)
    for rule in cfg["inventory"]:
        t, key = rule["type"], rule["key"]
        mode = rule.get("value", "parent")
        match = rule.get("match", "exact")
        observed: list[tuple[str, str, str]] = []
        for p in sorted(base.glob(rule["glob"])):
            if p.is_file():
                for n in _extract_names(p, mode):
                    observed.append((n, p.relative_to(base).as_posix(), match))
        by_type.setdefault(t, []).extend(observed)

        values = [(str(i.attrs.get(key, "")), i.id)
                  for i in store.items_of(t) if i.status != "deprecated"]
        for name, origin, m in observed:
            if not any(_matches(name, v, m) for v, _ in values):
                findings.append(_finding(
                    "error", "unregistered", origin,
                    f"実体 '{name}' に対応する {t} アイテムが無い（{key} で突合）"))

    for rule in cfg["inventory"]:
        t, key = rule["type"], rule["key"]
        observed = by_type[t]
        if not observed:
            continue
        for i in store.items_of(t):
            if i.status == "deprecated":
                continue
            v = str(i.attrs.get(key, ""))
            if not any(_matches(name, v, m) for name, _, m in observed):
                findings.append(_finding(
                    "error", "vanished", i.id,
                    f"{t} '{v}' に対応する実体が見つからない"
                    "— 廃止なら status: deprecated にする"))
        by_type[t] = []   # 種別ごとに 1 回だけ判定する
    return findings


# ---------- 3. 出典・パスの鮮度 ----------

_NOISE = str.maketrans("", "", "|`*_#>")   # Markdown の表・強調・引用の記号


def _squash(s: str) -> str:
    """表記の揺れ（空白・Markdown 記号・大文字小文字）を除いて照合用に正規化する。"""
    return "".join(str(s).split()).translate(_NOISE).casefold()


def _evidence_found(evidence: str, doc_text: str) -> bool:
    """evidence が文書に残っているか。省略記号（… / ...）で区切られた
    断片ごとに照合する（原文の一部を省略して引用した evidence を許す）。"""
    fragments = [f for f in evidence.replace("...", "…").split("…") if f.strip()]
    return all(_squash(f) in doc_text for f in fragments)


def check_sources(store: Store, cfg: dict, base: Path) -> list[dict]:
    findings = []
    checks = [tuple(spec.split(".", 1)) for spec in cfg["check_exists"]]
    for item in store.items.values():
        if item.status == "deprecated":
            continue
        for t, attr in checks:
            if item.type != t:
                continue
            v = item.attrs.get(attr)
            if isinstance(v, str) and v and not (base / _norm(v)).exists():
                findings.append(_finding(
                    "error", "dead-path", item.id,
                    f"{attr} 属性のパス '{v}' が存在しない"))
    checked: dict[str, str] = {}   # doc パス → 正規化済み内容
    for where, src in _iter_sources(store):
        doc = _norm(src["doc"])
        path = base / doc
        if not path.is_file():
            findings.append(_finding(
                "error", "dead-doc", where, f"source.doc '{src['doc']}' が存在しない"))
            continue
        evidence = src.get("evidence")
        if not evidence or path.suffix.lower() in _BINARY_EXTS:
            continue
        if doc not in checked:
            checked[doc] = _squash(path.read_text(encoding="utf-8", errors="ignore"))
        if not _evidence_found(str(evidence), checked[doc]):
            findings.append(_finding(
                "warn", "stale-evidence", where,
                f"evidence の原文が '{src['doc']}' に見つからない — 出典が古い可能性"))
    return findings


# ---------- レポート ----------

def run_checks(data_root: Path, rev: str | None = None,
               files: list[str] | None = None) -> dict:
    store = Store.load(data_root)
    cfg = load_config(data_root)
    base = repo_base(data_root)
    changed = files if files is not None else changed_files(base, rev)
    findings = (check_drift(store, cfg, changed, data_root, base)
                + check_inventory(store, cfg, base)
                + check_sources(store, cfg, base))
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["kind"]] = counts.get(f["kind"], 0) + 1
    return {"root": str(data_root), "base": str(base),
            "changed_files": changed, "findings": findings, "counts": counts}


def render_text(report: dict) -> str:
    lines = [f"# 同期チェック — {report['root']}", ""]
    lines.append(f"変更ファイル: {len(report['changed_files'])} 件")
    for f in report["changed_files"]:
        lines.append(f"  {f}")
    lines.append("")
    if not report["findings"]:
        lines.append("検出なし — 正本と実装の乖離は観測されていない。")
    for f in report["findings"]:
        lines.append(f"{f['level']}: {f['kind']} [{f['where']}] {f['message']}")
    if report["counts"]:
        summary = " / ".join(f"{k} {v}" for k, v in sorted(report["counts"].items()))
        lines += ["", f"検出: {summary}"]
    return "\n".join(lines) + "\n"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    data_root, rest = parse_root(sys.argv[1:])
    ap = argparse.ArgumentParser(
        prog="sync_check.py", description="実装と仕様データの乖離を機械的に検出する")
    ap.add_argument("--rev", help="このリビジョン以降のコミット済み変更も対象にする")
    ap.add_argument("--files", nargs="*", default=None,
                    help="git を使わず変更ファイルを明示する")
    ap.add_argument("--json", action="store_true", help="JSON で出力する")
    ap.add_argument("--strict", action="store_true",
                    help="error 級の検出があれば exit 1（CI ゲート用）")
    args = ap.parse_args(rest)

    report = run_checks(data_root, rev=args.rev, files=args.files)
    if args.json:
        sys.stdout.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    else:
        sys.stdout.write(render_text(report))
    if args.strict and any(f["level"] == "error" for f in report["findings"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
