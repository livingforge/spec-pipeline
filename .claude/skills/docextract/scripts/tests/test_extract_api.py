"""extract() エントリポイント — 形式判定・エラー処理・出力配置を検証する。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from docextract import SUPPORTED_EXTENSIONS, extract


def test_missing_file_raises_filenotfound(tmp_path):
    with pytest.raises(FileNotFoundError):
        extract(tmp_path / "does_not_exist.docx", output_dir=tmp_path / "out")


def test_directory_input_raises_filenotfound(tmp_path):
    # ディレクトリは is_file() が False -> FileNotFoundError
    d = tmp_path / "adir.docx"
    d.mkdir()
    with pytest.raises(FileNotFoundError):
        extract(d, output_dir=tmp_path / "out")


def test_unsupported_extension_raises_valueerror(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError) as ei:
        extract(f, output_dir=tmp_path / "out")
    # メッセージに対応形式一覧が含まれる
    assert ".docx" in str(ei.value)


def test_no_extension_raises_valueerror(tmp_path):
    f = tmp_path / "README"
    f.write_text("hi", encoding="utf-8")
    with pytest.raises(ValueError):
        extract(f, output_dir=tmp_path / "out")


def test_uppercase_extension_is_dispatched(tmp_path, make_docx):
    # .DOCX は小文字化されて docx 抽出器へ回る
    src = make_docx("Doc.DOCX", paragraphs=[("hi", None)])
    data = extract(src, output_dir=tmp_path / "out")
    assert data["file_type"] == "docx"


def test_xlsm_dispatched_to_xlsx(tmp_path, make_xlsx):
    src = make_xlsx("macro.xlsm", sheets={"S": [["a", "b"]]})
    data = extract(src, output_dir=tmp_path / "out")
    assert data["file_type"] == "xlsx"


def test_output_dir_naming_and_json_written(tmp_path, make_docx):
    src = make_docx("report.docx", paragraphs=[("body", None)])
    out = tmp_path / "out"
    data = extract(src, output_dir=out)
    doc_dir = out / "report_docx"
    assert doc_dir.is_dir()
    json_path = doc_dir / "result.json"
    assert json_path.exists()
    on_disk = json.loads(json_path.read_text(encoding="utf-8"))
    assert on_disk == data


def test_save_json_false_skips_file_but_returns_data(tmp_path, make_docx):
    src = make_docx("r.docx", paragraphs=[("body", None)])
    out = tmp_path / "out"
    data = extract(src, output_dir=out, save_json=False)
    assert not (out / "r_docx" / "result.json").exists()
    assert data["source"] == "r.docx"


def test_returned_dict_shape(tmp_path, make_docx):
    src = make_docx("r.docx", paragraphs=[("body", None)])
    data = extract(src, output_dir=tmp_path / "out")
    assert set(data) == {"source", "file_type", "metadata", "summary", "elements"}


def test_same_stem_different_ext_do_not_collide(tmp_path, make_docx, make_xlsx):
    # 拡張子込みの名前で分けるため衝突しない
    d = make_docx("data.docx", paragraphs=[("x", None)])
    x = make_xlsx("data.xlsx", sheets={"S": [["y"]]})
    out = tmp_path / "out"
    extract(d, output_dir=out)
    extract(x, output_dir=out)
    assert (out / "data_docx" / "result.json").exists()
    assert (out / "data_xlsx" / "result.json").exists()


def test_supported_extensions_constant():
    assert set(SUPPORTED_EXTENSIONS) >= {".docx", ".xlsx", ".xlsm", ".pptx", ".pdf"}
