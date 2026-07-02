# 依存ライブラリとライセンス — docextract

本体 (docextract) のライセンスは MIT ([LICENSE](LICENSE))。
実行時依存はすべて商用利用可能なライセンスで構成している。

## 実行時依存 (pip)

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| python-docx | Word (.docx) 解析 | MIT |
| openpyxl | Excel (.xlsx/.xlsm) 解析 | MIT |
| python-pptx | PowerPoint (.pptx) 解析 | MIT |
| pdfplumber (pdfminer.six) | PDF テキスト・表 | MIT |
| pypdf | PDF 画像抽出 | BSD-3-Clause |
| rapidocr | OCR エンジン (ONNX Runtime) | Apache-2.0 |
| rapid-layout | 画像内レイアウト解析 (表領域検出) | Apache-2.0 |
| rapid-table | 表構造復元 (SLANet-plus) | Apache-2.0 |
| Pillow | 画像処理 | MIT-CMU |
| winocr (任意) | Windows 標準 OCR のラッパー | MIT (エンジンは OS 機能) |

## 学習済みモデル (初回実行時に自動ダウンロード)

| モデル | 配布元 | ライセンス |
|--------|--------|-----------|
| PP-OCR 系 検出・認識モデル (日本語ほか) | RapidAI (PaddleOCR 由来) | Apache-2.0 |
| pp_layout_cdla (レイアウト解析) | RapidAI | Apache-2.0 |
| slanet-plus (表構造認識) | RapidAI (PaddleOCR 由来) | Apache-2.0 |

- モデルはユーザー環境の site-packages 配下にキャッシュされる
- 完全オフライン運用では、ネットワークのある環境で一度実行してキャッシュを
  作るか、`--ocr-backend windows` + `--no-image-tables` で運用する

## 意図的に採用しなかったもの

| 候補 | 理由 |
|------|------|
| PyMuPDF | AGPL-3.0 のため商用組み込みに制約 (0.1.0 で pdfplumber + pypdf に置換済み) |
| Tesseract | 外部バイナリのインストールが必要で配布が重い |
| EasyOCR / PaddleOCR 本体 | PyTorch / PaddlePaddle 依存が大きい (数 GB) |
