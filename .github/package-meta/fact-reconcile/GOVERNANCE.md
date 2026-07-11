# Governance — fact-reconcile

このスキルのバージョニング・互換性・廃止（deprecation）方針。
変更履歴は [CHANGELOG.md](CHANGELOG.md)、依存とライセンスは
[dependencies.md](dependencies.md)、脅威と防御は [threat-model.md](threat-model.md)
を参照。

## バージョニング（SemVer）

`factreconcile.__version__`（現在 `1.0.0`）は [Semantic Versioning 2.0.0](https://semver.org/)
に従う。互換性の対象は「CLI」「`reconcile.json` のスキーマ」「mutate `plan.json` の生成」。

- **MAJOR** — 後方非互換な変更（`reconcile.json` スキーマの破壊的変更、生成 `plan.json`
  の op 形式の破壊的変更、CLI 既存フラグの意味変更）。
- **MINOR** — 後方互換な機能追加（新しいブロッキング規則、裁定観点の追加、
  `reconcile.json` への任意フィールド追加）。
- **PATCH** — 後方互換なバグ修正・クラスタリング/裁定精度の改善・ドキュメント修正。

## 公開インターフェースと互換性の範囲

- CLI: `config` とレコンサイル実行のサブコマンド・既存フラグ。
- 出力: `reconcile.json`（`concept` / `contradiction` / `term_map` の提案。**レビュー専用**）
  と、そこから決定的に生成する contextdb mutate `plan.json`（`add-item` / `status: review`）。

## 設計上の約束（安全側の既定）

- **ブロッキングは決定的で順序非依存**。同じ入力からは同じ候補クラスタが出る
  （名寄せがファクトの投入順に依存しない）。
- **矛盾（contradiction）は自動解決しない**。相反する値は必ず人のレビューに委ね、
  勝手にどちらかへ寄せない。
- **`reconcile.json` は提案（レビュー専用）**であり正本ではない。承認された concept だけを
  決定的に contextdb の `add-item`（`status: review`）へ落とし、`mutate apply --dry-run`
  で検証してから適用する。

これらは MAJOR でのみ変えうる互換性ポリシーの一部として扱う。

## 廃止（Deprecation）方針

後方非互換にしたい面は、削除前に最低 1 つの MINOR リリースで非推奨として残し、
CHANGELOG に代替手段とともに明記、削除は次の MAJOR で行う。

## 棚卸し（inventory）とレビュー周期

| 対象 | 周期 | 作業 |
|---|---|---|
| ブロッキング規則 | 半期ごと | 取りこぼし/過剰結合の傾向を確認し規則を調整 |
| プロバイダ接続（docsummary と共用） | プロバイダ API 改定時 | docsummary 側の設定追随に合わせる |
| 脅威モデル（`threat-model.md`） | 半期ごと | データ egress・矛盾の非自動解決の担保を更新 |
