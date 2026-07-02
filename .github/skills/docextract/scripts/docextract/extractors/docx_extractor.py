"""Word (.docx) の抽出器。

本文の段落と表を文書内の出現順に走査する。段落内のインライン画像は
リレーションシップ ID を解決して画像パートから取り出す。
"""

from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..models import ExtractionResult, ImageElement, TableElement, TextElement
from .base import ImageSaver


def extract_docx(path: Path, saver: ImageSaver) -> ExtractionResult:
    doc = Document(str(path))
    result = ExtractionResult(source=path.name, file_type="docx")

    props = doc.core_properties
    result.metadata = {
        "title": props.title or None,
        "author": props.author or None,
        "created": props.created.isoformat() if props.created else None,
        "modified": props.modified.isoformat() if props.modified else None,
    }

    order = 0
    for block in doc.iter_inner_content():
        order += 1
        if isinstance(block, Paragraph):
            _extract_paragraph(block, doc, saver, result, order)
        elif isinstance(block, Table):
            rows = [
                [cell.text.strip() for cell in row.cells]
                for row in block.rows
            ]
            result.elements.append(TableElement(rows=rows, location={"order": order}))
    return result


def _extract_paragraph(
    para: Paragraph, doc, saver: ImageSaver, result: ExtractionResult, order: int
) -> None:
    text = para.text.strip()
    if text:
        style = para.style.name if para.style else None
        result.elements.append(
            TextElement(content=text, style=style, location={"order": order})
        )
    # 段落内のインライン画像 (a:blip の r:embed でリレーションを参照)
    for rid in para._element.xpath(".//a:blip/@r:embed"):
        try:
            image_part = doc.part.rels[rid].target_part
        except KeyError:
            continue
        ext = image_part.partname.ext
        rel_path = saver.save(image_part.blob, ext)
        result.elements.append(
            ImageElement(file=rel_path, format=ext.lstrip("."), location={"order": order})
        )
    # テキストボックス内のテキスト (para.text には含まれない)。
    # mc:AlternateContent の Fallback に同じ内容が重複するため dedupe する
    seen: set[str] = set()
    for txbx in para._element.xpath(".//w:txbxContent"):
        text = "\n".join(
            "".join(t.text or "" for t in p.xpath(".//w:t"))
            for p in txbx.xpath(".//w:p")
        ).strip()
        if text and text not in seen:
            seen.add(text)
            result.elements.append(
                TextElement(content=text, style="textbox", location={"order": order})
            )
