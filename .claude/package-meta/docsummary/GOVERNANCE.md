# Governance — docsummary

このスキルのバージョニング・互換性・廃止（deprecation）方針。
変更履歴は [CHANGELOG.md](CHANGELOG.md)、依存とライセンスは
[dependencies.md](dependencies.md)、脅威と防御は [threat-model.md](threat-model.md)
を参照。

## バージョニング（SemVer）

`docsummary.__version__`（現在 `1.0.0`）は [Semantic Versioning 2.0.0](https://semver.org/)
に従う。互換性の対象は「CLI」「要約ストアのスキーマ」「プロバイダ設定」「要約フォーマット
の枠組み」。

- **MAJOR** — 後方非互換な変更（`store/summaries.json` スキーマの破壊的変更、CLI 既存
  フラグの意味変更、プロバイダ設定キーの破壊的変更、要約出力構造の破壊的変更）。
- **MINOR** — 後方互換な機能追加（新しいプロバイダ、新しい対象選択フラグ、
  要約フォーマット/カテゴリ分類の後方互換な拡張）。
- **PATCH** — 後方互換なバグ修正・プロンプトや解析の改善・ドキュメント修正。

## 公開インターフェースと互換性の範囲

- CLI: `run` / `list` / `show` / `config` のサブコマンドと既存フラグ
  （`--dir` / `--pending` / `--all` / 文書 ID 指定 ほか）。
- 出力: 要約 Markdown（`.docextract/summaries/<doc_id>.md`）と集約
  `store/summaries.json` のスキーマ。要約構造は固定（メタデータ表 + カテゴリ + 本文）で、
  利用者が定義するのは**視点**（`summary_guide.md`）と**カテゴリ分類**
  （`summary_categories.json`）。
- 設定: プロバイダ選択と接続情報（`.env`・環境変数）。秘密の値は契約対象外
  （キー名のみが安定面）。

要約フォーマットの既定（`templates/summary_format.md`）は同梱するが、利用者が
上書きできる**既定値**であり、その文面自体は互換性ポリシーの対象外。

## 廃止（Deprecation）方針

後方非互換にしたい面は、**削除前に最低 1 つの MINOR リリースで非推奨**として残し、
CHANGELOG に代替手段とともに明記、実際の削除は次の **MAJOR** で行う。要約ストアは
`version` を持ち、リーダは未知の新フィールドを無視して読める。

## 鮮度（freshness）の扱い

要約は入力文書の**内容ハッシュ + 仕様ハッシュ**で鮮度を追跡し、`--pending` で未要約・
陳腐化分だけを対象にできる。ハッシュ算出の変更は再要約を誘発するため MINOR 以上で扱う。

## 棚卸し（inventory）とレビュー周期

| 対象 | 周期 | 作業 |
|---|---|---|
| プロバイダ接続（`.env` のキー名・エンドポイント） | プロバイダ API 改定時 | 呼び出し形式・モデル名の追随（`providers.py`） |
| 依存（`dependencies.md`） | 四半期ごと | 標準ライブラリのみ・兄弟参照の維持を確認 |
| 脅威モデル（`threat-model.md`） | 半期ごと + プロバイダ追加時 | 秘密・データ egress の防御を更新 |
