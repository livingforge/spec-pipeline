"""伝統的な日本の Excel 設計書フィクスチャの生成器。

実務でよく見る「Excel を方眼紙・帳票レイアウトとして使った設計書」を
openpyxl だけで決定論的に再現する。生成される 5 ファイルはそれぞれ
異なる構造化の難所を代表する:

  screen_item_def.xlsx  画面項目定義書  ヘッダブロック(ラベル:値) + 2段結合ヘッダ表
  table_def.xlsx        テーブル定義書  複数シート + ○×印 + 空欄混じりの定義表
  houganshi_spec.xlsx   機能仕様書      Excel 方眼紙 (1文字幅列 + 横結合セルの文章)
  test_spec.xlsx        試験項目書      縦結合の分類列 + セル内改行の手順
  basic_design.xlsx     基本設計書      表紙/改訂履歴/本体の複数シート + 1シート複数表

正解データは truth/<名前>.json に手書きで宣言する (このスクリプトからは
生成しない)。生成データを変えたら truth も必ず追随させること。

使い方::

    python make_fixtures.py [出力ディレクトリ]   # 既定は ./fixtures
"""

from __future__ import annotations

import sys
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

# ── 見た目 (抽出には影響しないが、人が Excel で開いて確認できるようにする) ──
_THIN = Side(style="thin")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
_HEADER_FILL = PatternFill("solid", fgColor="D9E1F2")
_LABEL_FILL = PatternFill("solid", fgColor="F2F2F2")
_TITLE_FONT = Font(size=14, bold=True)
_BOLD = Font(bold=True)
_WRAP = Alignment(wrap_text=True, vertical="top")
_CENTER = Alignment(horizontal="center", vertical="center")


def _put(ws, ref: str, value, *, fill=None, font=None, align=None, border=True):
    """ref ("B4" または "B4:D4") に値を書き、結合と体裁を適用する。"""
    top_left = ref.split(":")[0]
    cell = ws[top_left]
    cell.value = value
    if ":" in ref:
        ws.merge_cells(ref)
    if fill:
        cell.fill = fill
    if font:
        cell.font = font
    if align:
        cell.alignment = align
    if border:
        cell.border = _BORDER
    return cell


def _table(ws, first_row: int, header: list[str], rows: list[list], *, first_col=1):
    """1段ヘッダの素朴な表を書く。"""
    for j, h in enumerate(header):
        c = ws.cell(row=first_row, column=first_col + j, value=h)
        c.fill = _HEADER_FILL
        c.font = _BOLD
        c.border = _BORDER
    for i, row in enumerate(rows, start=first_row + 1):
        for j, v in enumerate(row):
            c = ws.cell(row=i, column=first_col + j, value=v)
            c.border = _BORDER
            c.alignment = _WRAP


