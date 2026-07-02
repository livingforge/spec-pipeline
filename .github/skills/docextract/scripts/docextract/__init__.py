"""docextract — Office 文書 (docx/xlsx/pptx) と PDF から
テキスト・表・画像を抽出して JSON 形式で出力するライブラリ。

使い方 (Python API):
    from docextract import extract
    result = extract("report.docx", output_dir="out")

使い方 (CLI):
    python -m docextract report.docx slides.pptx -o out
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .extractors import extract_docx, extract_pdf, extract_pptx, extract_xlsx
from .extractors.base import ImageSaver
from .image_tables import detect_tables
from .models import ExtractionResult, ImageElement, TableElement
from .ocr import ocr_image

__version__ = "0.1.0"

_EXTRACTORS = {
    ".docx": extract_docx,
    ".xlsx": extract_xlsx,
    ".xlsm": extract_xlsx,
    ".pptx": extract_pptx,
    ".pdf": extract_pdf,
}

SUPPORTED_EXTENSIONS = tuple(_EXTRACTORS)


def extract(
    input_path: str | Path,
    output_dir: str | Path = "output",
    save_json: bool = True,
    ocr: bool = True,
    ocr_lang: str = "ja",
    ocr_backend: str = "auto",
    image_tables: bool = True,
) -> dict[str, Any]:
    """1 つの文書を解析し、抽出結果を dict で返す。

    画像は ``<output_dir>/<入力ファイル名>/images/`` に保存され、
    ``save_json=True`` なら ``<output_dir>/<入力ファイル名>/result.json``
    も書き出す。

    ``ocr=True`` の場合、抽出した各画像に対して OCR を実行し、
    画像内のテキストを ``ocr_text`` として付加する
    (スクリーンショットや図として貼られたテキスト・表への対応)。

    ``image_tables=True`` の場合、各画像に対して表検出
    (rapid_layout + rapid_table) を実行し、見つかった表を
    通常の ``table`` 要素として追加する。location には
    ``from_image`` (元画像) と ``bbox_in_image`` が入る。
    """
    input_path = Path(input_path)
    if not input_path.is_file():
        raise FileNotFoundError(f"ファイルが見つかりません: {input_path}")

    ext = input_path.suffix.lower()
    extractor = _EXTRACTORS.get(ext)
    if extractor is None:
        supported = ", ".join(SUPPORTED_EXTENSIONS)
        raise ValueError(f"未対応の形式です: {ext} (対応形式: {supported})")

    # 拡張子違いの同名ファイルが衝突しないよう、拡張子込みの名前で分ける
    doc_out_dir = Path(output_dir) / f"{input_path.stem}_{ext.lstrip('.')}"
    doc_out_dir.mkdir(parents=True, exist_ok=True)

    saver = ImageSaver(doc_out_dir)
    result: ExtractionResult = extractor(input_path, saver)

    images = [el for el in result.elements if isinstance(el, ImageElement)]
    for el in images:
        image_path = doc_out_dir / el.file
        if ocr:
            el.ocr_text = ocr_image(image_path, lang=ocr_lang, backend=ocr_backend)
        if image_tables:
            for rows, bbox in detect_tables(image_path, lang=ocr_lang):
                location = dict(el.location)
                location["from_image"] = el.file
                if bbox:
                    location["bbox_in_image"] = bbox
                result.elements.append(TableElement(rows=rows, location=location))

    data = result.to_dict()

    if save_json:
        json_path = doc_out_dir / "result.json"
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return data
