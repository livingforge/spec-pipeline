"""日本の伝統的 Excel 設計書ベンチマークのランナー。

make_fixtures.py で生成した設計書 xlsx を docextract で抽出し、truth/*.json の
正解データと突き合わせてスコアを出す。cases.jsonl 方式 (合格/不合格の宣言) と
違い、こちらは **どこまで正しく構造化できたかを割合で測る** 能力測定である。

測る観点 (truth のフィールドに対応):

  sheets        シート名がすべてメタデータに現れるか
  key_values    ラベル:値ペアのキーと値が同一シートの抽出結果に現れるか (内容)
  must_contain  タイトル等の文字列が文書全体に現れるか (内容)
  reading_text  方眼紙の文章が読み順どおりシートに現れるか (内容)
  tables.cells  正解表の非空セルが同一シートの抽出セルに現れるか (内容)
  tables.header 見出し (多段は「親/子」を分解した各語) が現れるか (内容)
  tables.rows   正解の正規化行 (結合セル展開済み) と完全一致する抽出行があるか (構造)

「内容」系は取りこぼしの検出、「rows (構造)」は結合セル解決や行の再構成まで
できているかの検出で、後者は現状の抽出器では満点にならないことが想定される
(= 改善のターゲット)。

使い方::

    python run_benchmark.py             # 一時ディレクトリに生成して測定
    python run_benchmark.py --json      # 機械可読な JSON で出力
    python run_benchmark.py --keep DIR  # フィクスチャと抽出結果を DIR に残す
    python run_benchmark.py --strict    # 内容系が満点でなければ終了コード 1
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))


def _bootstrap_docextract() -> None:
    """docextract パッケージのある場所を探して sys.path に載せる (run_eval.py と同じ)。"""
    for base in [_HERE, *_HERE.parents]:
        if (base / "docextract" / "__init__.py").is_file():
            sys.path.insert(0, str(base))
            return
    sys.path.insert(0, str(_HERE.parents[1]))


_bootstrap_docextract()

from docextract import extract  # noqa: E402

import make_fixtures  # noqa: E402

TRUTH_DIR = _HERE / "truth"


# ── 抽出結果をシート単位のセル集合・行リスト・本文に整理する ──
def _by_sheet(data: dict) -> dict[str, dict]:
    sheets: dict[str, dict] = {}
    for el in data.get("elements", []):
        sheet = (el.get("location") or {}).get("sheet") or ""
        bucket = sheets.setdefault(sheet, {"cells": set(), "rows": []})
        if el.get("type") == "table":
            for row in el.get("rows", []):
                cells = [str(c) for c in row]
                bucket["rows"].append(cells)
                bucket["cells"].update(cells)
        elif el.get("type") == "text":
            bucket["cells"].add(el.get("content", ""))
    for bucket in sheets.values():
        bucket["haystack"] = "\n".join(bucket["cells"])
    return sheets


def _ratio(hit: int, total: int) -> float | None:
    return None if total == 0 else hit / total


def score_one(truth: dict, data: dict) -> dict:
    sheets = _by_sheet(data)
    doc_haystack = "\n".join(b["haystack"] for b in sheets.values())
    missing: list[str] = []

    # シート
    meta_sheets = data.get("metadata", {}).get("sheets", [])
    sheet_hit = sum(1 for s in truth["sheets"] if s in meta_sheets)
    for s in truth["sheets"]:
        if s not in meta_sheets:
            missing.append(f"シート欠落: {s}")

    # key_values: キーと値の両方が同じシートのセルに現れること
    kv_hit = 0
    for kv in truth.get("key_values", []):
        cells = sheets.get(kv["sheet"], {}).get("cells", set())
        if kv["key"] in cells and kv["value"] in cells:
            kv_hit += 1
        else:
            missing.append(f"キー項目欠落: {kv['key']}={kv['value']} ({kv['sheet']})")

    # must_contain: 文書全体のどこかに現れること
    mc_hit = 0
    for needle in truth.get("must_contain", []):
        if needle in doc_haystack:
            mc_hit += 1
        else:
            missing.append(f"文字列欠落: {needle!r}")

    # reading_text: 同じシートに現れること (セル内改行含め全文一致で探す)
    rt_hit = 0
    for rt in truth.get("reading_text", []):
        if rt["text"] in sheets.get(rt["sheet"], {}).get("haystack", ""):
            rt_hit += 1
        else:
            missing.append(f"文章欠落 ({rt['sheet']}): {rt['text'][:30]}...")

    # tables: セル内容 (内容) / 見出し語 (内容) / 正規化行の完全一致 (構造)
    cell_hit = cell_total = 0
    head_hit = head_total = 0
    row_hit = row_total = 0
    for table in truth.get("tables", []):
        bucket = sheets.get(table["sheet"], {"cells": set(), "rows": []})
        cells, rows = bucket["cells"], bucket["rows"]
        for col in table.get("columns", []):
            for token in col.split("/"):
                head_total += 1
                if token in cells:
                    head_hit += 1
                else:
                    missing.append(f"見出し欠落 ({table['title']}): {token}")
        for trow in table.get("rows", []):
            row_total += 1
            if trow in rows:
                row_hit += 1
            else:
                missing.append(f"行不一致 ({table['title']}): No={trow[0]}")
            for cell in trow:
                if not cell:
                    continue
                cell_total += 1
                if cell in cells:
                    cell_hit += 1
                else:
                    missing.append(f"セル欠落 ({table['title']}): {cell[:30]}")

    scores = {
        "sheets": _ratio(sheet_hit, len(truth["sheets"])),
        "key_values": _ratio(kv_hit, len(truth.get("key_values", []))),
        "must_contain": _ratio(mc_hit, len(truth.get("must_contain", []))),
        "reading_text": _ratio(rt_hit, len(truth.get("reading_text", []))),
        "table_cells": _ratio(cell_hit, cell_total),
        "table_header": _ratio(head_hit, head_total),
        "table_rows_exact": _ratio(row_hit, row_total),
    }
    content_keys = ["sheets", "key_values", "must_contain", "reading_text", "table_cells", "table_header"]
    content = [scores[k] for k in content_keys if scores[k] is not None]
    return {
        "source": truth["source"],
        "kind": truth["kind"],
        "scores": scores,
        "content_perfect": all(v == 1.0 for v in content),
        "missing": missing,
    }


def _fmt(v: float | None) -> str:
    return "  - " if v is None else f"{v * 100:3.0f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="日本の伝統的 Excel 設計書ベンチマーク")
    parser.add_argument("--json", action="store_true", help="集計を JSON で出力")
    parser.add_argument("--keep", metavar="DIR", help="フィクスチャと抽出結果をこのディレクトリに残す")
    parser.add_argument("--strict", action="store_true", help="内容系が満点でなければ終了コード 1")
    args = parser.parse_args(argv)

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    tmp = None
    if args.keep:
        workdir = Path(args.keep)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        tmp = tempfile.TemporaryDirectory()
        workdir = Path(tmp.name)

    try:
        fixture_paths = make_fixtures.build_all(workdir / "fixtures")
        results = []
        for src in fixture_paths:
            truth_path = TRUTH_DIR / (src.stem + ".json")
            truth = json.loads(truth_path.read_text(encoding="utf-8"))
            data = extract(
                src,
                output_dir=workdir / "out" / src.stem,
                ocr=False,
                image_tables=False,
                record_manifest=False,
            )
            results.append(score_one(truth, data))
    finally:
        if tmp:
            tmp.cleanup()

    summary = {
        "total": len(results),
        "content_perfect": sum(1 for r in results if r["content_perfect"]),
        "results": results,
    }

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        header = f"{'fixture':<22} {'シート':>5} {'キー':>5} {'必須':>5} {'文章':>5} {'セル':>5} {'見出':>5} {'行一致':>5}"
        print(header)
        for r in results:
            s = r["scores"]
            print(
                f"{Path(r['source']).stem:<22} {_fmt(s['sheets']):>5} {_fmt(s['key_values']):>5}"
                f" {_fmt(s['must_contain']):>5} {_fmt(s['reading_text']):>5}"
                f" {_fmt(s['table_cells']):>5} {_fmt(s['table_header']):>5}"
                f" {_fmt(s['table_rows_exact']):>5}"
            )
        for r in results:
            if r["missing"]:
                print(f"\n--- {r['source']} の不足 ({len(r['missing'])} 件、先頭 10 件) ---")
                for m in r["missing"][:10]:
                    print(f"  - {m}")
        print(f"\n内容系が満点: {summary['content_perfect']} / {summary['total']} ファイル")

    if args.strict and summary["content_perfect"] < summary["total"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
