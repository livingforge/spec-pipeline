"""codescan — ソースコードから骨格ファクトを決定論で洗い出す (Phase 1 / L0)。

設計書が無いリポジトリの「コード→仕様」逆方向パイプラインの入口。Python
ソースを ast で走査し、docstring・シグネチャ・型注釈**だけ**を根拠に、
docagent の facts シャード（``facts-merge`` で主ストアへ統合できる形式）を
生成する。LLM を使わず再実行しても同じ内容になる（決定論）。

生成するファクト種別と工程間トレース（refs）:
- ``エンティティ``       業務データを表す dataclass。``has-column`` で各項目を参照。
                        ストア/設定/エラー/CLI などインフラ内部クラスは名前接尾辞・
                        継承から判定して**エンティティにしない**（モジュール・クラス扱い）
- ``データ項目``         dataclass の型注釈付きフィールド（型は 数値/文字列/日付/真偽 へ写像。
                        ``Literal``/Enum は値域を ``(domain: …)``、既定値は ``(既定: …)``、
                        dict/list は ``(構造: …; 参照エンティティ/値オブジェクト候補)`` として保全）
- ``モジュール・クラス`` 通常クラス。``has-method`` で公開メソッドを、
                        ``refines`` で本体が参照するエンティティを参照
- ``メソッド``           クラスの公開メソッドとトップレベル関数

テスト（``test_*.py`` / ``conftest.py`` / ``tests/``）とエントリポイント
（``__main__.py`` / ``_bootstrap.py`` / ``setup_env*``）は仕様の源泉ではないため
既定で走査から除外し、除外一覧を必ず報告する（``--include-tests`` /
``--include-entrypoints`` で個別に含められる）。

意図の層（機能要件・業務ルール等）はここでは**作らない** — コードに書かれて
いない意図を決定論では復元できないため、後工程の LLM エージェント
（code-fact-extractor）が Phase 2 として担い、必ず人間レビューを通す。

doc_id は ``identity.doc_id``（正規化済み絶対パスのハッシュ入り）で、同じ
ファイルを ``docextract extract`` で抽出・登録した場合と**同一 ID** になる。
出典 location は ``{"line": 開始行}``、evidence はその行のソース。

使い方 (CLI):
    python -m docextract.codescan --dir src/ [-o シャード.json]
    docextract codescan --dir src/            # venv コマンド経由
統合:
    docextract docagent facts-merge <シャード.json>
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import identity, paths

# 型注釈 → jp-sier-std の data-item 型列挙（未知の型は 文字列 に丸める）
TYPE_MAP = {
    "int": "数値",
    "float": "数値",
    "Decimal": "数値",
    "str": "文字列",
    "date": "日付",
    "datetime": "日付",
    "bool": "真偽",
}

# コンテナ型。データ項目としては平坦化せず「参照エンティティ/値オブジェクト候補」
# のフラグを立てる（埋もれた業務実体の発見材料。B-2 のエンティティ未立項と連動）。
STRUCT_TYPE_NAMES = {"dict", "Dict", "list", "List", "set", "Set",
                     "tuple", "Tuple", "Mapping", "Sequence", "Iterable"}

# インフラ内部クラスの判定材料。ストア/設定/エラー/CLI はデータの業務実体では
# ないため「エンティティ」にしない（モジュール・クラスとして扱う）。
INFRA_CLASS_SUFFIXES = ("Store", "Config", "Settings", "Error", "Result",
                        "Cli", "CLI", "Command", "Client", "Queue")
INFRA_BASE_NAMES = {"Exception", "BaseException"}

# ファイルの役割分類。テストは検証手段であって仕様ではなく、エントリポイント/
# bootstrap は環境配線のため、どちらも骨格ファクトの対象にしない（既定）。
# 除外したファイルは必ず件数・内訳つきで報告する（silent cap 禁止）。
TEST_DIR_NAMES = {"tests", "test"}
ENTRYPOINT_FILE_NAMES = {"__main__.py", "_bootstrap.py"}
ENTRYPOINT_FILE_PREFIXES = ("setup_env",)
ROLE_LABELS = {"test": "テスト", "entrypoint": "エントリポイント"}

# 生成する種別・関係種別。docagent の既定語彙（item_types.json / rel_types.json）
# に「エンティティ」を追加する（facts-merge が和集合で主ストアへ取り込む）。
ITEM_TYPES = [
    "機能要件", "非機能要件", "業務ルール", "データ項目", "画面・帳票",
    "外部インターフェース", "モジュール・クラス", "メソッド", "制約・前提", "用語",
    "エンティティ",
]
REL_TYPES = ["realizes", "refines", "constrains", "interfaces",
             "has-method", "has-column", "displays"]

# 走査から除外するディレクトリ（生成物・依存・キャッシュ）
EXCLUDE_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__",
                ".docextract", ".contextdb", "dist", "build", ".mypy_cache",
                ".pytest_cache", ".ruff_cache"}


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _is_dataclass(node: ast.ClassDef) -> bool:
    for d in node.decorator_list:
        name = d.id if isinstance(d, ast.Name) else getattr(d, "attr", "")
        if isinstance(d, ast.Call):
            f = d.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
        if name == "dataclass":
            return True
    return False


def _docline(node: ast.AST) -> str:
    """docstring の先頭行（責務の 1 文）。無ければ空文字。"""
    doc = ast.get_docstring(node)
    return doc.strip().splitlines()[0] if doc else ""


def _ann_name(ann: ast.expr | None) -> str:
    if isinstance(ann, ast.Name):
        return ann.id
    if isinstance(ann, ast.Attribute):
        return ann.attr
    return ""


def _base_names(node: ast.ClassDef) -> list[str]:
    return [_ann_name(b) for b in node.bases]


def _is_enum(node: ast.ClassDef) -> bool:
    return any(b in {"Enum", "IntEnum", "StrEnum", "Flag", "IntFlag"}
               for b in _base_names(node))


def _is_infra_class(node: ast.ClassDef) -> bool:
    """ストア/設定/エラー/CLI などインフラ内部クラスか（業務エンティティにしない）。"""
    if node.name.endswith(INFRA_CLASS_SUFFIXES):
        return True
    return any(
        b in INFRA_BASE_NAMES or b.endswith(("Error", "Exception"))
        for b in _base_names(node)
    )


def _enum_domain(node: ast.ClassDef) -> str:
    """Enum クラスのメンバー値から値域（domain）文字列を作る。"""
    values: list[str] = []
    for stmt in node.body:
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 \
                and isinstance(stmt.targets[0], ast.Name):
            name = stmt.targets[0].id
            if isinstance(stmt.value, ast.Constant) \
                    and isinstance(stmt.value.value, (str, int)):
                values.append(str(stmt.value.value))
            else:
                values.append(name)
    return "|".join(values)


def _field_spec(
    ann: ast.expr | None, enum_domains: dict[str, str]
) -> tuple[str, str | None, str | None]:
    """型注釈から (日本語型, domain, 構造メモ) を導出する。

    ``Literal[...]`` / Enum 参照は値域（domain）へ転記し、``Optional`` / ``X | None``
    は中身の型で判定する。dict/list 等のコンテナは平坦化せず構造メモ
    （参照エンティティ/値オブジェクト候補のフラグ）を返す。区分値・構造の
    情報を「文字列」への丸めで失わないための保全（B-3）。
    """
    if ann is None:
        return "文字列", None, None
    # X | None (Optional 相当) は X で判定する
    if isinstance(ann, ast.BinOp) and isinstance(ann.op, ast.BitOr):
        for side in (ann.left, ann.right):
            if not (isinstance(side, ast.Constant) and side.value is None):
                return _field_spec(side, enum_domains)
    if isinstance(ann, ast.Subscript):
        base = _ann_name(ann.value)
        if base == "Optional":
            return _field_spec(ann.slice, enum_domains)
        if base == "Literal":
            elts = ann.slice.elts if isinstance(ann.slice, ast.Tuple) else [ann.slice]
            values = [e.value for e in elts if isinstance(e, ast.Constant)]
            jp = "数値" if values and all(isinstance(v, int) for v in values) else "文字列"
            return jp, "|".join(str(v) for v in values), None
        if base in STRUCT_TYPE_NAMES:
            return "文字列", None, ast.unparse(ann)
    name = _ann_name(ann)
    if name in STRUCT_TYPE_NAMES:
        return "文字列", None, ast.unparse(ann)
    if name in enum_domains:
        return "文字列", enum_domains[name], None
    return TYPE_MAP.get(name, "文字列"), None, None


def classify_role(rel: str) -> str:
    """相対パス（posix）からファイルの役割を決める: source / test / entrypoint。"""
    parts = rel.split("/")
    name = parts[-1]
    if name.startswith("test_") or name == "conftest.py" \
            or any(p in TEST_DIR_NAMES for p in parts[:-1]):
        return "test"
    if name in ENTRYPOINT_FILE_NAMES or name.startswith(ENTRYPOINT_FILE_PREFIXES):
        return "entrypoint"
    return "source"


def iter_py_files(root: Path) -> list[Path]:
    """走査対象の .py を列挙する（生成物・依存ディレクトリは除外）。"""
    out = []
    for f in sorted(root.rglob("*.py")):
        if not any(part in EXCLUDE_DIRS for part in f.relative_to(root).parts):
            out.append(f)
    return out


def scan(
    root: Path,
    include_tests: bool = False,
    include_entrypoints: bool = False,
) -> tuple[list[dict[str, Any]], list[tuple[str, str]], list[tuple[str, str]]]:
    """root 配下の Python ソースを走査し (骨格ファクト, スキップ, 除外) を返す。

    構文エラーのファイルは黙って握り潰さず、(相対パス, 理由) の
    スキップ一覧として返しファイル単位で続行する（fail-soft + observable）。
    テスト（``test_*.py`` / ``conftest.py`` / ``tests/`` 配下）と
    エントリポイント（``__main__.py`` / ``_bootstrap.py`` / ``setup_env*``）は
    既定で骨格の対象外とし、(相対パス, 役割ラベル) の除外一覧として返す
    （silent cap 禁止 — 呼び出し元は件数・内訳を必ず報告する）。
    """
    root = root.resolve()
    files = iter_py_files(root)
    trees: list[tuple[str, str, ast.Module, list[str]]] = []  # (doc_id, rel, tree, lines)
    dataclass_names: set[str] = set()
    enum_domains: dict[str, str] = {}
    skipped: list[tuple[str, str]] = []
    excluded: list[tuple[str, str]] = []

    for f in files:
        rel = f.relative_to(root).as_posix()
        role = classify_role(rel)
        if role == "test" and not include_tests:
            excluded.append((rel, ROLE_LABELS[role]))
            continue
        if role == "entrypoint" and not include_entrypoints:
            excluded.append((rel, ROLE_LABELS[role]))
            continue
        src = f.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            skipped.append((rel, f"構文エラー: {e.msg}"))
            continue
        trees.append((identity.doc_id(f), rel, tree, src.splitlines()))
        for n in ast.walk(tree):
            if not isinstance(n, ast.ClassDef):
                continue
            # インフラ内部の dataclass は業務エンティティにしない（B-2）
            if _is_dataclass(n) and not _is_infra_class(n):
                dataclass_names.add(n.name)
            elif _is_enum(n):
                enum_domains[n.name] = _enum_domain(n)

    facts: list[dict[str, Any]] = []
    fid = 0
    now = _now()

    def add(doc_id: str, type_: str, statement: str, line: int,
            lines: list[str], refs: list[dict] | None = None) -> None:
        nonlocal fid
        fid += 1
        evidence = lines[line - 1].strip() if 0 < line <= len(lines) else ""
        facts.append({
            "id": f"f{fid:04d}",
            "doc_id": doc_id,
            "type": type_,
            "statement": statement,
            "evidence": evidence,
            "location": {"line": line},
            "refs": refs or [],
            "added_at": now,
        })

    def add_method(doc_id: str, owner: str | None, fn: ast.FunctionDef | ast.AsyncFunctionDef,
                   lines: list[str]) -> str:
        args = [a.arg for a in fn.args.args if a.arg != "self"]
        sig = (f"{owner}." if owner else "") + f"{fn.name}({', '.join(args)})"
        add(doc_id, "メソッド", f"{sig}: {_docline(fn)}", fn.lineno, lines)
        return sig

    for doc_id, _rel, tree, lines in trees:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    add_method(doc_id, None, node, lines)
                continue
            if not isinstance(node, ast.ClassDef):
                continue
            cls = node.name
            if _is_dataclass(node) and not _is_infra_class(node):
                col_refs = []
                for stmt in node.body:
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                        field = stmt.target.id
                        jp, domain, struct = _field_spec(stmt.annotation, enum_domains)
                        text = f"{cls}.{field}: {jp}"
                        if domain:
                            text += f" (domain: {domain})"
                        if struct:
                            text += f" (構造: {struct}; 参照エンティティ/値オブジェクト候補)"
                        if isinstance(stmt.value, ast.Constant) \
                                and stmt.value.value is not None:
                            text += f" (既定: {stmt.value.value!r})"
                        add(doc_id, "データ項目", text, stmt.lineno, lines)
                        col_refs.append({"rel": "has-column", "to_ref": f"{cls}.{field}"})
                add(doc_id, "エンティティ", f"{cls}: {_docline(node)}",
                    node.lineno, lines, col_refs)
            else:
                refs = []
                for stmt in node.body:
                    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and not stmt.name.startswith("_"):
                        sig = add_method(doc_id, cls, stmt, lines)
                        refs.append({"rel": "has-method", "to_ref": sig})
                used = {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}
                for ent in sorted(used & dataclass_names):
                    refs.append({"rel": "refines", "to_ref": ent})
                add(doc_id, "モジュール・クラス", f"{cls}: {_docline(node)}",
                    node.lineno, lines, refs)

    return facts, skipped, excluded


def make_shard(facts: list[dict[str, Any]]) -> dict[str, Any]:
    """facts-merge が読める facts シャード（FactStore スキーマ）を組み立てる。"""
    return {"version": 1, "item_types": ITEM_TYPES, "rel_types": REL_TYPES,
            "items": facts}


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(
        prog="docextract codescan",
        description="ソースコードから骨格ファクトを決定論で洗い出す (L0)",
    )
    ap.add_argument("--dir", type=Path, required=True, help="走査するソースルート")
    ap.add_argument("-o", "--out", type=Path, default=None,
                    help="出力シャード JSON (既定: <store>/shards/facts.codescan.json)")
    ap.add_argument("--include-tests", action="store_true",
                    help="テスト (test_*.py / conftest.py / tests/) も骨格の対象に含める")
    ap.add_argument("--include-entrypoints", action="store_true",
                    help="エントリポイント (__main__.py / _bootstrap.py / setup_env*) も対象に含める")
    args = ap.parse_args(argv)

    if not args.dir.is_dir():
        print(f"ディレクトリが見つからない: {args.dir}", file=sys.stderr)
        return 2

    facts, skipped, excluded = scan(
        args.dir,
        include_tests=args.include_tests,
        include_entrypoints=args.include_entrypoints,
    )
    out = args.out or (paths.facts_path().parent / "shards" / "facts.codescan.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(make_shard(facts), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")

    by_type: dict[str, int] = {}
    for f in facts:
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
    excluded_by_role: dict[str, int] = {}
    for _, role in excluded:
        excluded_by_role[role] = excluded_by_role.get(role, 0) + 1
    print(json.dumps({
        "total": len(facts), "by_type": by_type,
        "skipped": [{"file": s, "reason": r} for s, r in skipped],
        "excluded": {
            "total": len(excluded),
            "by_role": excluded_by_role,
            "files": [{"file": s, "role": r} for s, r in excluded],
        },
        "out": str(out),
        "next": "docextract docagent facts-merge " + str(out),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
