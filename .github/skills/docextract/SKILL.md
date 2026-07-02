---
name: docextract
description: Extract text, tables, and images from Office documents (docx/xlsx/pptx) and PDF into structured JSON, with OCR for image-embedded text and reconstruction of tables pasted as pictures. Use when asked to "parse / extract / convert / 解析 / 抽出 / 構造化" the contents of Word, Excel, PowerPoint, or PDF files. Requires Python 3.10+.
---

# docextract

Office 文書 (Word / Excel / PowerPoint) と PDF を解析し、**テキスト・表・画像**を
構造化された JSON として出力するスキル。文書内に「ピクセルとしてしか存在しない」
コンテンツも取りこぼさないのが特徴:

- 画像・スクリーンショット内のテキスト → **OCR** (RapidOCR) で `ocr_text` として付加
- 画像として貼られた表 → **表検出 + 構造復元** (rapid_layout + rapid_table) で
  通常の `table` 要素 (2次元配列) として出力

依存はすべて商用利用可能な OSS (MIT / BSD / Apache-2.0)。詳細は
[package-meta/docextract/dependencies.md](../../package-meta/docextract/dependencies.md)。

## セットアップ (初回のみ)

```bash
pip install -r .github/skills/docextract/scripts/requirements.txt
```

OCR・表検出モデル (数十 MB) は初回実行時に自動ダウンロードされる。
オフライン環境では事前に一度実行してキャッシュするか、`--ocr-backend windows`
(Windows のみ、OS 標準 OCR) を使う。

## 使い方

```bash
python .github/skills/docextract/scripts/run_docextract.py <入力ファイル...> -o <出力ディレクトリ>
python .github/skills/docextract/scripts/run_docextract.py --dir <フォルダ> -o <出力ディレクトリ>      # フォルダ一括
python .github/skills/docextract/scripts/run_docextract.py --dir <フォルダ> -r -o <出力ディレクトリ>   # 再帰
```

上のパスはプロジェクトルートからの相対パス。**常にプロジェクトルートで実行**し、
スクリプトのある場所へ `cd` しない (起動スクリプトが cwd に依存せず動く)。
抽出結果を扱う docagent も同様に `python .github/skills/docextract/scripts/run_docagent.py <サブコマンド>` で起動する。

- 対応形式: `.docx` `.xlsx` `.xlsm` `.pptx` `.pdf` (ワイルドカード可)
- 入力ファイルごとに `<出力ディレクトリ>/<ファイル名>_<拡張子>/` が作られ、
  `result.json` と `images/` (抽出画像) が出力される
- **フォルダ一括**: `-d/--dir <フォルダ>` で指定フォルダ内の対応ファイルをすべて処理
  (複数指定可)。`-r/--recursive` でサブフォルダも走査。位置引数にフォルダを渡しても
  同じ (`~$` で始まる Office 一時ファイルは自動除外)
- その他のオプション: `--no-ocr` (OCR 無効)、`--no-image-tables` (画像内表検出を無効)、
  `--ocr-lang ja` (OCR 言語)、`--ocr-backend auto|rapidocr|windows`

Python API として使う場合:

```python
import sys; sys.path.insert(0, r".github/skills/docextract/scripts")
from docextract import extract
data = extract("report.docx", output_dir="out")   # dict を返し result.json も書く
```

## 出力 JSON の読み方

`elements` 配列に文書内の要素が出現順で並ぶ。要素は 3 種類:

| type | 内容 | 主なフィールド |
|------|------|---------------|
| `text` | 段落・見出し・テキストボックス | `content`, `style`, `location` |
| `table` | 表 (2次元配列) | `rows`, `n_rows`, `n_cols`, `location` |
| `image` | 抽出画像への参照 | `file`, `ocr_text`, `width`, `height`, `location` |

- `location` は形式ごとに異なる: docx=`order` (出現順) / xlsx=`sheet` /
  pptx=`slide` / pdf=`page` + `bbox`
- 画像内から検出された表は `location.from_image` に元画像のパス、
  `location.bbox_in_image` に画像内座標を持つ
- `summary` に要素種別ごとの件数、`metadata` にタイトル・作成者等が入る

完全なスキーマは [docs/output-schema.md](docs/output-schema.md)、
運用ガイド (CLI リファレンス・OCR バックエンド・自己検証テスト・
トラブルシューティング) は [docs/usage.md](docs/usage.md) を参照。

## 制限事項 (ユーザーに伝えるべきもの)

- PDF の表検出は罫線ベース (pdfplumber)。罫線のない表は検出できない場合がある
- 画像内の表は行・列構造まで復元するが、結合セルは colspan 分を空文字で埋める
- 旧形式 (`.doc` `.xls` `.ppt`) は未対応 — 新形式への変換を案内すること
- OCR の精度は完璧ではない。判読の難しい画像ではノイズが混じることを明示すること