# ── 1. 画面項目定義書: ラベル:値ヘッダブロック + 2段結合ヘッダの項目表 ──
def build_screen_item_def(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "画面項目定義"
    for col, w in zip("ABCDEFGH", (6, 16, 16, 10, 6, 6, 14, 28)):
        ws.column_dimensions[col].width = w

    _put(ws, "A1:H1", "画面項目定義書", font=_TITLE_FONT, align=_CENTER)

    _put(ws, "A3", "システム名", fill=_LABEL_FILL)
    _put(ws, "B3:D3", "受注管理システム")
    _put(ws, "A4", "画面ID", fill=_LABEL_FILL)
    _put(ws, "B4", "SCR-001")
    _put(ws, "C4", "画面名", fill=_LABEL_FILL)
    _put(ws, "D4:E4", "受注登録画面")
    _put(ws, "A5", "作成者", fill=_LABEL_FILL)
    _put(ws, "B5", "山田太郎")
    _put(ws, "C5", "作成日", fill=_LABEL_FILL)
    _put(ws, "D5", "2026/04/01")
    _put(ws, "A6", "版数", fill=_LABEL_FILL)
    _put(ws, "B6", "1.2")

    # 2段ヘッダ: 「属性」の下に 型/桁/必須 をぶら下げる伝統形式
    for ref, text in [
        ("A8:A9", "No"),
        ("B8:B9", "項目名"),
        ("C8:C9", "物理名"),
        ("D8:F8", "属性"),
        ("G8:G9", "初期値"),
        ("H8:H9", "備考"),
    ]:
        _put(ws, ref, text, fill=_HEADER_FILL, font=_BOLD, align=_CENTER)
    for ref, text in [("D9", "型"), ("E9", "桁"), ("F9", "必須")]:
        _put(ws, ref, text, fill=_HEADER_FILL, font=_BOLD, align=_CENTER)

    items = [
        [1, "受注番号", "ORDER_NO", "文字列", 10, "○", "自動採番", "主キー"],
        [2, "受注日", "ORDER_DATE", "日付", None, "○", "当日日付", None],
        [3, "顧客コード", "CUST_CODE", "文字列", 8, "○", None, "顧客マスタ参照"],
        [4, "顧客名", "CUST_NAME", "文字列", 40, None, None, "顧客コードから自動表示"],
        [5, "受注金額", "ORDER_AMOUNT", "数値", 12, "○", 0, "税込金額"],
        [6, "備考", "REMARKS", "文字列", 200, None, None, None],
    ]
    for i, row in enumerate(items, start=10):
        for j, v in enumerate(row, start=1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = _BORDER
    wb.save(str(path))


# ── 2. テーブル定義書: 一覧シート + 定義シート (○×印・空欄混じり) ──
def build_table_def(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "テーブル一覧"
    _put(ws, "A1:E1", "テーブル定義書", font=_TITLE_FONT, align=_CENTER)
    _table(
        ws,
        3,
        ["No", "テーブルID", "論理名", "物理名", "備考"],
        [
            [1, "TBL-001", "受注", "T_ORDER", "受注ヘッダ情報"],
            [2, "TBL-002", "受注明細", "T_ORDER_DETAIL", "受注の明細行"],
        ],
    )

    ws2 = wb.create_sheet("TBL-001_受注")
    _put(ws2, "A1", "テーブルID", fill=_LABEL_FILL)
    _put(ws2, "B1", "TBL-001")
    _put(ws2, "C1", "論理名", fill=_LABEL_FILL)
    _put(ws2, "D1", "受注")
    _put(ws2, "E1", "物理名", fill=_LABEL_FILL)
    _put(ws2, "F1", "T_ORDER")
    _table(
        ws2,
        3,
        ["No", "論理名", "物理名", "型", "桁", "NULL", "PK", "備考"],
        [
            [1, "受注番号", "ORDER_NO", "CHAR", 10, "×", "○", "主キー"],
            [2, "受注日", "ORDER_DATE", "DATE", None, "×", None, None],
            [3, "顧客コード", "CUST_CODE", "CHAR", 8, "×", None, "顧客マスタFK"],
            [4, "受注金額", "ORDER_AMOUNT", "NUMBER", 12, "○", None, "税込"],
            [5, "更新日時", "UPDATED_AT", "TIMESTAMP", None, "×", None, None],
        ],
    )
    wb.save(str(path))


# ── 3. 機能仕様書: Excel 方眼紙 (1文字幅の列 + 横結合セルに文章を流す) ──
def build_houganshi_spec(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "機能仕様"
    # A..AN の 40 列を 1 文字幅にして「方眼紙」を作る
    for idx in range(1, 41):
        col = ""
        n = idx
        while n:
            n, rem = divmod(n - 1, 26)
            col = chr(65 + rem) + col
        ws.column_dimensions[col].width = 2.5

    _put(ws, "B2:AM2", "機能仕様書　受注登録機能", font=_TITLE_FONT, align=_CENTER)

    _put(ws, "B4:H4", "1. 概要", font=_BOLD, border=False)
    _put(
        ws,
        "C6:AL8",
        "本機能は、営業担当者が顧客からの受注情報を登録するための機能である。\n"
        "登録された受注情報は受注テーブルに保存され、出荷管理機能から参照される。",
        align=_WRAP,
    )

    _put(ws, "B10:H10", "2. 処理概要", font=_BOLD, border=False)
    steps = [
        ("C12:AL12", "(1) 画面から入力された受注情報の妥当性チェックを行う。"),
        ("C14:AL14", "(2) チェックエラーがある場合はエラーメッセージを表示し、処理を中断する。"),
        ("C16:AL16", "(3) チェックOKの場合、受注テーブルに受注情報を登録する。"),
        ("C18:AL18", "(4) 登録完了後、受注番号を画面に表示する。"),
    ]
    for ref, text in steps:
        _put(ws, ref, text, border=False)

    _put(ws, "B20:H20", "3. 制約事項", font=_BOLD, border=False)
    _put(ws, "C22:AL23", "受注金額が100万円を超える場合は、上長承認が必要となる。", align=_WRAP)
    wb.save(str(path))


# ── 4. 試験項目書: 大分類/中分類の縦結合 + セル内改行の手順 ──
def build_test_spec(path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "試験項目"
    for col, w in zip("ABCDEFGH", (6, 12, 12, 30, 34, 34, 8, 10)):
        ws.column_dimensions[col].width = w

    _put(ws, "A1:H1", "単体試験項目書　受注登録画面", font=_TITLE_FONT, align=_CENTER)

    header = ["No", "大分類", "中分類", "試験内容", "試験手順", "期待結果", "結果", "確認者"]
    for j, h in enumerate(header, start=1):
        c = ws.cell(row=3, column=j, value=h)
        c.fill = _HEADER_FILL
        c.font = _BOLD
        c.border = _BORDER

    rows = [
        [1, "画面表示", "初期表示", "画面初期表示時の項目状態を確認する",
         "1. メニューから受注登録を選択する\n2. 画面表示を確認する",
         "全項目が初期値で表示されること", "OK", "佐藤"],
        [2, None, None, "受注番号が自動採番されること",
         "1. 画面を表示する\n2. 受注番号欄を確認する",
         "受注番号欄に新規番号が表示されること", "OK", "佐藤"],
        [3, "入力チェック", "必須チェック", "顧客コード未入力でエラーとなること",
         "1. 顧客コードを空にする\n2. 登録ボタンを押下する",
         "「顧客コードを入力してください」と表示されること", "NG", "鈴木"],
        [4, None, None, "受注日未入力でエラーとなること",
         "1. 受注日を空にする\n2. 登録ボタンを押下する",
         "「受注日を入力してください」と表示されること", "OK", "鈴木"],
        [5, None, "形式チェック", "受注金額に文字列を入力するとエラーとなること",
         "1. 受注金額に「あああ」を入力する\n2. 登録ボタンを押下する",
         "「受注金額は数値で入力してください」と表示されること", "OK", "鈴木"],
        [6, "登録処理", "正常系", "正常値の入力で受注が登録されること",
         "1. 全項目に正常値を入力する\n2. 登録ボタンを押下する",
         "受注テーブルに1件登録され、完了メッセージが表示されること", "未実施", None],
    ]
    for i, row in enumerate(rows, start=4):
        for j, v in enumerate(row, start=1):
            c = ws.cell(row=i, column=j, value=v)
            c.border = _BORDER
            c.alignment = _WRAP
    # 分類列の縦結合 (伝統の「同上はセル結合」)
    ws.merge_cells("B4:B5")   # 画面表示
    ws.merge_cells("C4:C5")   # 初期表示
    ws.merge_cells("B6:B8")   # 入力チェック
    ws.merge_cells("C6:C7")   # 必須チェック
    wb.save(str(path))


# ── 5. 基本設計書: 表紙 + 改訂履歴 + 本体 (1 シートに複数の表) ──
def build_basic_design(path: Path) -> None:
    wb = Workbook()
    cover = wb.active
    cover.title = "表紙"
    _put(cover, "C8:J10", "受注管理システム", font=Font(size=20, bold=True),
         align=_CENTER, border=False)
    _put(cover, "C12:J13", "基本設計書", font=Font(size=24, bold=True),
         align=_CENTER, border=False)
    _put(cover, "C16:J16", "第1.2版", align=_CENTER, border=False)
    _put(cover, "C18:J18", "2026年4月1日", align=_CENTER, border=False)
    _put(cover, "C20:J20", "株式会社サンプルシステムズ", align=_CENTER, border=False)

    hist = wb.create_sheet("改訂履歴")
    _put(hist, "A1", "改訂履歴", font=_TITLE_FONT, border=False)
    _table(
        hist,
        3,
        ["版数", "改訂日", "改訂内容", "担当"],
        [
            ["1.0", "2026/01/15", "初版作成", "山田"],
            ["1.1", "2026/02/20", "受注金額の桁数を12桁に変更", "山田"],
            ["1.2", "2026/04/01", "上長承認フローの追記", "佐藤"],
        ],
    )

    body = wb.create_sheet("処理設計")
    _put(body, "A1", "5. 処理設計", font=_TITLE_FONT, border=False)
    _put(body, "A3:J4", "受注登録機能の処理一覧と外部インターフェースを以下に示す。",
         align=_WRAP, border=False)
    _put(body, "A6", "5.1 処理一覧", font=_BOLD, border=False)
    _table(
        body,
        7,
        ["No", "処理ID", "処理名", "処理概要"],
        [
            [1, "P-001", "受注登録", "受注情報を受注テーブルに登録する"],
            [2, "P-002", "受注検索", "条件に一致する受注情報を検索する"],
            [3, "P-003", "受注取消", "受注情報を論理削除する"],
        ],
    )
    # 2 行以上の空白ギャップ -> 抽出器は別の表として分割するはず
    _put(body, "A13", "5.2 外部インターフェース一覧", font=_BOLD, border=False)
    _table(
        body,
        14,
        ["No", "IF-ID", "接続先", "方式", "概要"],
        [
            [1, "IF-001", "出荷管理システム", "ファイル連携", "受注確定データを日次で連携する"],
            [2, "IF-002", "会計システム", "API連携", "売上計上データをリアルタイム連携する"],
        ],
    )
    wb.save(str(path))


BUILDERS = {
    "screen_item_def.xlsx": build_screen_item_def,
    "table_def.xlsx": build_table_def,
    "houganshi_spec.xlsx": build_houganshi_spec,
    "test_spec.xlsx": build_test_spec,
    "basic_design.xlsx": build_basic_design,
}


def build_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, builder in BUILDERS.items():
        p = out_dir / name
        builder(p)
        paths.append(p)
    return paths


def main(argv: list[str]) -> int:
    out_dir = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent / "fixtures"
    for p in build_all(out_dir):
        print(p)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
