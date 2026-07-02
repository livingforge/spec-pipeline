"""docagent — カテゴライズ・要約結果を単一の集約 JSON に束ねるデータ操作 API。

docextract が出力する ``result.json`` を取り込み、カテゴリと要約を付与して
``store/library.json`` に集約する。CLI (``python -m docagent``) と、この
``Library`` を直接使う Python API の両方を提供する。
"""

from __future__ import annotations

from .store import (
    BUILTIN_CATEGORIES,
    DEFAULT_CATEGORIES,
    DEFAULT_STORE,
    DocAgentError,
    Library,
    doc_id_from_source,
)

__all__ = [
    "Library",
    "DocAgentError",
    "doc_id_from_source",
    "BUILTIN_CATEGORIES",
    "DEFAULT_STORE",
    "DEFAULT_CATEGORIES",
]
