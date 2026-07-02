"""pdf 抽出器の end-to-end テスト。"""

from __future__ import annotations

from docextract.extractors import extract_pdf
from docextract.extractors.base import ImageSaver


def _extract(path, out_dir):
    return extract_pdf(path, ImageSaver(out_dir)).to_dict()


def test_text_block_with_bbox(tmp_path, make_pdf):
    src = make_pdf(pages=[{"texts": [("Hello PDF", (72, 72))]}])
    data = _extract(src, tmp_path / "out")
    texts = [e for e in data["elements"] if e["type"] == "text"]
    assert any("Hello PDF" in e["content"] for e in texts)
    t = texts[0]
    assert t["location"]["page"] == 1
    assert len(t["location"]["bbox"]) == 4


def test_bbox_values_rounded_to_one_decimal(tmp_path, make_pdf):
    src = make_pdf(pages=[{"texts": [("x", (72, 72))]}])
    data = _extract(src, tmp_path / "out")
    bbox = [e for e in data["elements"] if e["type"] == "text"][0]["location"]["bbox"]
    for v in bbox:
        # 小数第 1 位に丸められている
        assert round(v, 1) == v


def test_table_detected(tmp_path, make_pdf):
    src = make_pdf(
        pages=[
            {
                "grid": {
                    "rows": 2,
                    "cols": 2,
                    "cells": {(0, 0): "h1", (0, 1): "h2", (1, 0): "a", (1, 1): "b"},
                }
            }
        ]
    )
    data = _extract(src, tmp_path / "out")
    tables = [e for e in data["elements"] if e["type"] == "table"]
    assert len(tables) >= 1
    assert tables[0]["location"]["page"] == 1
    assert "bbox" in tables[0]["location"]


def test_table_text_not_duplicated_as_text_block(tmp_path, make_pdf):
    # 表領域と重なるテキストブロックはテキスト要素から除外される
    src = make_pdf(
        pages=[
            {
                "grid": {
                    "rows": 2,
                    "cols": 2,
                    "cells": {(0, 0): "UNIQUECELL", (1, 1): "b"},
                }
            }
        ]
    )
    data = _extract(src, tmp_path / "out")
    text_contents = " ".join(
        e["content"] for e in data["elements"] if e["type"] == "text"
    )
    # 表内の文字列は text 要素には現れない
    assert "UNIQUECELL" not in text_contents
    # 表としては拾えている
    tables = [e for e in data["elements"] if e["type"] == "table"]
    flat = " ".join(c for t in tables for row in t["rows"] for c in row)
    assert "UNIQUECELL" in flat


def test_image_extracted(tmp_path, make_pdf, png_bytes):
    src = make_pdf(pages=[{"images": [(png_bytes, (100, 100, 160, 160))]}])
    out = tmp_path / "out"
    data = _extract(src, out)
    images = [e for e in data["elements"] if e["type"] == "image"]
    assert len(images) == 1
    assert (out / images[0]["file"]).exists()
    assert images[0]["location"]["page"] == 1


def test_multi_page_numbering(tmp_path, make_pdf):
    src = make_pdf(
        pages=[
            {"texts": [("PAGEONE", (72, 72))]},
            {"texts": [("PAGETWO", (72, 72))]},
        ]
    )
    data = _extract(src, tmp_path / "out")
    page_of = {
        "PAGEONE": None,
        "PAGETWO": None,
    }
    for e in data["elements"]:
        if e["type"] != "text":
            continue
        for key in page_of:
            if key in e["content"]:
                page_of[key] = e["location"]["page"]
    assert page_of["PAGEONE"] == 1
    assert page_of["PAGETWO"] == 2


def test_page_count_metadata(tmp_path, make_pdf):
    src = make_pdf(pages=[{}, {}, {}])
    data = _extract(src, tmp_path / "out")
    assert data["metadata"]["page_count"] == 3


def test_empty_page_yields_no_elements(tmp_path, make_pdf):
    src = make_pdf(pages=[{}])
    data = _extract(src, tmp_path / "out")
    assert data["elements"] == []
    assert data["summary"] == {}
