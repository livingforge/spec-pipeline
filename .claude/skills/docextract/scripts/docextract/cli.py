"""コマンドラインインターフェース。

例:
    python -m docextract report.docx -o out
    python -m docextract docs\\*.pdf slides.pptx
"""

from __future__ import annotations

import argparse
import glob
import sys
from pathlib import Path

from . import SUPPORTED_EXTENSIONS, extract


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="docextract",
        description=(
            "Office 文書 (docx/xlsx/pptx) と PDF からテキスト・表・画像を"
            "抽出して JSON 形式で出力します。"
        ),
    )
    parser.add_argument(
        "inputs",
        nargs="+",
        help=f"入力ファイル (対応形式: {', '.join(SUPPORTED_EXTENSIONS)})。ワイルドカード可",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default="output",
        help="出力先ディレクトリ (既定: output)",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="画像内テキストの OCR を無効化する",
    )
    parser.add_argument(
        "--ocr-lang",
        default="ja",
        help="OCR の言語 (既定: ja)",
    )
    parser.add_argument(
        "--ocr-backend",
        choices=["auto", "rapidocr", "windows"],
        default="auto",
        help="OCR バックエンド (既定: auto = rapidocr 優先、なければ Windows OCR)",
    )
    parser.add_argument(
        "--no-image-tables",
        action="store_true",
        help="画像内の表検出 (rapid_layout + rapid_table) を無効化する",
    )
    args = parser.parse_args(argv)

    # Windows のシェルはワイルドカードを展開しないため自前で展開する
    files: list[Path] = []
    for pattern in args.inputs:
        matched = [Path(p) for p in glob.glob(pattern)]
        if matched:
            files.extend(matched)
        else:
            files.append(Path(pattern))

    failed = 0
    for path in files:
        try:
            data = extract(
                path,
                output_dir=args.output_dir,
                ocr=not args.no_ocr,
                ocr_lang=args.ocr_lang,
                ocr_backend=args.ocr_backend,
                image_tables=not args.no_image_tables,
            )
        except Exception as e:
            print(f"[NG] {path}: {e}", file=sys.stderr)
            failed += 1
            continue
        summary = ", ".join(f"{k}={v}" for k, v in data["summary"].items()) or "抽出なし"
        out = Path(args.output_dir) / f"{path.stem}_{path.suffix.lstrip('.').lower()}" / "result.json"
        print(f"[OK] {path} -> {out} ({summary})")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
