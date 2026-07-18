"""docextract — Office 文書 (docx/xlsx/pptx) と PDF から
テキスト・表・画像を抽出して JSON 形式で出力するライブラリ。

使い方 (Python API):
    from docextract import extract
    result = extract("report.docx")                 # 既定 .docextract/output/ へ
    result = extract("report.docx", output_dir="out")  # 明示指定も可

使い方 (CLI):
    python -m docextract report.docx slides.pptx
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import identity, manifest, obs, paths, sensitivity
from .extractors import (
    extract_decrypting,
    extract_doc,
    extract_docx,
    extract_pdf,
    extract_ppt,
    extract_pptx,
    extract_python,
    extract_xls,
    extract_xlsx,
)
from .extractors.base import ImageSaver
from .image_tables import detect_tables
from .models import ExtractionResult, ImageElement, TableElement
from .ocr import ocr_image

__version__ = "0.1.0"

_EXTRACTORS = {
    ".docx": extract_docx,
    ".xlsx": extract_xlsx,
    ".xlsm": extract_xlsx,
    ".pptx": extract_pptx,
    ".pdf": extract_pdf,
    # 旧 Office バイナリ形式 (.xls/.doc/.ppt) は Windows 上でインストール済みの
    # Microsoft Office を COM 自動化して OOXML へ変換してから抽出する。Office /
    # pywin32 が無い環境では「Office が必要」である旨を含む明確なエラーで停止する
    # (未対応形式として黙って弾くのではなく、要件を伝えて fail-closed)。
    ".xls": extract_xls,
    ".doc": extract_doc,
    ".ppt": extract_ppt,
    # ソースコード: 設計書が無いリポジトリでは「コードが一次資料」。ソース
    # ファイルを文書として索引・ブロック化・仕様抽出の同じ流儀に載せる
    # （コード→仕様の逆方向パイプライン。骨格の決定論抽出は codescan が担う）。
    ".py": extract_python,
}

SUPPORTED_EXTENSIONS = tuple(_EXTRACTORS)


def register_extractor(
    extension: str,
    extractor: Any,
    *,
    overwrite: bool = False,
) -> None:
    """新しい形式の抽出器を登録する（拡張ポイント／差し替え機構）。

    組み込みの ``_EXTRACTORS`` はハードコードだが、この関数で外部から形式を
    追加・差し替えできる。これにより新形式のエクステンダを、本体を書き換えずに
    足せる（登録レジストリによる依存性注入）。

    引数:
        extension: ``.foo`` のような**先頭ドット付き**の拡張子（大小無視）。
        extractor: ``(input_path: Path, saver: ImageSaver) -> ExtractionResult``
            のシグネチャを持つ callable。組み込み抽出器と同じ契約。
        overwrite: 既存の形式（``.pdf`` 等）を差し替えたいときだけ True。
            既定では既存形式への上書きを ``ValueError`` で拒否する。

    ``SUPPORTED_EXTENSIONS`` は登録に追従して更新される。
    """
    ext = extension.lower()
    if not ext.startswith(".") or len(ext) < 2:
        raise ValueError(f"拡張子は先頭ドット付きで指定してください: {extension!r}")
    if not callable(extractor):
        raise TypeError("extractor は呼び出し可能である必要があります")
    if ext in _EXTRACTORS and not overwrite:
        raise ValueError(
            f"形式 {ext} は既に登録済みです（差し替えるには overwrite=True）"
        )
    _EXTRACTORS[ext] = extractor
    global SUPPORTED_EXTENSIONS
    SUPPORTED_EXTENSIONS = tuple(_EXTRACTORS)


def available_extractors() -> dict[str, Any]:
    """登録済みの ``{拡張子: 抽出器}`` のコピーを返す（差し替え状況の確認用）。"""
    return dict(_EXTRACTORS)


def extract(
    input_path: str | Path,
    output_dir: str | Path | None = None,
    save_json: bool = True,
    ocr: bool = True,
    ocr_lang: str = "ja",
    ocr_backend: str = "auto",
    image_tables: bool = True,
    record_manifest: bool = True,
    run_id: str | None = None,
    log: "obs.Run | None" = None,
) -> dict[str, Any]:
    """1 つの文書を解析し、抽出結果を dict で返す。

    出力先は入力パスから決まる**衝突しない ID** (:mod:`identity`) のフォルダ:
    画像は ``<output_dir>/<id>/images/`` に保存され、``save_json=True`` なら
    ``<output_dir>/<id>/result.json`` も書き出す。ID は正規化済み絶対パスの
    ハッシュを含むため、別フォルダの同名ファイルでも衝突しない。``output_dir``
    省略時は ``.docextract/output`` (環境変数 ``DOCEXTRACT_HOME`` で基点変更可)。

    ``ocr=True`` の場合、抽出した各画像に対して OCR を実行し、
    画像内のテキストを ``ocr_text`` として付加する
    (スクリーンショットや図として貼られたテキスト・表への対応)。

    ``image_tables=True`` の場合、各画像に対して表検出
    (rapid_layout + rapid_table) を実行し、見つかった表を
    通常の ``table`` 要素として追加する。location には
    ``from_image`` (元画像) と ``bbox_in_image`` が入る。

    ``record_manifest=True`` かつ ``save_json=True`` なら、出力先直下の
    ``index.json`` (抽出マニフェスト) にこの文書を ID で登録する。

    ``run_id`` を渡すと、その値をマニフェストの各エントリに ``run_id`` として
    記録する。バッチや複数エージェント連携で一連の処理を横断追跡するための
    相関 ID（CLI が 1 実行につき 1 つ発番して各文書へ引き回す）。

    ``log`` に :class:`obs.Run` を渡すと、抽出の開始/完了・劣化 (画像デコード
    失敗等) を構造化ログ (JSON Lines) に相関 ID 付きで残す。省略時は ``run_id``
    (無ければ環境変数 ``DOCEXTRACT_RUN_ID``) から自動でロガーを用意するので、
    「観測ログだけから 1 run を再構成できる」性質を単体呼び出しでも保てる。
    """
    input_path = Path(input_path)
    if output_dir is None:
        output_dir = paths.output_dir()
    # 観測ロガー: 明示指定 > run_id/環境変数から構築。run_id はロガーが実際に
    # 使う値に揃え、マニフェストと監査ログで同じ相関 ID になるようにする。
    if log is None:
        log = obs.open_run("docextract", run_id, base_dir=output_dir)
    run_id = log.run_id
    if not input_path.is_file():
        log.error("extract.error", reason="file_not_found", source=str(input_path))
        raise FileNotFoundError(f"ファイルが見つかりません: {input_path}")

    ext = input_path.suffix.lower()

    # 暗号化 / IRM(RMS) 保護の検知。純 Python では復号できないため、通常の抽出器へ
    # 素通しすると「zip でない」等の不明瞭なエラーになる。ここで検知して経路を分ける:
    #   - IRM/RMS (秘密度ラベル暗号化): 操作者はアクセス権を持つ前提。その権限で動く
    #     Office に COM で開かせて復号し、平文 OOXML へ変換してから抽出する。
    #   - パスワード暗号化: アクセス権とは別に鍵(パスワード)が要り、COM で開くと入力
    #     待ちでハングしうる。復号鍵を扱わない方針のため専用エラーで fail-closed。
    protection = sensitivity.detect_protection(input_path)
    if protection is not None and protection["kind"] != "irm":
        log.error(
            "extract.error",
            reason="encrypted_document",
            kind=protection["kind"],
            source=str(input_path),
        )
        raise sensitivity.ProtectedDocumentError(
            f"暗号化された文書のため抽出できません ({protection['detail']}): "
            f"{input_path}. この文書はパスワード等で暗号化されています。docextract は"
            f"復号鍵を扱いません。復号済みのコピーを渡してください。"
        )

    if protection is not None:  # kind == "irm": アクセス権前提で Office 復号して抽出
        log.event(
            "extract.decrypt", source=str(input_path), kind=protection["kind"]
        )
        extractor = lambda p, s: extract_decrypting(p, s, protection)  # noqa: E731
    else:
        extractor = _EXTRACTORS.get(ext)
        if extractor is None:
            supported = ", ".join(SUPPORTED_EXTENSIONS)
            log.error("extract.error", reason="unsupported_format", ext=ext, source=str(input_path))
            raise ValueError(f"未対応の形式です: {ext} (対応形式: {supported})")

    # 出力フォルダ名は identity で作る衝突しない ID。別フォルダの同名ファイルでも
    # パスが違えば ID が異なるため上書き事故が起きない。
    source_key = identity.canonical_source(input_path)
    doc_id = identity.doc_id(input_path, source_key=source_key)
    doc_out_dir = Path(output_dir) / doc_id
    doc_out_dir.mkdir(parents=True, exist_ok=True)
    log.event("extract.start", doc_id=doc_id, source=str(input_path), file_type=ext.lstrip("."))

    saver = ImageSaver(doc_out_dir)
    result: ExtractionResult = extractor(input_path, saver)

    # 抽出器が握り潰さず残した劣化痕跡を、相関 ID 付きで監査ログにも流す。
    for deg in result.degradations:
        log.warn("extract.degraded", doc_id=doc_id, **deg)

    images = [el for el in result.elements if isinstance(el, ImageElement)]
    for el in images:
        image_path = doc_out_dir / el.file
        if ocr:
            el.ocr_text = ocr_image(image_path, lang=ocr_lang, backend=ocr_backend)
        if image_tables:
            for rows, bbox in detect_tables(image_path, lang=ocr_lang):
                location = dict(el.location)
                location["from_image"] = el.file
                if bbox:
                    location["bbox_in_image"] = bbox
                result.elements.append(TableElement(rows=rows, location=location))

    # 抽出後に同一性情報を付与する (抽出器は本文の抽出だけに集中させる)。
    result.id = doc_id
    result.source_abspath = source_key
    result.source_hash = identity.source_hash(source_key)
    result.content_hash = identity.content_hash(input_path)

    # 秘密度ラベル (MSIP) を成果物へ伝播する。旧形式は legacy_com が変換後 OOXML
    # から既に metadata["sensitivity"] を設定済みなので、未設定のとき (=OOXML 直接)
    # だけ入力から読む。機密文書が無印のまま下流コーパスへ流入しないようにする。
    if "sensitivity" not in result.metadata:
        label = sensitivity.read_label(input_path)
        if label:
            result.metadata["sensitivity"] = label
    if result.metadata.get("sensitivity"):
        log.event("extract.sensitivity", doc_id=doc_id, label=result.metadata["sensitivity"].get("name"))

    data = result.to_dict()

    if save_json:
        json_path = doc_out_dir / "result.json"
        json_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        if record_manifest:
            record = {
                "id": doc_id,
                "source": str(input_path),
                "source_abspath": source_key,
                "source_hash": result.source_hash,
                "content_hash": result.content_hash,
                "file_type": result.file_type,
                "result_path": (doc_out_dir / "result.json").as_posix(),
                "size": input_path.stat().st_size,
                "run_id": run_id,
            }
            # 秘密度ラベルがあれば索引にも載せ、コーパス側で機密文書を機械判定できる
            # ようにする (無印のまま横断検索へ流入させない)。
            if result.metadata.get("sensitivity"):
                record["sensitivity"] = result.metadata["sensitivity"]
            manifest.record(record, path=Path(output_dir) / "index.json")
    # 1 文書分の完了を監査ログに残す。要素数と劣化件数を載せ、観測ログだけで
    # 「何を何件抽出し、何件取りこぼしたか」を再構成できるようにする。
    log.event(
        "extract.done",
        doc_id=doc_id,
        summary=data["summary"],
        degraded=len(result.degradations),
    )
    return data
