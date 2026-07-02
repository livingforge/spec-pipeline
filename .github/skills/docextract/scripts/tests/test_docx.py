"""docx 抽出器の end-to-end テスト。"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docextract.extractors import extract_docx
from docextract.extractors.base import ImageSaver


def _extract(path, out_dir):
    return extract_docx(path, ImageSaver(out_dir)).to_dict()


def test_paragraphs_with_styles(tmp_path, make_docx):
    src = make_docx(paragraphs=[("Title", "Heading 1"), ("body text", None)])
    data = _extract(src, tmp_path / "out")
    texts = [e for e in data["elements"] if e["type"] == "text"]
    assert texts[0]["content"] == "Title"
    assert texts[0]["style"] == "Heading 1"
    assert any(e["content"] == "body text" for e in texts)


def test_empty_and_whitespace_paragraphs_skipped(tmp_path, make_docx):
    src = make_docx(paragraphs=[("", None), ("   \t  ", None), ("real", None)])
    data = _extract(src, tmp_path / "out")
    texts = [e for e in data["elements"] if e["type"] == "text"]
    assert len(texts) == 1
    assert texts[0]["content"] == "real"


def test_empty_document_has_no_elements(tmp_path, make_docx):
    src = make_docx()  # 段落・表・画像なし
    data = _extract(src, tmp_path / "out")
    # python-docx の空 Document は本文段落を持たないか空段落のみ -> text 要素なし
    assert data["summary"].get("text", 0) == 0
    assert "table" not in data["summary"]
    assert "image" not in data["summary"]


def test_table_extraction_and_cell_strip(tmp_path, make_docx):
    src = make_docx(
        paragraphs=[("intro", None)],
        table=[["  h1 ", "h2"], ["a", "  b  "]],
    )
    data = _extract(src, tmp_path / "out")
    tables = [e for e in data["elements"] if e["type"] == "table"]
    assert len(tables) == 1
    t = tables[0]
    assert t["n_rows"] == 2
    assert t["n_cols"] == 2
    assert t["rows"][0] == ["h1", "h2"]      # セルは strip される
    assert t["rows"][1] == ["a", "b"]


def test_inline_image_extracted_and_saved(tmp_path, make_docx, png_file):
    src = make_docx(paragraphs=[("caption", None)], image_path=png_file)
    out = tmp_path / "out"
    data = _extract(src, out)
    images = [e for e in data["elements"] if e["type"] == "image"]
    assert len(images) == 1
    img = images[0]
    assert img["format"] == "png"
    assert (out / img["file"]).exists()


def test_order_is_monotonic(tmp_path, make_docx):
    src = make_docx(
        paragraphs=[("p1", None), ("p2", None)],
        table=[["cell"]],
    )
    data = _extract(src, tmp_path / "out")
    orders = [e["location"]["order"] for e in data["elements"]]
    assert orders == sorted(orders)


def test_metadata_present(tmp_path, make_docx):
    src = make_docx(paragraphs=[("x", None)], title="My Title", author="Me")
    data = _extract(src, tmp_path / "out")
    assert data["metadata"]["title"] == "My Title"
    assert data["metadata"]["author"] == "Me"
    assert set(data["metadata"]) >= {"title", "author", "created", "modified"}
