"""venv コマンド (skill_launcher) の解決・委譲仕様を固定する。

`contextdb` / `docextract` コマンドの実体。cwd から上方向にスキルが解決できる
最初のディレクトリを探して __main__.py へ委譲する探索係で、ここでは
モジュールを直接 import して in-process で検証する。
リポジトリ (tests/) とスキルバンドル (scripts/tests/) の両レイアウトに対応する。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve()
_CANDIDATES = [
    _here.parents[1] / "launcher",  # バンドル: scripts/tests -> scripts/launcher
    _here.parents[1] / "src" / "skills" / "docextract" / "scripts" / "launcher",
]
LAUNCHER_DIR = next(
    (p for p in _CANDIDATES if (p / "skill_launcher.py").is_file()), None
)

pytestmark = pytest.mark.skipif(
    LAUNCHER_DIR is None, reason="launcher/skill_launcher.py が見つからない"
)


@pytest.fixture()
def launcher():
    spec = importlib.util.spec_from_file_location(
        "skill_launcher_under_test", LAUNCHER_DIR / "skill_launcher.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_skill(root: Path, name: str) -> None:
    """argv をそのまま印字して正常終了する偽スキルを .claude 側に展開する。"""
    skill_dir = root / ".claude" / "skills" / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "__main__.py").write_text(
        "import sys\nprint('ARGS:' + '|'.join(sys.argv[1:]))\nraise SystemExit(0)\n",
        encoding="utf-8",
    )


def test_outside_project_exits_2(launcher, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = launcher.main("fooskill-not-exists", [])
    assert rc == 2
    assert "スキルが見つからない" in capsys.readouterr().err


def test_resolves_upward_and_dispatches(launcher, tmp_path, monkeypatch, capsys):
    _fake_skill(tmp_path, "fooskill")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    with pytest.raises(SystemExit) as exc:
        launcher.main("fooskill", ["hello", "--flag"])
    assert exc.value.code == 0
    assert "ARGS:hello|--flag" in capsys.readouterr().out


def test_contextdb_gets_default_root_from_subdir(launcher, tmp_path, monkeypatch,
                                              capsys):
    _fake_skill(tmp_path, "contextdb")
    (tmp_path / ".contextdb").mkdir()
    sub = tmp_path / "docs"
    sub.mkdir()
    monkeypatch.chdir(sub)
    with pytest.raises(SystemExit):
        launcher.main("contextdb", ["engine"])
    out = capsys.readouterr().out
    assert "--root" in out
    assert str(tmp_path / ".contextdb") in out


def test_contextdb_keeps_explicit_root(launcher, tmp_path, monkeypatch, capsys):
    _fake_skill(tmp_path, "contextdb")
    (tmp_path / ".contextdb").mkdir()
    sub = tmp_path / "docs"
    sub.mkdir()
    monkeypatch.chdir(sub)
    with pytest.raises(SystemExit):
        launcher.main("contextdb", ["engine", "--root", "elsewhere"])
    out = capsys.readouterr().out
    assert out.count("--root") == 1
    assert "elsewhere" in out


def test_contextdb_init_gets_no_default_root(launcher, tmp_path, monkeypatch,
                                             capsys):
    """--root を受け付けない init に自動補完しない（補完すると init 側が
    未知フラグ扱いでヘルプ表示・exit 2 になる回帰バグの再現ケース）。"""
    _fake_skill(tmp_path, "contextdb")
    (tmp_path / ".contextdb").mkdir()   # 上位に既存ルートがあっても
    sub = tmp_path / "consumer-app"
    sub.mkdir()
    monkeypatch.chdir(sub)              # cwd には .contextdb が無い
    with pytest.raises(SystemExit) as exc:
        launcher.main("contextdb", ["init"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--root" not in out
    assert "ARGS:init" in out


def test_contextdb_aggregate_gets_no_default_root(launcher, tmp_path, monkeypatch,
                                                  capsys):
    """aggregate は複数ルートを位置引数で受けるため --root を補完しない。"""
    _fake_skill(tmp_path, "contextdb")
    (tmp_path / ".contextdb").mkdir()
    sub = tmp_path / "docs"
    sub.mkdir()
    monkeypatch.chdir(sub)
    with pytest.raises(SystemExit):
        launcher.main("contextdb", ["aggregate", "a", "b"])
    out = capsys.readouterr().out
    assert "--root" not in out


def test_python_package_at_root_is_not_a_skill(launcher, tmp_path, monkeypatch,
                                               capsys):
    """__init__.py を持つ同名パッケージ（docextract 等）を誤解決しない。"""
    pkg = tmp_path / "fooskill"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "__main__.py").write_text("from .x import y\n", encoding="utf-8")
    _fake_skill(tmp_path, "fooskill")  # 展開先スキルの方が本物
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc:
        launcher.main("fooskill", ["ok"])
    assert exc.value.code == 0
    assert "ARGS:ok" in capsys.readouterr().out


def test_installed_command_if_present(tmp_path):
    """共有 venv に install 済みなら、実コマンドでも動くことを確認する。"""
    exe = Path(sys.executable).parent / (
        "contextdb.exe" if sys.platform == "win32" else "contextdb"
    )
    if not exe.is_file():
        pytest.skip("contextdb コマンドが venv に未インストール")
    import subprocess
    (tmp_path / "metamodel.yaml").write_text(
        "version: 1\n"
        "item_types:\n"
        "  thing:\n"
        "    label: モノ\n"
        "    label_field: name\n"
        "    attributes:\n"
        "      name: { kind: string, required: true }\n",
        encoding="utf-8",
    )
    items = tmp_path / "items" / "thing"
    items.mkdir(parents=True)
    (items / "core.yaml").write_text("- { id: t1, name: A }\n", encoding="utf-8")
    r = subprocess.run(
        [str(exe), "engine", "--root", str(tmp_path)],
        capture_output=True, encoding="utf-8", errors="replace",
    )
    assert r.returncode == 0, r.stderr
