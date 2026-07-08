# -*- coding: utf-8 -*-
"""history.py のテスト — Git 履歴からの意味的な変更履歴の再構成を固定する。

一時ディレクトリに実際の Git リポジトリを作り、データルートはそのサブディレクトリ
(実運用の .contextdb/ と同じ形) に置く。git が PATH に無い環境では skip する。
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from history import collect_history, render_markdown  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None,
                                reason="git が無い環境")

METAMODEL = """
version: 1
item_types:
  entity:
    label: エンティティ
    label_field: name
    attributes:
      name: { kind: string, required: true }
  data-item:
    label: データ項目
    label_field: name
    attributes:
      name: { kind: string, required: true }
relation_types:
  has-column:
    label: 列
    from: entity
    to: data-item
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True)


def make_repo() -> tuple[Path, Path]:
    repo = Path(tempfile.mkdtemp(prefix="contextdb-hist-"))
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "テスト太郎")
    data = repo / "specdata"
    (data / "items" / "entity").mkdir(parents=True)
    (data / "items" / "data-item").mkdir(parents=True)
    (data / "relations").mkdir()
    (data / "metamodel.yaml").write_text(METAMODEL, encoding="utf-8")
    (data / "items" / "entity" / "core.yaml").write_text(
        "- { id: e1, name: 顧客 }\n", encoding="utf-8")
    (data / "items" / "data-item" / "core.yaml").write_text(
        "- { id: d1, name: コード }\n", encoding="utf-8")
    (data / "relations" / "cols.yaml").write_text(
        "- { type: has-column, from: e1, to: d1 }\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "初回登録")
    return repo, data


def commit_second(repo: Path, data: Path) -> None:
    """d2 追加・e1 改名・関係の status 変更を 2 コミット目として積む。"""
    (data / "items" / "data-item" / "core.yaml").write_text(
        "- { id: d1, name: コード }\n- { id: d2, name: 名称 }\n", encoding="utf-8")
    (data / "items" / "entity" / "core.yaml").write_text(
        "- { id: e1, name: 顧客マスタ }\n", encoding="utf-8")
    (data / "relations" / "cols.yaml").write_text(
        "- { type: has-column, from: e1, to: d1, status: approved }\n"
        "- { type: has-column, from: e1, to: d2 }\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "d2 追加と改名")


def test_initial_and_changes():
    repo, data = make_repo()
    commit_second(repo, data)
    hist = collect_history(data)
    assert len(hist) == 2
    first, second = hist
    assert first["initial"] and "初版" in first["summary"]
    assert first["author"] == "テスト太郎"
    assert [r["id"] for r in second["items_added"]] == ["d2"]
    assert [r["id"] for r in second["items_changed"]] == ["e1"]
    assert any(r["to"] == "d2" for r in second["rels_added"])
    assert any(r["to"] == "d1" for r in second["rels_changed"])
    assert second["subject"] == "d2 追加と改名"


def test_worktree_changes_appear_uncommitted():
    repo, data = make_repo()
    (data / "items" / "data-item" / "core.yaml").write_text(
        "- { id: d1, name: 商品コード }\n", encoding="utf-8")
    hist = collect_history(data)
    assert len(hist) == 2
    wt = hist[-1]
    assert wt["rev"] is None and wt["subject"] == "(未コミット)"
    assert [r["id"] for r in wt["items_changed"]] == ["d1"]


def test_item_filter():
    repo, data = make_repo()
    commit_second(repo, data)
    hist = collect_history(data, item_id="d2")
    # 初版時点に d2 は存在しないので、2 コミット目だけが残る
    assert len(hist) == 1
    assert [r["id"] for r in hist[0]["items_added"]] == ["d2"]
    # e1 は初版から存在するので、初版 + 変更の 2 エントリ
    hist_e1 = collect_history(data, item_id="e1")
    assert len(hist_e1) == 2 and hist_e1[0]["initial"]


def test_formatting_only_commit_is_skipped():
    repo, data = make_repo()
    (data / "items" / "entity" / "core.yaml").write_text(
        "# コメント追加のみ\n- { id: e1, name: 顧客 }\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "整形のみ")
    hist = collect_history(data)
    assert len(hist) == 1 and hist[0]["initial"]


def test_render_markdown():
    repo, data = make_repo()
    commit_second(repo, data)
    md = render_markdown(collect_history(data), "specdata")
    assert "# 仕様データ変更履歴 — specdata" in md
    assert "## 版 1" in md and "## 版 2" in md
    assert "**d2**" in md and "初版" in md
