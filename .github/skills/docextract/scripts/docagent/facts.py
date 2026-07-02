"""抽出ファクト (仕様・要件項目) の集約 JSON ストア。

docextract の抽出結果から、システム開発の後工程 (現状把握・設計・仕様の洗い出し)
で機械的に使える**構造化された事実**を項目単位で蓄える。各項目は必ず出典
(``doc_id`` + ``location`` + ``evidence``) を持ち、「この要件はどの資料の
どこから来たか」を後工程で辿れるようにする。

集約ストア (docagent の ``library.json``) が「文書ごとの分類・要約」を持つのに
対し、こちらは「文書を横断した仕様・要件の項目」を持つ。両者は別ファイル
(``facts.json`` / ``library.json``) だが、同じ ``run_docagent.py`` から操作する。

- 操作:  add / remove
- 参照:  get / query / stats / export
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from docextract import paths as _paths

from .store import DocAgentError, _resolve_term

SCHEMA_VERSION = 1

DEFAULT_STORE = _paths.facts_path()
DEFAULT_ITEM_TYPES = _paths.item_types_path()

# 既定の種別定義 (パッケージ同梱)。コードにハードコードしない。実行時は
# store/item_types.json (利用者が各自編集できる) が存在すればそちらを優先。
PACKAGED_ITEM_TYPES = Path(__file__).resolve().parent / "item_types.json"

# confidence に許す値 (抽出器の確信度)。
CONFIDENCE_LEVELS = ("high", "medium", "low")


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def default_item_types() -> list[str]:
    """パッケージ同梱の item_types.json から既定の種別一覧を読む。"""
    its = _read_item_types_file(PACKAGED_ITEM_TYPES)
    if its is None:
        raise DocAgentError(
            f"既定の種別定義が読めません: {PACKAGED_ITEM_TYPES}。"
            " docagent パッケージに item_types.json が同梱されているか確認してください"
        )
    return its


def _read_item_types_file(path: Path) -> list[str] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    its = data.get("item_types") if isinstance(data, dict) else data
    return list(its) if its else None


def _load_item_types(path: Path) -> list[str]:
    its = _read_item_types_file(path)
    return its if its is not None else default_item_types()


@dataclass
class FactStore:
    """抽出ファクトの集約 JSON ストア本体。"""

    path: Path
    item_types_path: Path | None = None
    version: int = SCHEMA_VERSION
    item_types: list[str] = field(default_factory=default_item_types)
    items: list[dict[str, Any]] = field(default_factory=list)

    # ── 入出力 ────────────────────────────────────────────────
    @classmethod
    def load(cls, path: str | Path, item_types_path: str | Path | None = None) -> "FactStore":
        path = Path(path)
        it_path = (
            Path(item_types_path)
            if item_types_path
            else path.parent / "item_types.json"
        )
        item_types = _load_item_types(it_path)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            store = cls(
                path=path,
                item_types_path=it_path,
                version=data.get("version", SCHEMA_VERSION),
                item_types=data.get("item_types") or item_types,
                items=data.get("items", []),
            )
            if it_path.exists():
                store.item_types = item_types
            return store
        return cls(path=path, item_types_path=it_path, item_types=item_types)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": self.version,
            "item_types": self.item_types,
            "items": self.items,
        }
        self.path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    # ── 参照 ──────────────────────────────────────────────────
    def get(self, fact_id: str) -> dict[str, Any]:
        for it in self.items:
            if it["id"] == fact_id:
                return it
        raise DocAgentError(
            f"ファクト ID '{fact_id}' は存在しません。"
            f" 一覧: python -m docagent facts"
        )

    def query(
        self,
        doc_id: str | None = None,
        type: str | None = None,
        text: str | None = None,
    ) -> list[dict[str, Any]]:
        results = self.items
        if doc_id:
            results = [it for it in results if it.get("doc_id") == doc_id]
        if type:
            # 種別も表記揺れを吸収して絞り込む (未知なら DocAgentError)。
            resolved = _resolve_term(type, self.item_types, label="種別")
            results = [it for it in results if it.get("type") == resolved]
        if text:
            t = text.lower()
            results = [it for it in results if t in _searchable(it)]
        return list(results)

    def stats(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        by_doc: dict[str, int] = {}
        for it in self.items:
            by_type[it.get("type", "?")] = by_type.get(it.get("type", "?"), 0) + 1
            by_doc[it.get("doc_id", "?")] = by_doc.get(it.get("doc_id", "?"), 0) + 1
        return {"total": len(self.items), "by_type": by_type, "by_doc": by_doc}

    def export(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "item_types": self.item_types,
            "items": self.items,
        }

    # ── 操作 ──────────────────────────────────────────────────
    def _next_id(self) -> str:
        nums = [
            int(it["id"][1:])
            for it in self.items
            if isinstance(it.get("id"), str) and it["id"][1:].isdigit()
        ]
        return f"f{(max(nums) + 1) if nums else 1:04d}"

    def add(
        self,
        doc_id: str,
        type: str,
        statement: str,
        evidence: str | None = None,
        location: dict[str, Any] | None = None,
        keywords: Iterable[str] | None = None,
        confidence: str | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        """ファクトを1件追加する。``type`` はタクソノミーへ正規化して許可。"""
        if not doc_id:
            raise DocAgentError("doc_id は必須です (どの文書から抽出したか)。")
        if not (statement or "").strip():
            raise DocAgentError("statement は必須です (抽出した事実の本文)。")
        resolved = _resolve_term(type, self.item_types, force=force, label="種別")
        if confidence is not None and confidence not in CONFIDENCE_LEVELS:
            raise DocAgentError(
                f"confidence は {', '.join(CONFIDENCE_LEVELS)} のいずれかです: {confidence}"
            )
        item = {
            "id": self._next_id(),
            "doc_id": doc_id,
            "type": resolved,
            "statement": statement.strip(),
            "evidence": (evidence or "").strip() or None,
            "location": location or {},
            "keywords": [k.strip() for k in (keywords or []) if k.strip()],
            "confidence": confidence,
            "added_at": _now(),
        }
        self.items.append(item)
        return item

    def remove(self, fact_id: str) -> dict[str, Any]:
        item = self.get(fact_id)
        self.items.remove(item)
        return item

    # ── 種別管理 ──────────────────────────────────────────────
    def add_item_type(self, name: str) -> None:
        if name not in self.item_types:
            self.item_types.append(name)

    def remove_item_type(self, name: str) -> None:
        if name in self.item_types:
            self.item_types.remove(name)

    def save_item_types(self) -> None:
        if self.item_types_path:
            self.item_types_path.parent.mkdir(parents=True, exist_ok=True)
            self.item_types_path.write_text(
                json.dumps({"item_types": self.item_types}, ensure_ascii=False, indent=2)
                + "\n",
                encoding="utf-8",
            )


def _searchable(item: dict[str, Any]) -> str:
    parts = [
        item.get("statement", ""),
        item.get("evidence") or "",
        " ".join(item.get("keywords", [])),
        item.get("doc_id", ""),
    ]
    return " ".join(parts).lower()
