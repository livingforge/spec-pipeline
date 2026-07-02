"""画像内テキストの OCR。

バックエンド (backend 引数で選択):
- "rapidocr" : RapidOCR (Apache-2.0, ONNX)。クロスプラットフォームで商用利用可。
               初回実行時にモデルを自動ダウンロードする。
- "windows"  : Windows 標準の Windows.Media.Ocr (winocr 経由)。オフラインで動くが
               Windows の言語パックに依存。
- "auto"     : rapidocr が利用可能ならそれを、なければ windows を使う (既定)。
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Optional

# Windows OCR は日本語で全文字間にスペースを挿入するため、CJK 文字同士の
# 間のスペースだけを除去する
_CJK = "[　-ヿ㐀-䶿一-鿿豈-﫿＀-￯]"
_SPACE_BETWEEN_CJK = re.compile(f"(?<={_CJK}) (?={_CJK})")

# Windows OCR の最大画像サイズ (これを超える場合は縮小する)
_MAX_DIMENSION = 9500

# 一般的な言語コード → RapidOCR の lang_type
_RAPIDOCR_LANG = {"ja": "japan", "en": "en", "zh": "ch", "ko": "korean"}

_rapidocr_engines: dict[str, object] = {}


def ocr_image(path: str | Path, lang: str = "ja", backend: str = "auto") -> Optional[str]:
    """画像ファイルを OCR してテキストを返す。読み取れない場合は None。"""
    if backend in ("auto", "rapidocr"):
        text = _ocr_rapidocr(path, lang)
        if text is not None or backend == "rapidocr":
            return text
    return _ocr_windows(path, lang)


def _get_rapidocr_engine(lang: str):
    key = _RAPIDOCR_LANG.get(lang, lang)
    if key not in _rapidocr_engines:
        from rapidocr import RapidOCR

        _rapidocr_engines[key] = RapidOCR(
            params={"Rec.lang_type": key, "Global.log_level": "error"}
        )
    return _rapidocr_engines[key]


def _ocr_rapidocr(path: str | Path, lang: str) -> Optional[str]:
    try:
        engine = _get_rapidocr_engine(lang)
        result = engine(str(path))
    except Exception:
        return None
    if not result.txts:
        return None
    return "\n".join(result.txts).strip() or None


def _ocr_windows(path: str | Path, lang: str) -> Optional[str]:
    try:
        import winocr
        from PIL import Image
    except ImportError:
        return None

    try:
        img = Image.open(path)
        if max(img.size) > _MAX_DIMENSION:
            img.thumbnail((_MAX_DIMENSION, _MAX_DIMENSION))
        if img.mode != "RGBA":
            img = img.convert("RGBA")
    except Exception:
        return None

    async def _recognize(language: str):
        return await winocr.recognize_pil(img, language)

    result = None
    for language in (lang, "en"):
        try:
            result = asyncio.run(_recognize(language))
            break
        except Exception:
            continue
    if result is None:
        return None

    text = "\n".join(line.text for line in result.lines).strip()
    text = _SPACE_BETWEEN_CJK.sub("", text)
    return text or None
