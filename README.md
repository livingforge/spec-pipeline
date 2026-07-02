# DocExtract

Office 文書 (Word / Excel / PowerPoint) と PDF を解析し、**テキスト・表・画像**を抽出して JSON 形式で出力するツールです。

## 対応形式

| 形式 | 拡張子 | 抽出内容 |
|------|--------|----------|
| Word | `.docx` | 段落 (スタイル名付き)・表・インライン画像 — 文書内の出現順 |
| Excel | `.xlsx` `.xlsm` | シートごとの表 (数式は計算結果)・埋め込み画像 (アンカーセル付き) |
| PowerPoint | `.pptx` | テキストフレーム・表・画像 (スライド番号付き)・発表者ノート |
| PDF | `.pdf` | テキスト段落・表 (自動検出)・埋め込み画像 — ページ番号と座標 (bbox) 付き |

## セットアップ

```powershell
pip install -r requirements.txt
```

## 使い方

### CLI

```powershell
python -m docextract report.docx -o output
python -m docextract docs\*.pdf slides.pptx        # 複数・ワイルドカード可
python -m docextract --dir 資料フォルダ -o output   # フォルダ内の対応ファイルを一括
python -m docextract --dir 資料フォルダ -r -o output # サブフォルダも再帰的に
python -m docextract report.pdf --no-ocr           # 画像内テキストの OCR を無効化
```

`--dir/-d <フォルダ>`（複数指定可）で、そのフォルダ内の対応ファイル
（`.docx` `.xlsx` `.xlsm` `.pptx` `.pdf`）をすべて処理します。`-r/--recursive` で
サブフォルダも走査。位置引数にフォルダを渡しても同じ動作です（`~$` で始まる Office の
一時ファイルは自動的に除外）。

入力ファイルごとに `output/<ファイル名>_<拡張子>/` が作られ、以下が出力されます:

```
output/
└── report_docx/
    ├── result.json      # 抽出結果
    └── images/          # 抽出された画像
        ├── image_001.png
        └── ...
```

### Python API

```python
from docextract import extract

data = extract("report.docx", output_dir="output")
print(data["summary"])   # 例: {'text': 12, 'table': 3, 'image': 2}
```

## 出力 JSON の形式

```jsonc
{
  "source": "report.docx",
  "file_type": "docx",
  "metadata": { "title": "...", "author": "...", "created": "...", "modified": "..." },
  "summary": { "text": 3, "table": 1, "image": 1 },
  "elements": [
    // テキスト
    { "type": "text", "content": "月次報告書", "style": "Heading 1",
      "location": { "order": 1 } },
    // 表 (2次元配列)
    { "type": "table", "n_rows": 2, "n_cols": 3,
      "rows": [["項目", "4月", "5月"], ["売上", "100", "110"]],
      "location": { "order": 3 } },
    // 画像 (ファイルとして保存され、相対パスで参照)
    // ocr_text には画像内から OCR で読み取ったテキストが入る
    { "type": "image", "file": "images/image_001.png", "format": "png",
      "width": 60, "height": 40, "ocr_text": "図1: 売上推移 …",
      "location": { "order": 4 } }
  ]
}
```

`location` は形式ごとに異なります:

- **docx**: `order` (文書内の出現順)
- **xlsx**: `sheet` (シート名)、画像は `anchor` (A1 形式のセル)
- **pptx**: `slide` (スライド番号)、`shape_name`
- **pdf**: `page` (ページ番号)、`bbox` (座標 `[x0, y0, x1, y1]`)

## 資料整理エージェント（doc-guide / doc-analyzer / doc-qa + docagent）

抽出だけでなく、文書を **カテゴライズ → 要約 → 集約 JSON** にまとめる
カスタムエージェント一式を同梱している:

