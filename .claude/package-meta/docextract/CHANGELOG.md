# Changelog — docextract

## 0.1.0 (2026-07-02)

初回リリース。

- Office 文書 (docx / xlsx / xlsm / pptx) と PDF からテキスト・表・画像を抽出し
  JSON 形式で出力する CLI / Python API
- 画像内テキストの OCR (`ocr_text`)。バックエンドは RapidOCR (Apache-2.0、既定) と
  Windows 標準 OCR (winocr 経由) の 2 系統、`auto` でフォールバック
- 画像として貼られた表の検出と構造復元 (rapid_layout + rapid_table / SLANet-plus)。
  行・列を復元し通常の `table` 要素として出力
- Word のテキストボックス内テキストの抽出 (`style: "textbox"`)
- PDF 解析は pdfplumber (MIT) + pypdf (BSD-3-Clause)。全依存を商用利用可能な
  OSS (MIT / BSD / Apache-2.0) で構成
- 単体テスト (18 件) をバンドルに同梱 (`scripts/tests/`)。フィクスチャは
  実行時生成でネットワーク・OCR モデル不要、配布先で自己検証できる
