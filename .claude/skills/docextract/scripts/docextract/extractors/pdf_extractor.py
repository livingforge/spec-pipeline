"""PDF の抽出器 (pdfplumber + pypdf、いずれも MIT/BSD ライセンス)。

ページごとに以下を抽出する:
- 表    : pdfplumber の find_tables() による表検出 (罫線ベース)
- テキスト : 行を垂直方向の間隔でまとめた段落ブロック単位。
          表領域内のテキストは重複を避けるため除外
- 画像   : pypdf による埋め込み画像のデコード (同一画像はハッシュで重複排除)
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pdfplumber
from pypdf import PdfReader

from ..models import ExtractionResult, ImageElement, TableElement, TextElement
from .base import ImageSaver


def extract_pdf(path: Path, saver: ImageSaver) -> ExtractionResult:
    result = ExtractionResult(source=path.name, file_type="pdf")

    with pdfplumber.open(str(path)) as pdf:
        meta = pdf.metadata or {}
        result.metadata = {
            "title": meta.get("Title") or None,
            "author": meta.get("Author") or None,
            "created": meta.get("CreationDate") or None,
            "modified": meta.get("ModDate") or None,
            "page_count": len(pdf.pages),
        }

        for page_no, page in enumerate(pdf.pages, start=1):
            _extract_tables_and_text(page, page_no, result)

    _extract_images(path, saver, result)
    return result


def _extract_tables_and_text(page, page_no: int, result: ExtractionResult) -> None:
    # --- 表 ---
    tables = page.find_tables()
    table_bboxes: list[tuple[float, float, float, float]] = []
    for tab in tables:
        rows = [
            ["" if c is None else str(c).strip() for c in row]
            for row in tab.extract()
        ]
        if not any(any(cell for cell in row) for row in rows):
            continue
        table_bboxes.append(tab.bbox)
        result.elements.append(
            TableElement(
                rows=rows,
                location={"page": page_no, "bbox": [round(v, 1) for v in tab.bbox]},
            )
        )

    # --- テキスト (表領域内の文字は除外) ---
    if table_bboxes:

        def outside_tables(obj) -> bool:
            cx = (obj["x0"] + obj["x1"]) / 2
            cy = (obj["top"] + obj["bottom"]) / 2
            return not any(
                bx0 <= cx <= bx1 and by0 <= cy <= by1
                for bx0, by0, bx1, by1 in table_bboxes
            )

        page = page.filter(outside_tables)

    lines = [ln for ln in page.extract_text_lines() if ln["text"].strip()]
    for block in _group_lines_into_blocks(lines):
        text = "\n".join(ln["text"].strip() for ln in block)
        x0 = min(ln["x0"] for ln in block)
        y0 = min(ln["top"] for ln in block)
        x1 = max(ln["x1"] for ln in block)
        y1 = max(ln["bottom"] for ln in block)
        result.elements.append(
            TextElement(
                content=text,
                location={
                    "page": page_no,
                    "bbox": [round(v, 1) for v in (x0, y0, x1, y1)],
                },
            )
        )


def _group_lines_into_blocks(lines: list[dict]) -> list[list[dict]]:
    """行を垂直方向の間隔で段落ブロックにまとめる。

    直前の行との間隔が行の高さより十分小さければ同じ段落とみなす。
    """
    blocks: list[list[dict]] = []
    current: list[dict] = []
    for ln in sorted(lines, key=lambda l: (l["top"], l["x0"])):
        if current:
            prev = current[-1]
            line_height = max(ln["bottom"] - ln["top"], 1.0)
            gap = ln["top"] - prev["bottom"]
            if gap > line_height * 0.9:
                blocks.append(current)
                current = []
        current.append(ln)
    if current:
        blocks.append(current)
    return blocks


def _extract_images(path: Path, saver: ImageSaver, result: ExtractionResult) -> None:
    try:
        reader = PdfReader(str(path))
    except Exception:
        return
    seen_hashes: set[str] = set()
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            images = page.images
        except Exception:
            continue
        for img in images:
            try:
                data = img.data
            except Exception:
                continue
            digest = hashlib.md5(data).hexdigest()
            if digest in seen_hashes:
                continue
            seen_hashes.add(digest)
            ext = Path(img.name).suffix.lstrip(".").lower() or "png"
            rel_path = saver.save(data, ext)
            width = height = None
            try:
                width, height = img.image.size
            except Exception:
                pass
            result.elements.append(
                ImageElement(
                    file=rel_path,
                    format=ext,
                    width=width,
                    height=height,
                    location={"page": page_no},
                )
            )
