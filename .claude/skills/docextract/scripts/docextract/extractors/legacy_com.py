"""Microsoft Office を COM 自動化して扱う経路 — 旧形式変換と IRM/RMS 復号。

2 つの用途を同じ COM 変換 (Office で開いて OOXML へ SaveAs) で担う:

1. **旧 Office バイナリ形式 (.xls / .doc / .ppt)**: OOXML ではなく OLE2/BIFF の
   ため python-docx / openpyxl / python-pptx では読めない。Office で開いて新形式
   (OOXML) へ変換し、既存の抽出器へ委譲する。
2. **IRM/RMS (秘密度ラベル) で暗号化された文書**: 純 Python では復号できない。
   **操作者が対象文書へのアクセス権を持つ前提**で、その権限で動く Office に開かせて
   復号し、暗号化なしの OOXML へ SaveAs してから抽出する。

いずれも変換後は OCR・画像内表検出など既存パイプラインをそのまま再利用できる。

前提 (満たさない場合は「Office が必要」である旨を含む明確なエラーで停止する):
  - OS が Windows であること
  - 対応する Microsoft Office (Excel / Word / PowerPoint) がインストール済みで、
    IRM 復号の場合は操作者アカウントに対象文書へのアクセス権があること
  - pywin32 (win32com) が利用可能であること (``pip install pywin32``)

Office を使えないときは静かにフォールバックせず、必ず「Office が必要」である旨を
含むメッセージを送出する。CLI 側 (D2) が 1 ファイルの失敗を捕捉して ``[NG]`` +
非ゼロ終了へ分離するため、他ファイルの処理は止めない。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Callable

from ..models import ExtractionResult
from ..sensitivity import read_label
from .base import ImageSaver
from .docx_extractor import extract_docx
from .pptx_extractor import extract_pptx
from .xlsx_extractor import extract_xlsx

# COM 変換を必ず要する旧 Office バイナリ形式。拡張子だけで判定できるため、
# CLI の事前チェック (pywin32 未導入の早期検知) で IRM スキャン無しに拾える。
LEGACY_EXTENSIONS = frozenset({".xls", ".doc", ".ppt"})

# pywin32 未導入時に案内する具体的な導入コマンド (共有 venv 前提と素の pip の両方)。
# --force-reinstall を付けるのは、win32com が import できない状態には「完全に未導入」
# だけでなく「pip 上は導入済みだが DLL (pywin32_system32) 欠落等で import 失敗」の
# **部分破損**も含まれ、後者では素の install が no-op になって直らないため。
# --force-reinstall はクリーンな環境でも 1 回入れ直すだけで無害で、両状態を確実に直せる。
PYWIN32_INSTALL_HINT = (
    "uv pip install --python .venv/Scripts/python.exe pywin32 --force-reinstall"
    " (または pip install --force-reinstall pywin32)"
)


class OfficeUnavailableError(RuntimeError):
    """COM 変換/復号に必要な Microsoft Office / pywin32 が使えないことを表す。

    メッセージには必ず「Office が必要」である旨と、利用できないときの回避策
    (あらかじめ新形式へ変換・復号済みのコピーを渡す) を含める。
    """


class Win32ComUnavailableError(OfficeUnavailableError):
    """pywin32 (win32com / pythoncom) 自体が import できないことを表す。

    Office アプリ未導入や COM 変換失敗 (:class:`OfficeUnavailableError` の他の
    ケース) と違い、これは**実行環境の前提条件**で、対象文書に依らず同一に起きる。
    バッチ処理側 (CLI) がこの型を捕捉して「ファイル数だけ同じエラーを繰り返す」
    のを避け、1 回だけ FB して早期に停止できるよう、専用の型に切り出している。
    """


def win32com_available() -> bool:
    """pywin32 (pythoncom / win32com.client) が import できるかを返す。

    副作用なく可否だけを判定する (COM の初期化やアプリ起動はしない)。CLI が
    バッチ開始前に一度だけ呼び、旧形式/IRM 文書があるのに pywin32 が無い場合を
    早期に検知するために使う。
    """
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return True


def _office_required_error(
    ext: str, app: str, action: str, cause: object | None = None
) -> Win32ComUnavailableError:
    """pywin32 (win32com) が import できないときのエラー。

    pywin32 は再現性固定の ``requirements.lock`` に含めない方針のため bootstrap
    では入らない。手当ての具体コマンドまで案内し、失敗ログを読んで手動で調べ
    直す手間を省く。COM が動いた後の変換失敗はこの経路ではなく
    ``_com_conversion_error`` を使う (Office/pywin32 は検出済みだと切り分ける)。

    実行環境の前提条件 (対象文書に依らず同一に起きる) なので、専用型
    :class:`Win32ComUnavailableError` を返し、CLI 側が早期停止に使えるようにする。
    """
    msg = (
        f"{action}には Microsoft Office ({app}) の COM 自動化が必要です。"
        f"Windows 上でインストール済みの Microsoft {app} を COM で操作します。"
        f"Windows で Microsoft {app} が導入済みで、pywin32 が利用可能か確認して"
        f"ください。pywin32 は自動導入されないため、未導入なら "
        f"`{PYWIN32_INSTALL_HINT}` で追加してから再実行してください。利用できない"
        f"環境では、あらかじめ復号・新形式変換した .docx/.xlsx/.pptx を渡してください。"
    )
    if cause is not None:
        msg += f" (原因: {cause})"
    return Win32ComUnavailableError(msg)


def _com_conversion_error(
    ext: str, app: str, action: str, cause: object | None = None
) -> OfficeUnavailableError:
    """Office/pywin32 は検出済みだが COM 変換自体が失敗したときのエラー。

    ``_require_win32com`` を通過した後に投げるので、pywin32 の import は成功して
    いる。ここで ``_office_required_error`` の万能文言を使うと、パス不正・IRM 権限
    拒否・自動化不許可・出力形式不整合などが一律に「Office/pywin32 が無い」に化け、
    利用者を Excel 未導入の誤診へ誘導する (フィードバック不具合 2 の二次被害)。
    そこで「検出済み」を先に切り出して報告する。
    """
    msg = (
        f"{action}中に Microsoft Office ({app}) の COM 変換が失敗しました。"
        f"Office と pywin32 は検出済みです (未導入ではありません)。"
        f"対象文書へのアクセス権 (IRM/RMS の場合)、Office の自動化許可、"
        f"保存先の書き込み可否などを確認してください。"
    )
    if cause is not None:
        msg += f" (原因: {cause})"
    return OfficeUnavailableError(msg)


def _require_win32com(ext: str, app: str, action: str) -> None:
    """pywin32 (win32com / pythoncom) が import できることを確かめる。"""
    try:
        import pythoncom  # noqa: F401
        import win32com.client  # noqa: F401
    except ImportError as e:
        raise _office_required_error(ext, app, action, cause=e) from e


# --- Office アプリ別のコンバータ (COM 経由で OOXML を書き出す) ----------------
#
# 旧形式でも暗号化 OOXML でも、Office で開いて OOXML へ SaveAs すれば
# 「旧形式であること」も落ちた OOXML が得られる。ただし SaveAs は既定で
# ソースの IRM/RMS ラベルを保存先へ再適用するため、そのままでは OLE2 複合
# ファイル (暗号化 OOXML) のまま出力され、後段の openpyxl 等が
# 「File is not a zip file」で開けない。SaveAs の前に必ず _disable_irm() で
# Permission を無効化し、平文 OOXML を得る。FileFormat 定数は Office の
# enum 値をそのまま使う:
#   Excel      xlOpenXMLWorkbook            = 51  (.xlsx)
#   Word       wdFormatDocumentDefault      = 16  (.docx)
#   PowerPoint ppSaveAsOpenXMLPresentation  = 24  (.pptx)


def _disable_irm(document: object) -> None:
    """SaveAs が IRM/RMS ラベルを引き継がないよう Permission を無効化する。

    Excel/Word/PowerPoint いずれも ``document.Permission.Enabled = False`` で
    共通に扱える。非 IRM 文書では元々権限が無いため no-op として安全に呼べる
    (例外は握り潰す)。これを呼ばないと Office は保存先にも同じラベルを再適用し、
    暗号化された OLE2 コンテナのまま保存されて平文 OOXML が得られない。
    """
    try:
        document.Permission.Enabled = False
    except Exception:
        pass  # 非 IRM 文書 / Permission 非対応なら不要


def _convert_excel(src: Path, dst: Path) -> None:
    import pythoncom
    import win32com.client as com

    # Office COM は呼び出し側の cwd を共有しない。相対パスは Excel の作業
    # ディレクトリ基準で解決され「ファイルが見つからない」で失敗するため、
    # Open/SaveAs に渡す前に必ず絶対パス化する。
    src_abs = str(Path(src).resolve())
    dst_abs = str(Path(dst).resolve())

    pythoncom.CoInitialize()
    app = None
    try:
        app = com.DispatchEx("Excel.Application")
        app.Visible = False
        app.DisplayAlerts = False
        wb = app.Workbooks.Open(src_abs, ReadOnly=True)
        try:
            _disable_irm(wb)
            wb.SaveAs(dst_abs, FileFormat=51)  # xlOpenXMLWorkbook (.xlsx)
        finally:
            wb.Close(SaveChanges=False)
    finally:
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


def _convert_word(src: Path, dst: Path) -> None:
    import pythoncom
    import win32com.client as com

    # Office COM は cwd を共有しないため、必ず絶対パスで渡す (_convert_excel 参照)
    src_abs = str(Path(src).resolve())
    dst_abs = str(Path(dst).resolve())

    pythoncom.CoInitialize()
    app = None
    try:
        app = com.DispatchEx("Word.Application")
        app.Visible = False
        app.DisplayAlerts = 0  # wdAlertsNone
        # ConfirmConversions=False で「形式変換」ダイアログ待ちを避ける
        doc = app.Documents.Open(src_abs, ReadOnly=True, ConfirmConversions=False)
        try:
            _disable_irm(doc)
            doc.SaveAs2(dst_abs, FileFormat=16)  # wdFormatDocumentDefault (.docx)
        finally:
            doc.Close(SaveChanges=False)
    finally:
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


def _convert_powerpoint(src: Path, dst: Path) -> None:
    import pythoncom
    import win32com.client as com

    # Office COM は cwd を共有しないため、必ず絶対パスで渡す (_convert_excel 参照)
    src_abs = str(Path(src).resolve())
    dst_abs = str(Path(dst).resolve())

    pythoncom.CoInitialize()
    app = None
    try:
        app = com.DispatchEx("PowerPoint.Application")
        # PowerPoint は Visible=False を嫌う版があるため、ウィンドウ無しで開く
        pres = app.Presentations.Open(src_abs, ReadOnly=True, WithWindow=False)
        try:
            _disable_irm(pres)
            pres.SaveAs(dst_abs, 24)  # ppSaveAsOpenXMLPresentation (.pptx)
        finally:
            pres.Close()
    finally:
        if app is not None:
            app.Quit()
        pythoncom.CoUninitialize()


# Office アプリ別の変換仕様 (変換先拡張子・コンバータ・委譲先抽出器)
_APP_SPEC: dict[str, dict] = {
    "Excel": {"target": ".xlsx", "convert": _convert_excel, "delegate": extract_xlsx},
    "Word": {"target": ".docx", "convert": _convert_word, "delegate": extract_docx},
    "PowerPoint": {"target": ".pptx", "convert": _convert_powerpoint, "delegate": extract_pptx},
}

# 拡張子 → 担当 Office アプリ。旧形式も暗号化 OOXML も同じマッピングを使う
# (.docx が IRM 暗号化されていても Word で開けば復号される)。
_EXT_TO_APP: dict[str, str] = {
    ".xls": "Excel", ".xlsx": "Excel", ".xlsm": "Excel",
    ".doc": "Word", ".docx": "Word",
    ".ppt": "PowerPoint", ".pptx": "PowerPoint",
}


def _convert_and_extract(
    path: Path, saver: ImageSaver, app: str, source_ext: str, action: str, note: str
) -> ExtractionResult:
    """Office COM で ``path`` を OOXML へ変換し、既存抽出器へ委譲する共通処理。"""
    _require_win32com(source_ext, app, action)
    spec = _APP_SPEC[app]
    with tempfile.TemporaryDirectory(prefix="docextract_com_") as tmp:
        dst = Path(tmp) / (Path(path).stem + spec["target"])
        try:
            spec["convert"](Path(path), dst)
        except OfficeUnavailableError:
            raise
        except Exception as e:  # ここに来た時点で pywin32 は import 済み。
            # Office 未導入ではなく COM 変換自体の失敗 (パス不正・権限拒否等)。
            raise _com_conversion_error(source_ext, app, action, cause=e) from e
        if not dst.is_file():
            raise _com_conversion_error(
                source_ext, app, action, cause="変換後ファイルが生成されませんでした"
            )
        # 変換済み OOXML を既存抽出器で処理 (saver をそのまま渡し画像も回収)
        result = spec["delegate"](dst, saver)
        # 秘密度ラベルは変換後 OOXML の custom.xml に残っていれば読み継ぐ (best-effort。
        # IRM 復号後はラベルが外れることもあるが、それは許容)。
        label = read_label(dst)
    # 下流には実際のソース形式を見せる (中間の OOXML ではなく元の形式)
    result.source = Path(path).name
    result.file_type = source_ext.lstrip(".")
    result.metadata["converted_via"] = note
    if label:
        result.metadata["sensitivity"] = label
    return result


def _make_legacy_extractor(ext: str) -> Callable[[Path, ImageSaver], ExtractionResult]:
    """旧形式 (.xls/.doc/.ppt) 用の抽出器を作る。"""
    app = _EXT_TO_APP[ext]
    target = _APP_SPEC[app]["target"]

    def _extract(path: Path, saver: ImageSaver) -> ExtractionResult:
        return _convert_and_extract(
            path,
            saver,
            app,
            ext,
            action=f"旧形式 {ext} の抽出",
            note=f"Microsoft {app} COM ({ext} -> {target})",
        )

    _extract.__name__ = f"extract_{ext.lstrip('.')}"
    _extract.__qualname__ = _extract.__name__
    return _extract


extract_xls = _make_legacy_extractor(".xls")
extract_doc = _make_legacy_extractor(".doc")
extract_ppt = _make_legacy_extractor(".ppt")


def extract_decrypting(
    path: Path, saver: ImageSaver, protection: dict
) -> ExtractionResult:
    """IRM/RMS 保護文書を Office COM で復号して抽出する。

    **操作者が対象文書へのアクセス権を持つ前提**。IRM/RMS は署名済みユーザーの
    権限で Office が復号する。復号済み OOXML へ SaveAs してから既存抽出器へ委譲する。

    パスワード暗号化 (``kind == "encrypted"``) はアクセス権とは別にパスワードが
    要り、COM で開くとパスワード入力待ちでハングしうるため、この関数の対象外
    (呼び出し側 ``extract()`` が手前で弾く)。
    """
    ext = Path(path).suffix.lower()
    app = _EXT_TO_APP.get(ext)
    if app is None:
        raise OfficeUnavailableError(
            f"保護された文書 {path} の形式 ({ext}) は Office での復号に対応しません。"
            f"復号済みの .docx/.xlsx/.pptx を渡してください。"
        )
    detail = protection.get("detail", "IRM/RMS 保護")
    return _convert_and_extract(
        path,
        saver,
        app,
        ext,
        action=f"IRM/RMS 保護 ({ext}) の復号・抽出",
        note=f"Microsoft {app} COM decrypt ({detail})",
    )


__all__ = [
    "OfficeUnavailableError",
    "Win32ComUnavailableError",
    "LEGACY_EXTENSIONS",
    "PYWIN32_INSTALL_HINT",
    "win32com_available",
    "extract_xls",
    "extract_doc",
    "extract_ppt",
    "extract_decrypting",
]
