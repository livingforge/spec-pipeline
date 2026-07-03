"""_bootstrap.py の承認ゲート (_gate) — fail-closed の多層防御を検証する。

脅威モデル (package-meta/docextract/threat-model.md) の T1「リモートインストーラ
の暗黙実行 / 大容量DL」に対する防御層 D1（opt-in + fail-closed 承認ゲート）が
実際に効いていることの回帰ガード。リポジトリ・バンドル両レイアウトで _bootstrap.py
を探して読み込む。
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve()


def _find_bootstrap() -> Path | None:
    candidates = [
        _HERE.parents[1] / "skill-src" / "docextract" / "scripts" / "_bootstrap.py",
        _HERE.parents[1] / "_bootstrap.py",  # バンドル: scripts/_bootstrap.py の隣が tests/
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def _load_bootstrap():
    path = _find_bootstrap()
    if path is None:
        pytest.skip("_bootstrap.py が見つからない")
    spec = importlib.util.spec_from_file_location("_bootstrap_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeStdin:
    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_noninteractive_unapproved_fails_closed(monkeypatch):
    bs = _load_bootstrap()
    monkeypatch.delenv(bs._AUTOINSTALL_ENV, raising=False)
    monkeypatch.delenv(bs._NO_AUTOINSTALL_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    with pytest.raises(SystemExit) as exc:
        bs._gate("高リスク操作", ["uv pip install ..."])
    assert exc.value.code  # 非ゼロ/メッセージ付きで停止 (既定で安全)


def test_optin_env_allows(monkeypatch):
    bs = _load_bootstrap()
    monkeypatch.setenv(bs._AUTOINSTALL_ENV, "1")
    monkeypatch.delenv(bs._NO_AUTOINSTALL_ENV, raising=False)
    # 承認済みなら例外を投げず通す
    bs._gate("高リスク操作", ["uv pip install ..."])


def test_no_autoinstall_takes_precedence_over_optin(monkeypatch):
    bs = _load_bootstrap()
    # opt-in が立っていても、明示禁止フラグが最優先で停止させる
    monkeypatch.setenv(bs._AUTOINSTALL_ENV, "1")
    monkeypatch.setenv(bs._NO_AUTOINSTALL_ENV, "1")
    with pytest.raises(SystemExit):
        bs._gate("高リスク操作", ["uv pip install ..."])


def test_interactive_decline_aborts(monkeypatch):
    bs = _load_bootstrap()
    monkeypatch.delenv(bs._AUTOINSTALL_ENV, raising=False)
    monkeypatch.delenv(bs._NO_AUTOINSTALL_ENV, raising=False)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    monkeypatch.setattr("builtins.input", lambda *a, **k: "n")
    with pytest.raises(SystemExit):
        bs._gate("高リスク操作", ["uv pip install ..."])


# --------------------------------------------------------------------------
# _run_step — 非対話時に冗長な出力をログへ退避し、標準出力を汚さない (④)
# --------------------------------------------------------------------------
class _FakeStderr(io.StringIO):
    """isatty() を制御できる stderr 代替 (write は StringIO が担う)。"""

    def __init__(self, tty: bool):
        super().__init__()
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def test_run_step_captures_output_to_log_when_noninteractive(
    tmp_path, monkeypatch, capsys
):
    bs = _load_bootstrap()
    fake_err = _FakeStderr(tty=False)
    monkeypatch.setattr(sys, "stderr", fake_err)
    log = tmp_path / "logs" / "bootstrap.log"
    bs._run_step(
        [sys.executable, "-c", "print('NOISE_LINE')"],
        log,
        "テスト手順",
    )
    # 冗長出力は stdout にも stderr にも出ず、ログファイルへ退避される
    assert "NOISE_LINE" not in capsys.readouterr().out
    assert "NOISE_LINE" not in fake_err.getvalue()
    assert "NOISE_LINE" in log.read_text(encoding="utf-8")


def test_run_step_failure_reports_tail_and_exits(tmp_path, monkeypatch):
    bs = _load_bootstrap()
    fake_err = _FakeStderr(tty=False)
    monkeypatch.setattr(sys, "stderr", fake_err)
    log = tmp_path / "logs" / "bootstrap.log"
    with pytest.raises(SystemExit) as exc:
        bs._run_step(
            [sys.executable, "-c", "import sys; print('BOOM'); sys.exit(3)"],
            log,
            "壊れる手順",
        )
    assert exc.value.code == 3
    err = fake_err.getvalue()
    assert "壊れる手順" in err
    assert "BOOM" in err  # 失敗時はログ末尾を stderr に出して原因を追える


def test_bootstrap_log_path_honors_home_env(monkeypatch, tmp_path):
    bs = _load_bootstrap()
    monkeypatch.setenv("DOCEXTRACT_HOME", str(tmp_path / "home"))
    p = bs._bootstrap_log_path(tmp_path / "root")
    assert p == tmp_path / "home" / "logs" / "bootstrap.log"

    monkeypatch.delenv("DOCEXTRACT_HOME", raising=False)
    p2 = bs._bootstrap_log_path(tmp_path / "root")
    assert p2 == tmp_path / "root" / ".docextract" / "logs" / "bootstrap.log"
