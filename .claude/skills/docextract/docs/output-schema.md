# result.json スキーマ

## トップレベル

```jsonc
{
  "id": "report_docx_a1b2c3d4",   // 安定・衝突しない文書 ID (出力フォルダ名と一致)
  "source": "report.docx",        // 入力ファイル名
  "source_abspath": "C:/work/資料/report.docx",  // ID の基準となる正規化済み絶対パス
  "source_hash": "a1b2c3d4",      // source_abspath の sha256 先頭8桁 (ID 末尾と一致)
  "content_hash": "9f86d0…",      // ファイル内容の sha256 (重複・改変の検知用)
  "file_type": "docx",            // docx | xlsx | pptx | pdf
  "metadata": {                   // 文書メタデータ (無い項目は null)
    "title": "...", "author": "...",
    "created": "...", "modified": "...",
    // xlsx: "sheets": [...] / pptx: "slide_count" / pdf: "page_count"
    // 旧形式(.xls/.doc/.ppt)を COM 変換した場合のみ "converted_via": "Microsoft Excel COM (.xls -> .xlsx)"
    "sensitivity": {              // 秘密度ラベル(MSIP)がある場合のみ。無ければキー自体が無い
      "name": "社外秘",           // ラベル表示名
      "id": "2096f6a2-…",         // ラベル GUID
      "enabled": true,
      "set_date": "2026-07-01T00:00:00Z",
      "method": "Standard",       // Standard | Privileged
      "site_id": "…", "content_bits": "…"
      // 複数ラベルが埋め込まれている場合は "all": [ … ] に全件
    }
  },
  "summary": { "text": 12, "table": 3, "image": 2 },  // type 別の要素数
  "elements": [ /* 下記の要素が文書内の出現順 */ ]
}
```

`id` は入力ファイルの**正規化済み絶対パスのハッシュ**を含むため、別フォルダに
ある同名ファイル (`2024/議事録.docx` と `2025/議事録.docx`) でも衝突しない。
同じパスを再抽出すると同じ ID になる (冪等)。抽出結果は `output/<id>/result.json`
に置かれ、`output/index.json` (抽出マニフェスト) に ID で索引される。

`metadata.sensitivity` は秘密度ラベル (MSIP) が付いた文書に現れ (ラベルが残っている
場合)、`index.json` の該当エントリにも同じ内容が載る (機密文書をコーパスで機械判定
するため)。**IRM/RMS で暗号化された文書は、操作者の権限で Office COM により復号
してから抽出**する (要 Windows + Office + pywin32)。**パスワード暗号化**の文書だけは
鍵が別途必要なため抽出せず `extract()` が `ProtectedDocumentError` で停止する (詳細は
[usage.md](usage.md) の「秘密度ラベル・保護文書の扱い」)。なお `result.json` 自体は
無保護の平文であり元のラベルの暗号化・アクセス制御を継承しない点に注意。

## text 要素

```jsonc
{
  "type": "text",
  "content": "月次報告書",        // 段落内の改行は \n
  "style": "Heading 1",          // docx: スタイル名 / "textbox" / pptx: "notes" (省略あり)
  "location": { ... }
}
```

## table 要素

```jsonc
{
  "type": "table",
  "n_rows": 2,
  "n_cols": 3,                   // 最長行の列数
  "rows": [["項目", "4月", "5月"], ["売上", "100", "110"]],  // 全セル文字列
  "location": { ... }
}
```

- セルは常に文字列。空セルは `""`
- 画像内から検出された表は `location` に `from_image` と `bbox_in_image` を持つ
- colspan は空文字セルで列位置を保持する

## image 要素

```jsonc
{
  "type": "image",
  "file": "images/image_001.png",  // result.json からの相対パス (POSIX 形式)
  "format": "png",
  "width": 1504, "height": 757,    // 取得できた場合のみ
  "ocr_text": "図1: 売上推移 ...", // OCR 結果 (空なら省略)
  "location": { ... }
}
```

## location の形式別フィールド

| 形式 | フィールド |
|------|-----------|
| docx | `order` (文書内の出現順、1 始まり) |
| xlsx | `sheet` (シート名)、画像は `anchor` (A1 形式)、図形テキストは `style:"shape"` + `cell` (A1 形式) + `shape_name` + `shape_id`、図形の接続関係は `kind:"diagram_topology"` の 2 列テーブル (`接続元`/`接続先`) |
| pptx | `slide` (1 始まり)、`shape_name` |
| pdf  | `page` (1 始まり)、`bbox` `[x0, y0, x1, y1]` (pt、原点は左上) |
| 画像内の表 | 元画像の location + `from_image`、`bbox_in_image` (px) |
