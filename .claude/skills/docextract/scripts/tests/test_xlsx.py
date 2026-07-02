"""xlsx 抽出器の end-to-end テスト。空行・空列の除去挙動が中心。"""

from __future__ import annotations

from docextract.extractors import extract_xlsx
from docextract.extractors.base import ImageSaver


def _extract(path, out_dir):
    return extract_xlsx(path, ImageSaver(out_dir)).to_dict()


def _tables(data):
    return [e for e in data["elements"] if e["type"] == "table"]


def test_basic_grid_and_number_stringified(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"S1": [["x", 5], ["y", 10]]})
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["location"]["sheet"] == "S1"
    # 数値は str 化される
    assert t["rows"] == [["x", "5"], ["y", "10"]]


def test_none_cells_become_empty_string(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"S": [["a", None, "c"]]})
    data = _extract(src, tmp_path / "out")
    assert _tables(data)[0]["rows"][0] == ["a", "", "c"]


def test_trailing_empty_rows_removed(tmp_path, make_xlsx):
    # 末尾に空行 -> 除去され n_rows は実データ分のみ
    src = make_xlsx(sheets={"S": [["a", "b"], [None, None], [None, None]]})
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["n_rows"] == 1
    assert t["rows"] == [["a", "b"]]


def test_trailing_empty_columns_removed(tmp_path, make_xlsx):
    # 末尾に空列 -> 除去
    src = make_xlsx(sheets={"S": [["a", None], ["b", None]]})
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["n_cols"] == 1
    assert t["rows"] == [["a"], ["b"]]


def test_middle_empty_row_is_kept(tmp_path, make_xlsx):
    # 中間の空行は除去されない (末尾のみ除去する仕様)
    src = make_xlsx(sheets={"S": [["a", "b"], [None, None], ["c", "d"]]})
    data = _extract(src, tmp_path / "out")
    t = _tables(data)[0]
    assert t["n_rows"] == 3
    assert t["rows"][1] == ["", ""]


def test_fully_empty_sheet_yields_no_table(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"Empty": [[None, None], [None, None]]})
    data = _extract(src, tmp_path / "out")
    assert _tables(data) == []


def test_multiple_sheets_each_become_table(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"A": [["1"]], "B": [["2"]]})
    data = _extract(src, tmp_path / "out")
    tables = _tables(data)
    sheets = {t["location"]["sheet"] for t in tables}
    assert sheets == {"A", "B"}


def test_image_extracted_with_anchor(tmp_path, make_xlsx, png_file):
    src = make_xlsx(sheets={"S": [["x"]]}, image=("S", png_file, "C3"))
    out = tmp_path / "out"
    data = _extract(src, out)
    images = [e for e in data["elements"] if e["type"] == "image"]
    assert len(images) == 1
    img = images[0]
    assert img["location"]["sheet"] == "S"
    assert img["location"]["anchor"] == "C3"
    assert (out / img["file"]).exists()


def test_image_anchor_beyond_column_z(tmp_path, make_xlsx, png_file):
    # AA1 相当のアンカー -> 2 文字列への変換 (26 進の桁上がり) を検証
    src = make_xlsx(sheets={"S": [["x"]]}, image=("S", png_file, "AA1"))
    data = _extract(src, tmp_path / "out")
    img = [e for e in data["elements"] if e["type"] == "image"][0]
    assert img["location"]["anchor"] == "AA1"


def test_metadata_includes_sheet_names(tmp_path, make_xlsx):
    src = make_xlsx(sheets={"First": [["a"]], "Second": [["b"]]})
    data = _extract(src, tmp_path / "out")
    assert data["metadata"]["sheets"] == ["First", "Second"]
