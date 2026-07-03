"""抽出マニフェスト (``output/index.json``) の読み書き。

docextract が抽出した文書を ID で索引する台帳。ソースの正規化済みパス・
内容ハッシュ・result.json の場所・抽出時刻を記録し、後工程が次を行えるようにする:

- 別フォルダ同名ファイルの衝突が起きていないこと (ID がユニーク) の担保
- 同一内容の重複 (``content_hash`` 一致) の検知
- 元ファイルの改変 (``content_hash`` 差分) の判定

集約ストア (docagent の ``library.json``) が「分析結果」を持つのに対し、こちらは
「抽出そのもの」を管理する土台であり、doc-indexer 系エージェントの参照元になる。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import paths

VERSION = 1

# index.json は「読み込み → 更新 → 書き戻し」で更新するため、複数スレッドから
# 同時に record() を呼ぶ (フォルダ一括抽出の並列化) と後勝ちで登録が欠落しうる。
# 同一プロセス内の並列抽出を想定し、この upsert 全体をプロセス内ロックで直列化する。
_record_lock = threading.Lock()


def _now() -> str:
    """ISO8601 (UTC) のタイムスタンプ文字列。"""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _resolve(path: str | Path | None) -> Path:
    return Path(path) if path is not None else paths.manifest_path()


def load(path: str | Path | None = None) -> dict[str, Any]:
    """マニフェストを読み込む。無ければ空の構造を返す (ファイルは作らない)。"""
    p = _resolve(path)
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and isinstance(data.get("documents"), dict):
            return data
    return {"version": VERSION, "documents": {}}


def save(data: dict[str, Any], path: str | Path | None = None) -> None:
    """マニフェストを JSON として書き出す (親ディレクトリは自動作成)。"""
    p = _resolve(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def record(entry: dict[str, Any], path: str | Path | None = None) -> dict[str, Any]:
    """``entry`` を ID で upsert してマニフェストを保存し、更新後の全体を返す。

    同じ ID (＝同じソースパス) の再抽出は上書きが正しい挙動なので黙って更新する。
    ``first_seen`` は初回登録時刻を保持する。

    読み込み〜書き戻しは :data:`_record_lock` で直列化する。並列抽出で複数文書が
    同時に登録されても、途中状態を踏んで登録を取りこぼさない。
    """
    with _record_lock:
        data = load(path)
        e = dict(entry)
        existing = data["documents"].get(e.get("id"))
        e["first_seen"] = existing.get("first_seen") if existing else _now()
        e["updated_at"] = _now()
        data["documents"][e["id"]] = e
        save(data, path)
        return data


def duplicates(data: dict[str, Any]) -> dict[str, list[str]]:
    """``content_hash`` が同一の ID 群 (2件以上) を ``{hash: [id, ...]}`` で返す。"""
    by_hash: dict[str, list[str]] = {}
    for doc_id, e in data["documents"].items():
        h = e.get("content_hash")
        if h:
            by_hash.setdefault(h, []).append(doc_id)
    return {h: ids for h, ids in by_hash.items() if len(ids) > 1}
