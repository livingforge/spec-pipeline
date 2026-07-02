# result.json スキーマ

## トップレベル

```jsonc
{
  "source": "report.docx",        // 入力ファイル名
  "file_type": "docx",            // docx | xlsx | pptx | pdf
  "metadata": {                   // 文書メタデータ (無い項目は null)
    "title": "...", "author": "...",
    "created": "...", "modified": "...",
    // xlsx: "sheets": [...] / pptx: "slide_count" / pdf: "page_count"
  },
  "summary": { "text": 12, "table": 3, "image": 2 },  // type 別の要素数
  "elements": [ /* 下記の要素が文書内の出現順 */ ]
}
```

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
| xlsx | `sheet` (シート名)、画像は `anchor` (A1 形式) |
| pptx | `slide` (1 始まり)、`shape_name` |
| pdf  | `page` (1 始まり)、`bbox` `[x0, y0, x1, y1]` (pt、原点は左上) |
| 画像内の表 | 元画像の location + `from_image`、`bbox_in_image` (px) |