```
利用者 ──▶ doc-guide（窓口・伴走）
              │  ①説明 ②場所確認 ③抽出 ④分類+要約 ⑤集約 ⑥確認
              ├─ docextract（スキル）… 文書 → result.json（テキスト/表/画像）
              ├─ doc-analyzer（解析）… 1文書を分類+要約して保存
              └─ docagent（データ操作API）… 集約 JSON（store/library.json）

不明点は ──▶ doc-qa（質問回答ヘルプ）
```

| エージェント | 役割 | 使いどころ |
|------------|------|-----------|
| **doc-guide** | 最初の窓口。使い方を平易に説明し、アップロード〜結果確認まで伴走・オーケストレーション | 「資料を整理して」「使い方が分からない」 |
| **doc-analyzer** | 中核。1文書を固定カテゴリで分類し日本語要約、`docagent` で保存 | 個別/バッチ解析、並列処理 |
| **doc-qa** | 質問回答。使い方・カテゴリの意味・現在の状態・トラブルに回答 | 「これは何？」「結果はどこ？」 |

Claude Code 上で `@doc-guide` に「この資料を整理して」と頼めば、抽出から一覧提示
まで案内してくれる。データ操作 API `docagent` はスキルに同梱されており
（`.claude/skills/docextract/scripts/docagent/`）、CLI の詳細・集約 JSON の構造・
カテゴリ（固定タクソノミー）の変更方法は同梱の
[README](.claude/skills/docextract/scripts/docagent/README.md) を参照。

## スキルとしての配布

このツールは Claude Code / GitHub Copilot のエージェントスキルとして配布できる。
単一のソース `skill-src/` (スキル) + `agent-src/` (エージェント) + 本体パッケージ
`docextract/` から、ビルドスクリプトが `.claude/` と `.github/` の両方へ同一内容を
出力する:

```powershell
python scripts\build_skill.py        # --no-zip で zip 作成を省略
```

| 出力先 | 内容 |
|--------|------|
| `.claude/skills/docextract/` `.github/skills/docextract/` | SKILL.md・docs/・scripts/ (本体パッケージ + テスト同梱、自己完結) |
| `.claude/agents/` `.github/agents/` | doc-guide / doc-analyzer / doc-qa の各エージェント定義 |
| `.claude/package-meta/docextract/` `.github/package-meta/docextract/` | LICENSE (MIT)・CHANGELOG.md・dependencies.md (依存ライセンス一覧) |
| `dist/docextract-skill.zip` | 上記をまとめた配布物 (展開先リポジトリのルートに解凍するだけで導入完了) |

SKILL.md とエージェント .md は「共通 body + プラットフォーム別フロントマター」から
組み立てる。ソースは 1 スキル / 1 エージェントごとに次の 4 ファイル:

```
skill-src/docextract/  または  agent-src/<エージェント名>/
├── body.md                    # 両プラットフォーム共通の本文
├── frontmatter.common.yaml    # name / description など共通フロントマター
├── frontmatter.claude.yaml    # .claude 固有 (例: doc-analyzer の model: haiku)
└── frontmatter.github.yaml    # .github 固有 (例: model: claude-haiku-4.5)
```

共通部分は common / body を1箇所直せば両方に反映され、プラットフォーム固有の
フィールド (Claude の `license` や model 指定の表記差など) は各フラグメントに置く。

body.md と docs/ 内の `{{skill_dir}}` はビルド時にプラットフォーム別のスキルパス
(`.claude/skills/docextract` / `.github/skills/docextract`) へ展開される。
エージェントが実行するコマンドは、cwd に依存する `python -m docextract` ではなく、
どこから実行しても動く起動スクリプト
`python {{skill_dir}}/scripts/run_docextract.py` / `run_docagent.py` を使って書く。

## テスト

```powershell
python -m unittest discover -s tests -v                                    # リポジトリで実行
python -m unittest discover -s .claude\skills\docextract\scripts\tests -v  # ビルド済みバンドルで実行
```

フィクスチャ (docx/xlsx/pptx/pdf) はテスト実行時に生成されるため、バイナリの
コミットもネットワークも OCR モデルも不要。テストはバンドルにも同梱され、
配布先の環境でそのまま自己検証に使える。

