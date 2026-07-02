# 資料整理カスタムエージェント（docagent + 3 エージェント）

所持しているドキュメント（プロジェクト資料）を **抽出 → カテゴライズ → 要約** し、
結果を**ひとつの集約 JSON** にまとめる仕組み。AI に不慣れな人でも、対話しながら
「アップロード〜結果確認」まで進められるようにエージェントが伴走する。

```
利用者 ──▶ doc-guide（窓口・伴走）
              │  ①説明 ②場所確認 ③抽出 ④分類+要約 ⑤集約 ⑥確認
              ├─ docextract（スキル）… 文書 → result.json（テキスト/表/画像）
              ├─ doc-analyzer（解析）… 1文書を分類+要約して保存
              └─ docagent（データ操作API）… 集約 JSON（store/library.json）

不明点は ──▶ doc-qa（質問回答ヘルプ）
```

## 3 つのカスタムエージェント（`.claude/agents/`）

| エージェント | 役割 | 使いどころ |
|------------|------|-----------|
| **doc-guide** | 最初の窓口。使い方を平易に説明し、アップロード〜結果確認まで伴走・オーケストレーション | 「資料を整理して」「使い方が分からない」 |
| **doc-analyzer** | 中核。1文書を固定カテゴリで分類し日本語要約、`docagent` で保存 | 個別/バッチ解析、並列処理 |
| **doc-qa** | 質問回答。使い方・カテゴリの意味・現在の状態・トラブルに回答 | 「これは何？」「結果はどこ？」 |

呼び出しは Claude Code 上で `@doc-guide` のように指定する。通常はまず **@doc-guide**
に話しかければよい。

## データ操作 API（`docagent`）

カテゴライズ・要約結果を単一 JSON に集約する CLI / Python モジュール。
データの**操作**（add / set / remove）と**参照**（get / list / query / stats / export）
を提供する。

```bash
python -m docagent init                                   # ストア初期化（初回のみ）
python -m docagent add output/report_docx/result.json     # result.json を登録
python -m docagent categories                             # 固定タクソノミー表示
python -m docagent set <id> --category "報告・レポート" \
    --summary "…" --keywords "月次,売上"                   # 分類+要約を保存
python -m docagent list                                   # 一覧
python -m docagent query --category "契約・法務"           # 絞り込み
python -m docagent stats                                  # カテゴリ別・状態別集計
python -m docagent get <id> --json                        # 1件の詳細（機械可読）
python -m docagent export -o library.json                 # 集約 JSON を1ファイルに出力
```

- `--json` は全サブコマンドで機械可読出力（前後どちらの位置でも可）。
- `--store` / `--categories` で保存先・タクソノミー定義を変更可能。
- ID は入力ファイル名から安定生成（`report.docx` → `report_docx`。docextract の
  出力フォルダ名と一致）。

### 集約 JSON（`store/library.json`）の構造

```jsonc
{
  "version": 1,
  "categories": ["契約・法務", "設計・仕様", "議事録", "報告・レポート",
                 "見積・費用", "計画・提案", "マニュアル・手順", "その他"],
  "documents": [
    {
      "id": "report_docx",
      "source": "report.docx",
      "file_type": "docx",
      "result_path": "output/report_docx/result.json",  // 抽出結果への参照
      "metadata": { "title": null, "author": "…" },
      "stats": { "text": 12, "table": 3, "image": 2 },   // 要素数
      "preview": "本文・表見出し・画像OCRの抜粋（分類の手がかり、最大600字）",
      "category": "報告・レポート",   // 固定タクソノミーから1つ（未設定は null）
      "summary": "日本語の要約（未設定は null）",
      "keywords": ["月次", "売上"],
      "status": "analyzed",           // registered（登録のみ）| analyzed（分類+要約済）
      "added_at": "2026-07-02T…Z",
      "updated_at": "2026-07-02T…Z"
    }
  ]
}
```

### カテゴリ（固定タクソノミー）

`store/categories.json` で定義。既定は 8 分類（契約・法務／設計・仕様／議事録／
報告・レポート／見積・費用／計画・提案／マニュアル・手順／その他）。
`set-category` はここにある名前のみ許可（`--force` で例外設定）。編集は

```bash
python -m docagent categories add "新カテゴリ"
python -m docagent categories remove "その他"
```

## 使い方（利用者視点：Claude Code 上で）

1. 対象ファイルをこのプロジェクトのルート（`c:\DocExtract`）に置く、または場所を控える。
2. `@doc-guide` に「この資料を整理して」と頼む。
3. doc-guide が抽出 → 分類 → 要約 → 集約 → 一覧提示まで案内する。
4. 分からないことは `@doc-qa` に質問する。

## テスト

```bash
python -m unittest tests.test_docagent -v
```

フィクスチャはテスト内で生成するため、docextract 実行もネットワークも不要。

## 補足（配布とバージョン管理）

- エージェント定義（`.claude/agents/*.md`）は git 追跡対象。
- `docagent/`・`store/`・`output/` はリポジトリ直下のため `.gitignore` の許可リスト
  方式で**非追跡**（`docextract/` 本体と同じ扱い＝ローカル動作用）。集約結果を
  共有したい場合は `python -m docagent export -o library.json` で書き出す。
