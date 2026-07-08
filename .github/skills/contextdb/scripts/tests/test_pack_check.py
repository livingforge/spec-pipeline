# -*- coding: utf-8 -*-
"""pack check — block 規約のリリースチェック（設計メモ §6.3）を固定する。"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pack as packmod  # noqa: E402

PACK_DIR = Path(__file__).resolve().parents[1] / "packs" / "jp-sier-std"


def test_template_blocks_parses_ast():
    src = ('{% block cover %}c{% endblock %}'
           '{% block preface %}p{% endblock %}')
    assert packmod._template_blocks(src) == {"cover", "preface"}


def test_real_pack_satisfies_block_convention(capsys):
    rc = packmod._cmd_check(PACK_DIR)
    assert rc == 0
    assert "警告 0 件" in capsys.readouterr().out


def test_partial_block_coverage_is_flagged(capsys):
    import re
    tmp = Path(tempfile.mkdtemp(prefix="pack-check-"))
    dst = tmp / "pk"
    shutil.copytree(PACK_DIR, dst)
    t = dst / "templates" / "basic-design.html.j2"
    src = re.sub(r"\{% block appendix %\}.*?\{% endblock %\}", "",
                 t.read_text(encoding="utf-8"), flags=re.S)
    t.write_text(src, encoding="utf-8")
    rc = packmod._cmd_check(dst)
    captured = capsys.readouterr()
    combined = captured.out + captured.err
    assert rc == 0                              # 欠損は warn（error ではない）
    assert "STD-W401" in combined and "appendix" in combined
    assert "警告 1 件" in combined