`.claude/` `.github/` 配下は生成物なので直接編集しないこと。変更は
`skill-src/` (スキル文書)・`agent-src/` (エージェント定義)・`docextract/` (コード)
に対して行い、再ビルドする。

## 構成

```
docextract/
├── __init__.py          # extract() エントリポイント・形式判定
├── cli.py               # コマンドライン処理
├── models.py            # 抽出要素のデータモデル (text / table / image)
└── extractors/
    ├── docx_extractor.py   # python-docx
    ├── xlsx_extractor.py   # openpyxl
    ├── pptx_extractor.py   # python-pptx
    └── pdf_extractor.py    # PyMuPDF
```

## 画像内テキストの OCR

スクリーンショットや図として貼り付けられた画像の中のテキスト・表は、
文書ファイル内には「ピクセル」としてしか存在しないため、通常の抽出では取得できません。
既定で各画像に OCR を実行し、読み取れたテキストを画像要素の `ocr_text` に付加します。

バックエンドは `--ocr-backend` で選択できます:

| backend | エンジン | ライセンス | 備考 |
|---------|---------|-----------|------|
| `rapidocr` | RapidOCR (PaddleOCR モデルの ONNX 版) | Apache-2.0 | クロスプラットフォーム。初回実行時にモデルを自動ダウンロード |
| `windows` | Windows 標準 `Windows.Media.Ocr` | OS 機能 | オフラインで動作。Windows の言語パックに依存 |
| `auto` (既定) | rapidocr 優先、なければ windows | — | |

- 言語は `--ocr-lang` (既定 `ja`)
- 無効化する場合は `--no-ocr` (API では `extract(..., ocr=False)`)

## 画像内の表検出 (OSS)

画像として貼られた表を検出し、行・列構造を復元して通常の `table` 要素として出力します。
パイプラインはすべて Apache-2.0 の OSS です:

1. **rapid_layout** — レイアウト解析で画像内の表領域を検出
2. **rapid_table** (SLANet-plus) — 検出領域の表構造を復元し、セル文字列を RapidOCR で認識

検出された表の `location` には元画像への参照が入ります:

```json
{ "type": "table", "n_rows": 2, "n_cols": 3,
  "rows": [["Item", "Q1", "Q2"], ["Sales", "100", "110"]],
  "location": { "order": 2, "from_image": "images/image_001.png",
                "bbox_in_image": [135.6, 302.9, 959.0, 422.9] } }
```

- 無効化する場合は `--no-image-tables` (API では `extract(..., image_tables=False)`)
- 初回実行時にモデル (数十 MB) を自動ダウンロードします

## 依存ライブラリとライセンス

| ライブラリ | 用途 | ライセンス |
|-----------|------|-----------|
| python-docx | Word 解析 | MIT |
| openpyxl | Excel 解析 | MIT |
| python-pptx | PowerPoint 解析 | MIT |
| pdfplumber (pdfminer.six) | PDF テキスト・表 | MIT |
| pypdf | PDF 画像抽出 | BSD-3-Clause |
| rapidocr / rapid-table / rapid-layout | OCR・表検出 | Apache-2.0 |
| Pillow | 画像処理 | MIT-CMU |
| winocr | Windows OCR ラッパー | MIT (エンジンは OS 機能) |

すべて MIT / BSD / Apache-2.0 系で、**商用利用可能**な構成です。

## 備考

- PDF の表は pdfplumber の `find_tables()` による自動検出です (罫線ベース)。罫線のない表は検出できない場合があります。**画像として貼られた表**は画像内の表検出 (rapid_layout + rapid_table) で `table` 要素として抽出されます。
- PDF のテキストは、行間の広さで段落ブロックにまとめて出力します。表領域と重なるテキストは重複を避けるため除外されます。
- Word のテキストボックス内テキストは `style: "textbox"` のテキスト要素として抽出されます。
- 旧形式 (`.doc` `.xls` `.ppt`) は未対応です。事前に新形式へ変換してください。
