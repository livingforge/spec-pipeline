"""cli.py — 終了コード・エラー報告・ワイルドカード展開を検証する。"""

from __future__ import annotations

from docextract.cli import main


def test_success_returns_zero(tmp_path, make_docx, capsys):
    src = make_docx("a.docx", paragraphs=[("hello", None)])
    rc = main([str(src), "-o", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr()
    assert "[OK]" in out.out


def test_unsupported_file_reports_ng_and_returns_one(tmp_path, capsys):
    bad = tmp_path / "note.txt"
    bad.write_text("x", encoding="utf-8")
    rc = main([str(bad), "-o", str(tmp_path / "out")])
    assert rc == 1
    err = capsys.readouterr().err
    assert "[NG]" in err


def test_missing_file_reports_ng(tmp_path, capsys):
    rc = main([str(tmp_path / "ghost.pdf"), "-o", str(tmp_path / "out")])
    assert rc == 1
    assert "[NG]" in capsys.readouterr().err


def test_partial_failure_still_returns_one(tmp_path, make_docx, capsys):
    good = make_docx("ok.docx", paragraphs=[("hi", None)])
    bad = tmp_path / "bad.txt"
    bad.write_text("x", encoding="utf-8")
    rc = main([str(good), str(bad), "-o", str(tmp_path / "out")])
    assert rc == 1  # 1 件でも失敗すれば非ゼロ
    captured = capsys.readouterr()
    assert "[OK]" in captured.out
    assert "[NG]" in captured.err


def test_wildcard_expansion(tmp_path, make_docx, capsys):
    make_docx("one.docx", paragraphs=[("1", None)])
    make_docx("two.docx", paragraphs=[("2", None)])
    pattern = str(tmp_path / "*.docx")
    rc = main([pattern, "-o", str(tmp_path / "out")])
    assert rc == 0
    assert capsys.readouterr().out.count("[OK]") == 2


def test_nonmatching_glob_falls_back_to_literal_path(tmp_path, capsys):
    # マッチしないパターンはそのままパスとして扱われ、存在しないので NG
    pattern = str(tmp_path / "nope_*.pdf")
    rc = main([pattern, "-o", str(tmp_path / "out")])
    assert rc == 1
    assert "[NG]" in capsys.readouterr().err


def test_multiple_files_all_ok(tmp_path, make_docx, make_xlsx, capsys):
    d = make_docx("a.docx", paragraphs=[("x", None)])
    x = make_xlsx("b.xlsx", sheets={"S": [["y"]]})
    rc = main([str(d), str(x), "-o", str(tmp_path / "out")])
    assert rc == 0
    assert capsys.readouterr().out.count("[OK]") == 2


def test_default_output_dir(tmp_path, make_docx, monkeypatch, capsys):
    # -o 省略時は cwd 直下の output/ を使う
    src = make_docx("a.docx", paragraphs=[("x", None)])
    monkeypatch.chdir(tmp_path)
    rc = main([str(src)])
    assert rc == 0
    assert (tmp_path / "output" / "a_docx" / "result.json").exists()
