"""コマンドラインインターフェース。

例:
    python -m docextract report.docx                   # 既定 .docextract/output/ へ
    python -m docextract docs\\*.pdf slides.pptx
    python -m docextract --dir 資料フォルダ            # フォルダ内の対応ファイルを一括
    python -m docextract --dir 資料フォルダ -r          # サブフォルダも再帰的に
    python -m docextract report.docx -o out            # 出力先を明示指定
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

from . import SUPPORTED_EXTENSIONS, extract, manifest, obs, paths


def _scan_dir(directory: Path, recursive: bool) -> list[Path]:
    """フォルダ内の対応形式ファイル (docx/xlsx/xlsm/pptx/pdf) を集める。

    ``recursive=True`` ならサブフォルダも辿る。一時ファイル (``~$`` で始まる
    Office のロックファイル等) は除外する。結果はパス順にソートして返す。
    """
    supported = {ext.lower() for ext in SUPPORTED_EXTENSIONS}
    it = directory.rglob("*") if recursive else directory.glob("*")
    found = [
        p
        for p in it
        if p.is_file() and p.suffix.lower() in supported and not p.name.startswith("~$")
    ]
    return sorted(found)


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
        nargs="*",
        help=(
            f"入力ファイルまたはフォルダ (対応形式: {', '.join(SUPPORTED_EXTENSIONS)})。"
            "ワイルドカード可。フォルダを渡すと中の対応ファイルを一括処理"
        ),
    )
    parser.add_argument(
        "-d",
        "--dir",
        action="append",
        default=[],
        metavar="FOLDER",
        help="指定フォルダ内の対応ファイル (docx/xlsx/pptx/pdf) をすべて処理する (複数指定可)",
    )
    parser.add_argument(
        "-r",
        "--recursive",
        action="store_true",
        help="--dir やフォルダ指定でサブフォルダも再帰的に走査する",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=None,
        help="出力先ディレクトリ (既定: .docextract/output、env DOCEXTRACT_HOME で基点変更可)",
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
    parser.add_argument(
        "--run-id",
        default=None,
        metavar="ID",
        help=(
            "この実行の相関 ID を明示指定する (既定: 環境変数 DOCEXTRACT_RUN_ID、"
            "無ければ自動採番)。docextract→docagent を同じ ID で貫きたいときに使う"
        ),
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help=(
            "ファイルごとの [OK] 等の進捗行を抑制し、エラー ([NG]) のみ stderr に出す。"
            "LLM/エージェントに標準出力を渡す際のコンテキスト圧迫を避ける。"
            "--json-summary と併用すると stdout は最終サマリ 1 行だけになる"
        ),
    )
    parser.add_argument(
        "--json-summary",
        action="store_true",
        help=(
            "処理終了時に機械可読な 1 行 JSON サマリ (run_id/成否件数/出力先/"
            "index.json パス/失敗一覧/重複) を stdout に出す。詳細は出力先の "
            "index.json・result.json・logs/<run_id>.jsonl を参照する前提の「レシート」"
        ),
    )
    # Windows コンソール (cp932) でも日本語を安全に出力する
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    args = parser.parse_args(argv)

    def info(msg: str) -> None:
        """人向けの進捗行。``--quiet`` では抑制する (エラーは別途 stderr へ)。"""
        if not args.quiet:
            print(msg)

    # 既定の出力先を解決 (extract() 内部でも解決されるが、下の完了メッセージで
    # 実際の出力先を表示するためここで確定させておく)。
    if args.output_dir is None:
        args.output_dir = str(paths.output_dir())

    if not args.inputs and not args.dir:
        parser.error("入力ファイルまたは --dir <フォルダ> を1つ以上指定してください")

    # 収集したファイル (重複は解決済みパスで排除し、指定順を保つ)
    files: list[Path] = []
    seen: set[Path] = set()

    def add_file(path: Path) -> None:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            files.append(path)

    failed = 0

    # Windows のシェルはワイルドカードを展開しないため自前で展開する。
    # 位置引数がフォルダなら中の対応ファイルを走査する。
    for pattern in args.inputs:
        p = Path(pattern)
        if p.is_dir():
            for f in _scan_dir(p, args.recursive):
                add_file(f)
            continue
        matched = [Path(m) for m in glob.glob(pattern)]
        if matched:
            for m in matched:
                if m.is_dir():
                    for f in _scan_dir(m, args.recursive):
                        add_file(f)
                else:
                    add_file(m)
        else:
            add_file(p)  # 存在しなければ後段の extract が明確なエラーを出す

    # --dir で明示指定されたフォルダを走査する
    for d in args.dir:
        dp = Path(d)
        if not dp.is_dir():
            print(f"[NG] フォルダが見つかりません: {dp}", file=sys.stderr)
            failed += 1
            continue
        matched = _scan_dir(dp, args.recursive)
        if not matched:
            scope = "（サブフォルダ含む）" if args.recursive else ""
            info(f"[--] 対応ファイルが見つかりません{scope}: {dp}")
        for f in matched:
            add_file(f)

    if not files:
        print("処理対象のファイルがありませんでした。", file=sys.stderr)
        return 1
    # この実行を横断追跡する相関 ID。各文書のマニフェスト・出力・監査ログに載せる。
    # ロガーは run_id を解決 (明示 > 環境変数 > 採番) し、監査ログ (JSON Lines) を
    # 出力先配下 logs/<run_id>.jsonl に残す。extract() にも渡して同じ ID で貫く。
    log = obs.open_run("docextract.cli", args.run_id, base_dir=args.output_dir)
    run_id = log.run_id
    log.event("run.start", targets=len(files))
    info(f"[run] run_id={run_id} 対象={len(files)} 件")
    processed_ids: list[str] = []
    failures: list[dict[str, str]] = []
    for path in files:
        try:
            data = extract(
                path,
                output_dir=args.output_dir,
                ocr=not args.no_ocr,
                ocr_lang=args.ocr_lang,
                ocr_backend=args.ocr_backend,
                image_tables=not args.no_image_tables,
                run_id=run_id,
                log=log.child("docextract"),
            )
        except Exception as e:
            log.error("run.doc_failed", source=str(path), error=repr(e))
            print(f"[NG] {path}: {e} (run_id={run_id})", file=sys.stderr)
            failures.append({"source": str(path), "error": str(e)})
            failed += 1
            continue
        summary = ", ".join(f"{k}={v}" for k, v in data["summary"].items()) or "抽出なし"
        out = Path(args.output_dir) / data["id"] / "result.json"
        processed_ids.append(data["id"])
        info(f"[OK] {path} -> {out} (id={data['id']}, {summary})")

    # 内容が同一の文書 (別名でコピーされた資料など) をマニフェストから検知して知らせる。
    # ID はパスごとに一意なので抽出は正しく分離されるが、重複は把握しておく価値がある。
    dup_groups: list[list[str]] = []
    if processed_ids:
        seen = set(processed_ids)
        mdata = manifest.load(Path(args.output_dir) / "index.json")
        for ids in manifest.duplicates(mdata).values():
            if any(i in seen for i in ids):
                sorted_ids = sorted(ids)
                log.warn("run.duplicate", ids=sorted_ids)
                dup_groups.append(sorted_ids)
                info(f"[!] 内容が同一の文書があります: {', '.join(sorted_ids)}")

    # 相関 ID 付きの完了サマリ。監査ログ (JSON Lines) と標準出力の両方に残し、
    # ログだけからでも 1 実行の成否・出力先を再構成できるようにする。
    log.event(
        "run.done",
        succeeded=len(processed_ids),
        failed=failed,
        log_path=str(log.log_path) if log.log_path else None,
    )
    info(
        f"[done] run_id={run_id} 成功={len(processed_ids)} 失敗={failed}"
        + (f" ログ={log.log_path}" if log.log_path else "")
    )

    # 機械可読な「レシート」1 行。--quiet と併用すると stdout はこの 1 行だけになり、
    # 呼び出し側 (LLM/エージェント) は成否と出力の在り処 (index.json) だけを受け取る。
    # 各文書の中身は index.json → 各 result.json、詳細な監査は logs/<run_id>.jsonl を辿る。
    if args.json_summary:
        print(
            json.dumps(
                {
                    "event": "summary",
                    "run_id": run_id,
                    "succeeded": len(processed_ids),
                    "failed": failed,
                    "output_dir": str(Path(args.output_dir)),
                    "index": str(Path(args.output_dir) / "index.json"),
                    "log_path": str(log.log_path) if log.log_path else None,
                    "ids": processed_ids,
                    "failures": failures,
                    "duplicates": dup_groups,
                },
                ensure_ascii=False,
            )
        )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
