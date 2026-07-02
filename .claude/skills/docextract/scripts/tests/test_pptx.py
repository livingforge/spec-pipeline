"""pptx 抽出器の end-to-end テスト。"""

from __future__ import annotations

from docextract.extractors import extract_pptx
from docextract.extractors.base import ImageSaver


def _extract(path, out_dir):
    return extract_pptx(path, ImageSaver(out_dir)).to_dict()


def test_textbox_extracted(tmp_path, make_pptx):
    src = make_pptx(slides=[{"texts": ["hello world"]}])
    data = _extract(src, tmp_path / "out")
    texts = [e for e in data["elements"] if e["type"] == "text"]
    assert any(e["content"] == "hello world" for e in texts)
    assert texts[0]["location"]["slide"] == 1


def test_empty_textbox_skipped(tmp_path, make_pptx):
    src = make_pptx(slides=[{"texts": ["", "   "]}])
    data = _extract(src, tmp_path / "out")
    assert [e for e in data["elements"] if e["type"] == "text"] == []


def test_table_extracted_with_strip(tmp_path, make_pptx):
    src = make_pptx(slides=[{"tables": [[[" a ", "b"], ["c", "d"]]]}])
    data = _extract(src, tmp_path / "out")
    tables = [e for e in data["elements"] if e["type"] == "table"]
    assert len(tables) == 1
    assert tables[0]["rows"][0] == ["a", "b"]


def test_picture_extracted_with_size(tmp_path, make_pptx, png_file):
    src = make_pptx(slides=[{"images": [png_file]}])
    out = tmp_path / "out"
    data = _extract(src, out)
    images = [e for e in data["elements"] if e["type"] == "image"]
    assert len(images) == 1
    img = images[0]
    assert img["width"] and img["height"]  # サイズが取れている
    assert (out / img["file"]).exists()


def test_notes_extracted_with_style(tmp_path, make_pptx):
    src = make_pptx(slides=[{"texts": ["body"], "notes": "speaker note"}])
    data = _extract(src, tmp_path / "out")
    notes = [e for e in data["elements"] if e.get("style") == "notes"]
    assert len(notes) == 1
    assert notes[0]["content"] == "speaker note"


def test_empty_notes_skipped(tmp_path, make_pptx):
    src = make_pptx(slides=[{"texts": ["body"], "notes": "   "}])
    data = _extract(src, tmp_path / "out")
    assert [e for e in data["elements"] if e.get("style") == "notes"] == []


def test_slide_numbers_across_multiple_slides(tmp_path, make_pptx):
    src = make_pptx(
        slides=[{"texts": ["s1"]}, {"texts": ["s2"]}, {"texts": ["s3"]}]
    )
    data = _extract(src, tmp_path / "out")
    by_content = {e["content"]: e["location"]["slide"] for e in data["elements"]}
    assert by_content == {"s1": 1, "s2": 2, "s3": 3}


def test_slide_count_metadata(tmp_path, make_pptx):
    src = make_pptx(slides=[{"texts": ["a"]}, {"texts": ["b"]}])
    data = _extract(src, tmp_path / "out")
    assert data["metadata"]["slide_count"] == 2


def test_mixed_content_single_slide(tmp_path, make_pptx, png_file):
    src = make_pptx(
        slides=[
            {
                "texts": ["heading"],
                "tables": [[["r1c1", "r1c2"]]],
                "images": [png_file],
                "notes": "n",
            }
        ]
    )
    data = _extract(src, tmp_path / "out")
    assert data["summary"]["text"] == 2  # 本文 + notes
    assert data["summary"]["table"] == 1
    assert data["summary"]["image"] == 1
