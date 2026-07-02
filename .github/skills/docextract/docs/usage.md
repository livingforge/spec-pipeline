# docextract 運用ガイド

## 対応形式と抽出内容

| 形式 | 拡張子 | 抽出内容 |
|------|--------|----------|
| Word | `.docx` | 段落 (スタイル名付き)・表・インライン画像・テキストボックス — 文書内の出現順 |
| Excel | `.xlsx` `.xlsm` | シートごとの表 (数式は計算結果)・埋め込み画像 (アンカーセル付き) |
| PowerPoint | `.pptx` | テキストフレーム・表・画像 (スライド番号付き)・発表者ノート |
| PDF | `.pdf` | テキスト段落・表 (自動検出)・埋め込み画像 — ページ番号と座標 (bbox) 付き |

## CLI リファレンス

```
python .github/skills/docextract/scripts/run_docextract.py <入力...> [オプション]

  <入力...>          入力ファイル。複数指定・ワイルドカード可
  -o, --output-dir   出力先ディレクトリ (既定: output)
  --no-ocr           画像内テキストの OCR を無効化
  --ocr-lang <lang>  OCR の言語 (既定: ja)
  --ocr-backend      auto | rapidocr | windows (既定: auto)
  --no-image-tables  画像内の表検出を無効化
```

終了コード: 全ファイル成功で 0、1 つでも失敗すると 1 (失敗ファイルは stderr に `[NG]`)。

## 出力レイアウト

```
<output-dir>/
└── <ファイル名>_<拡張子>/     # 例: report_docx/ (同名異形式の衝突を防ぐ)
    ├── result.json           # 抽出結果 (UTF-8, ensure_ascii=False)
    └── images/               # 抽出された画像 (image_001.png, ...)
```

## OCR バックエンドの選択

| backend | エンジン | 特徴 |
|---------|---------|------|
| `rapidocr` | RapidOCR (PaddleOCR モデルの ONNX 版) | クロスプラットフォーム。初回にモデルを自動ダウンロード |
| `windows` | Windows 標準 `Windows.Media.Ocr` | 完全オフライン。Windows の言語パックに依存 |
| `auto` (既定) | rapidocr → windows の順にフォールバック | |

## 画像内の表検出パイプライン

1. **rapid_layout** — レイアウト解析で画像内の表領域 (bbox) を検出 (スコア 0.5 未満は棄却)
2. **rapid_table** (SLANet-plus) — 領域を切り出して表構造を復元、セル文字は RapidOCR で認識
3. HTML → `rows` (2次元配列) に変換して `table` 要素として出力

依存パッケージが無い環境やモデル未取得での失敗時は静かにスキップされ、
抽出全体は失敗しない (表要素が出ないだけ)。

## 自己検証 (バンドル同梱テスト)

バンドルには単体テストが同梱されており、配布先の環境でそのまま実行できる。
導入直後や依存更新後の動作確認に使う:

```bash
python -m unittest discover -s .github/skills/docextract/scripts/tests -v
```

数秒で完了する。フィクスチャ (docx/xlsx/pptx/pdf) はテスト実行時に生成される
ため、ネットワークも OCR モデルも不要。

## トラブルシューティング

- **画像内のテキストが取れない**: `--no-ocr` を付けていないか確認。初回はモデル
  ダウンロードのためネットワークが必要 (プロキシ環境では失敗しうる)
- **表が `table` 要素にならない**: 罫線のない PDF 表は検出不可のことがある。
  画像内の表はレイアウト検出スコアが低いと棄却される
- **Excel の数式が None になる**: 保存時に計算結果キャッシュがないファイルは
  `data_only=True` で値が取れない。Excel で開いて保存し直すと解消する
- **文字化けして見える**: result.json は UTF-8。ビューア側のエンコーディングを確認
