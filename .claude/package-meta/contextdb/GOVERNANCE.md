# Governance — contextdb

このスキルのバージョニング・互換性・廃止（deprecation）方針。
変更履歴は [CHANGELOG.md](CHANGELOG.md)、依存とライセンスは
[dependencies.md](dependencies.md)、脅威と防御は [threat-model.md](threat-model.md)
を参照。

## バージョニング（SemVer）

[Semantic Versioning 2.0.0](https://semver.org/) に準じる。互換性の対象は
「仕様データのスキーマ」「CLI」「生成ビュー」の 3 面。

- **MAJOR** — 後方非互換な変更（`metamodel.yaml` の宣言方法の破壊的変更、
  既存アイテム/関係の必須属性・カーディナリティの厳格化、CLI 既存フラグの意味変更、
  標準パックの互換性のない改版）。
- **MINOR** — 後方互換な機能追加（新しい種別・属性・関係の追加、新しいサブコマンド／
  フラグ、新しい生成ビュー、標準パックの後方互換な拡張）。
- **PATCH** — 後方互換なバグ修正・検証や生成の改善・ドキュメント修正。

## 公開インターフェースと互換性の範囲

安定を保証する面（＝互換性ポリシーの対象）:

- データ: `items/` `relations/` `metamodel.yaml` のスキーマと ID 採番規約
  （`id_prefix` / `sequence`）。標準パックの `extends` 解決と準拠検証（L1+L2+lock）。
- CLI: `engine` / `generate` / `conform` / `pack` / `aggregate` / `diff` / `history` /
  `list` / `visualize` / `sync-check` / `mutate` の各サブコマンドと既存フラグ。
- 生成物: `generate` の設計書構造と `visualize` の `out/contextdb.html`。ただし
  `out/` は**生成ビュー**であり正本ではない（`dr-06`：正本と生成ビューの分離）。

`_` 始まりの内部関数・各ツールの補助構造は**非公開**で予告なく変わる。

## 廃止（Deprecation）方針

後方非互換にしたい面は、**削除前に最低 1 つの MINOR リリースで非推奨**として残す。
CHANGELOG に `Deprecated` として代替手段とともに明記し、実際の削除は次の **MAJOR**
で行う。データ後方互換のため、リーダは未知の新しい属性を無視し、旧スキーマは移行して
読めるようにする。

## 変更の安全性（mutate の規律）

仕様データの変更は `mutate` を通し、次を機械的に強制する（手で守らない）:

- 新規・変更は必ず `status: review`。`approved` へ上げるのは人のレビュー後、
  `approve` 操作でのみ。
- 適用後に全体を再検証し、**新たな error を生む plan は自動で巻き戻す**（fail-closed）。

## 棚卸し（inventory）とレビュー周期

| 対象 | 周期 | 作業 |
|---|---|---|
| メタモデル（`metamodel.yaml`） | スキーマ変更時 | 種別・属性・関係の宣言と既存データの整合を確認 |
| 標準パック（`packs/` / `packs-src/`） | パック改版時 | `pack lock` / `conform` で版と準拠を確認 |
| 依存（`dependencies.md`） | 四半期ごと + CVE 通知時 | PyYAML / Jinja2 の版・ライセンス・脆弱性を確認 |
| 脅威モデル（`threat-model.md`） | 半期ごと | 脅威・防御・検証テストの対応を更新 |
