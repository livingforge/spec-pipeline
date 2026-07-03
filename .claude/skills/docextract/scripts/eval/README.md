# eval — docextract 評価ハーネス

同梱テスト (`scripts/tests/`) が「自コードの自己検証 (ユニット)」であるのに対し、
ここは **評価データセットと合否基準を data として外出し**した、視点分離の評価
ハーネスです。基準（期待値・しきい値・必須フィールド）はコードに埋め込まず、
`cases.jsonl` の `expect` ブロックに宣言します。

## 構成

| ファイル | 役割 |
|---|---|
| `cases.jsonl` | 評価ケース（入力フィクスチャの仕様 + 合否基準）を 1 行 1 ケースで宣言 |
| `run_eval.py` | ケースを列挙実行し、宣言基準と突き合わせて合否を集計する専用ランナー |
| `jp_excel/` | 日本の伝統的 Excel 設計書（方眼紙・結合セル・帳票ヘッダ）の構造化能力ベンチマーク。正解データ付き。詳細は [jp_excel/README.md](jp_excel/README.md) |

## 実行

```bash
python run_eval.py            # 同ディレクトリの cases.jsonl を実行
python run_eval.py --json     # 集計を機械可読な JSON で出力
python run_eval.py mycases.jsonl
```

全ケース pass なら終了コード 0、1 件でも fail なら 1。フィクスチャは実行時生成
（docx/xlsx/pptx を runtime 依存だけで構築）でネットワーク・OCR モデル不要、
決定論的です。抽出は `ocr=False` / `image_tables=False`（非決定パスを無効化）で
走らせます。

## ケースの書き方（合否基準は data として宣言）

```json
{
  "id": "docx-heading-paragraph-table",
  "format": "docx",
  "build": { "paragraphs": [["見積書", "Heading 1"], ["本文", null]],
             "table": [["項目", "金額"], ["ライセンス", "100000"]] },
  "extract": { "ocr": false, "image_tables": false },
  "expect": {
    "required_top_keys": ["id", "source", "file_type", "summary", "elements"],
    "summary_min":       { "text": 2, "table": 1 },
    "must_contain_text": ["見積書", "ライセンス"],
    "no_degradations":   true
  }
}
```

`expect` に宣言できる合否基準:

| キー | 意味 |
|---|---|
| `required_top_keys` | `result.json` に必須のトップレベルキー |
| `summary_min` | 要素種別ごとの最小件数 |
| `must_contain_text` | いずれかの text/table 要素に含まれるべき文字列 |
| `no_degradations` | `true` なら劣化痕跡ゼロ（取りこぼしなし）を要求 |

## カバレッジと未評価サーフェス

このハーネスが評価する視点・していない視点の一覧は
[../../docs/coverage.md](../../docs/coverage.md)（カバレッジ設計）を参照。
非決定・外部依存パス（OCR 実モデル、画像内表復元、docagent 連携 E2E）は
本ハーネスでは意図的に対象外にしており、その理由と代替もそこに明記しています。
