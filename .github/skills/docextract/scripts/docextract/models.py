"""抽出結果を表すデータモデル。

すべての抽出要素は共通の dict 形式に変換され、JSON に直列化される。
- text  : 段落・見出しなどのテキストブロック
- table : 2次元配列 (rows) で表現される表
- image : ファイルとして保存された画像への参照とメタ情報
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class TextElement:
    content: str
    style: Optional[str] = None
    location: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "text", "content": self.content}
        if self.style:
            d["style"] = self.style
        if self.location:
            d["location"] = self.location
        return d


@dataclass
class TableElement:
    rows: list[list[str]]
    location: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "table",
            "n_rows": len(self.rows),
            "n_cols": max((len(r) for r in self.rows), default=0),
            "rows": self.rows,
        }
        if self.location:
            d["location"] = self.location
        return d


@dataclass
class ImageElement:
    file: str  # 保存先への相対パス
    format: str
    width: Optional[int] = None
    height: Optional[int] = None
    ocr_text: Optional[str] = None  # OCR で読み取った画像内テキスト
    location: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "image", "file": self.file, "format": self.format}
        if self.width is not None:
            d["width"] = self.width
        if self.height is not None:
            d["height"] = self.height
        if self.ocr_text:
            d["ocr_text"] = self.ocr_text
        if self.location:
            d["location"] = self.location
        return d


@dataclass
class ExtractionResult:
    source: str
    file_type: str
    metadata: dict[str, Any] = field(default_factory=dict)
    elements: list[Any] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        serialized = []
        for el in self.elements:
            d = el.to_dict()
            counts[d["type"]] = counts.get(d["type"], 0) + 1
            serialized.append(d)
        return {
            "source": self.source,
            "file_type": self.file_type,
            "metadata": self.metadata,
            "summary": counts,
            "elements": serialized,
        }
