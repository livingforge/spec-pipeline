"""構造化イベントログ (JSON Lines) による観測性の土台。

1 実行を **相関 ID (run_id)** で貫き、各ステップを機械可読な監査記録として残す。
人向けの `print` メッセージ (``[OK]`` 等) とは別チャネルで JSON Lines を吐き、
「観測データ (ログ) だけから 1 run を再構成できる」ことを狙う。docextract と
docagent の両方がこのモジュールを使い、同じ run_id を共有する。

環境変数:
- ``DOCEXTRACT_RUN_ID``    : 上流 (呼び出し側エージェント) が採番した run_id を
                             引き継ぐ。docextract → docagent の一連処理を 1 つの
                             ID で貫くための伝播経路。未設定なら新規採番する。
- ``DOCEXTRACT_LOG``       : 監査ログ (JSON Lines) の出力先ファイル。未指定なら
                             基点配下 ``logs/<run_id>.jsonl``。
- ``DOCEXTRACT_LOG_STDERR``: ``1`` なら stderr にも 1 行 JSON を鏡写しする
                             (対話実行でのライブ観測用)。

1 レコードの形 (最低限のフィールド)::

    {"ts": "...Z", "run_id": "run_...", "component": "docextract",
     "event": "extract.done", "level": "info", ...task固有フィールド}
"""

from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

ENV_RUN_ID = "DOCEXTRACT_RUN_ID"
ENV_LOG = "DOCEXTRACT_LOG"
ENV_LOG_STDERR = "DOCEXTRACT_LOG_STDERR"

# 並列抽出では複数スレッドが同じ監査ログへ追記する。1 レコード = 1 行の JSON Lines
# を保つため、ファイル追記をプロセス内ロックで直列化し、行が混ざらないようにする。
_emit_lock = threading.Lock()


def _now_iso() -> str:
    """ISO8601 (UTC、秒精度) のタイムスタンプ。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_run_id() -> str:
    """1 実行を識別する相関 ID (``run_<UTC時刻>_<短縮hex>``)。

    バッチ／複数エージェント連携で一連の処理を横断追跡できるよう、実行の起点で
    1 つ発番して各文書・各ステップに引き回す。**唯一の run_id 生成箇所**。
    """
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"run_{stamp}_{uuid.uuid4().hex[:6]}"


def resolve_run_id(explicit: str | None = None) -> str:
    """使う run_id を決める: 明示値 > 環境変数 > 新規採番。

    上流が ``DOCEXTRACT_RUN_ID`` を渡していれば必ずそれを引き継ぎ、
    docextract → docagent が同じ ID になるようにする。
    """
    if explicit:
        return explicit
    from_env = os.environ.get(ENV_RUN_ID)
    if from_env:
        return from_env
    return new_run_id()


class Run:
    """1 実行 (= 1 run_id) に紐づく構造化ロガー。

    ``event(name, **fields)`` を呼ぶと 1 行の JSON レコードを監査ログへ追記する。
    ``component`` (docextract / docagent など) でどの層のイベントかを区別する。
    """

    def __init__(
        self,
        run_id: str,
        component: str,
        log_path: Path | None,
        *,
        mirror_stderr: bool = False,
        extra_sink: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.run_id = run_id
        self.component = component
        self.log_path = log_path
        self._mirror_stderr = mirror_stderr
        self._extra_sink = extra_sink

    def event(self, event: str, level: str = "info", **fields: Any) -> dict[str, Any]:
        rec: dict[str, Any] = {
            "ts": _now_iso(),
            "run_id": self.run_id,
            "component": self.component,
            "event": event,
            "level": level,
        }
        rec.update(fields)
        self._emit(rec)
        return rec

    # よく使う重大度のショートカット
    def warn(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.event(event, level="warning", **fields)

    def error(self, event: str, **fields: Any) -> dict[str, Any]:
        return self.event(event, level="error", **fields)

    def child(self, component: str) -> "Run":
        """同じ run_id・出力先で component だけ差し替えた子ロガー。"""
        return Run(
            self.run_id,
            component,
            self.log_path,
            mirror_stderr=self._mirror_stderr,
            extra_sink=self._extra_sink,
        )

    def _emit(self, rec: dict[str, Any]) -> None:
        line = json.dumps(rec, ensure_ascii=False)
        if self.log_path is not None:
            with _emit_lock:
                try:
                    self.log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.log_path.open("a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except OSError:
                    # 監査ログの書き込み失敗で本処理を止めない (観測は best-effort)。
                    pass
        if self._mirror_stderr:
            print(line, file=sys.stderr)
        if self._extra_sink is not None:
            self._extra_sink(rec)


def _default_log_path(run_id: str, base_dir: str | Path | None) -> Path | None:
    """監査ログの既定パスを決める。

    ``DOCEXTRACT_LOG`` が最優先。無ければ ``<base_dir>/logs/<run_id>.jsonl``。
    ``base_dir`` も無ければ paths の基点配下に置く。
    """
    env_path = os.environ.get(ENV_LOG)
    if env_path:
        return Path(env_path)
    if base_dir is not None:
        return Path(base_dir) / "logs" / f"{run_id}.jsonl"
    from . import paths

    return paths.home_dir() / "logs" / f"{run_id}.jsonl"


def open_run(
    component: str,
    run_id: str | None = None,
    *,
    base_dir: str | Path | None = None,
    log_path: str | Path | None = None,
) -> Run:
    """`Run` ロガーを構築する。

    - ``run_id`` 省略時は :func:`resolve_run_id` で解決 (環境変数の伝播を尊重)。
    - ``log_path`` 省略時は :func:`_default_log_path` で決める。
    - 環境変数 ``DOCEXTRACT_LOG_STDERR=1`` なら stderr にも鏡写しする。
    """
    rid = resolve_run_id(run_id)
    path: Optional[Path]
    if log_path is not None:
        path = Path(log_path)
    else:
        path = _default_log_path(rid, base_dir)
    mirror = os.environ.get(ENV_LOG_STDERR) == "1"
    return Run(rid, component, path, mirror_stderr=mirror)
