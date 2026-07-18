"""Python ソースコード (.py) の抽出器。

設計書が無いリポジトリでは**コードが唯一の一次資料**になる。この抽出器は
ソースファイルを「文書」として扱い、後工程（docagent の索引化・ブロック
キュー・仕様の洗い出し）が資料と同じ流儀で扱える ``ExtractionResult`` に
変換する。要約・解釈はしない（意図の抽出は後工程の LLM エージェント、
骨格の決定論抽出は ``codescan`` モジュールの役割）。

要素の切り方（文書内の出現順）:
- モジュール docstring       → ``style="module_doc"``
- トップレベルの import/定数等 → 1 要素にまとめて ``style="module_body"``
  （送料閾値のような定数は業務ルール推論の重要な根拠になるため落とさない）
- トップレベルの class        → ``style="class"``（クラス全文）
- トップレベルの def          → ``style="function"``（関数全文）

location は ``{"line": 開始行}``（1 始まり）。シート/ページに相当する
ユニットキーを持たないため、ブロック分割は body（ファイル単位 + 文字数
上限）として扱われる。
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..models import ExtractionResult, TextElement
from .base import ImageSaver


def extract_python(path: Path, saver: ImageSaver) -> ExtractionResult:
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        raise ValueError(f"Python 構文エラー: {path.name} 行 {e.lineno}: {e.msg}")

    result = ExtractionResult(source=path.name, file_type="py")
    result.metadata = {
        "title": path.stem,
        "author": None,
        "created": None,
        "modified": None,
        "loc": src.count("\n") + 1 if src else 0,
    }

    doc = ast.get_docstring(tree)
    if doc:
        result.elements.append(
            TextElement(content=doc.strip(), style="module_doc",
                        location={"line": 1})
        )

    # トップレベルの import・定数・式などを 1 要素へまとめる（出現順を保つため
    # 最初の該当ノードの行を location にする）。docstring の Expr は除外する。
    body_parts: list[str] = []
    body_line: int | None = None
    for i, node in enumerate(tree.body):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if i == 0 and doc:
            continue  # モジュール docstring は抽出済み
        seg = ast.get_source_segment(src, node)
        if seg:
            body_parts.append(seg)
            if body_line is None:
                body_line = node.lineno
    if body_parts:
        result.elements.append(
            TextElement(content="\n".join(body_parts), style="module_body",
                        location={"line": body_line or 1})
        )

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            style = "class"
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            style = "function"
        else:
            continue
        seg = ast.get_source_segment(src, node)
        if not seg:
            result.note_degraded("code", "source segment 取得失敗",
                                 name=node.name, line=node.lineno)
            continue
        result.elements.append(
            TextElement(content=seg, style=style,
                        location={"line": node.lineno, "name": node.name})
        )

    return result
