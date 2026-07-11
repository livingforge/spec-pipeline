# Changelog — contextdb

バージョニング方針は [GOVERNANCE.md](GOVERNANCE.md)、依存とライセンスは
[dependencies.md](dependencies.md) を参照。

## Unreleased

### Added
- **package-meta の整備**。ライセンス（MIT）・変更履歴・依存・脅威モデルを
  `package-meta/contextdb/`（LICENSE / CHANGELOG.md / dependencies.md /
  GOVERNANCE.md / threat-model.md）へ集約し、スキルの実行時動作に直接関係しない
  ガバナンス/メタ文書を scripts/ から分離した。

## Renamed (2026-07-09)

- **`specdb` → `contextdb` へ全面リネーム**（AI Ready 化）。CLI 名・スキル名・
  正本ディレクトリ（`.specdb` → `.contextdb`）を刷新。データスキーマ・検証規則は不変。

## Highlights（〜2026-07）

- **仕様をデータとして管理**する正本ストア。メタモデル（`metamodel.yaml`）で種別・
  属性・関係を宣言し、`items/` + `relations/`（YAML）を単一の正本とする。
- **機械検証**（`engine`）— 必須属性・一意性・カーディナリティ・孤児を宣言に対して
  検証し、error があれば exit 1。壊れた YAML は中断せず error として報告。
- **設計書生成**（`generate`）— Markdown / Excel 風 HTML の設計書を Jinja2 テンプレートで
  生成。生成物 `out/` はビューであり直接編集しない（`dr-06`）。
- **対話型グラフビューア**（`visualize`）— 依存・外部依存なしの自己完結 HTML
  `out/contextdb.html`。「レビュー中」表示でレビュー対象と隣接関係を確認できる。
- **安全な変更**（`mutate`）— ID・連番の自動採番、`status: review` 強制、
  トランザクショナル適用（新たな error を生む plan は自動巻き戻し）。
- **ドリフト検出**（`sync-check`）— 実装と正本の乖離を列挙し、`--strict` で CI ゲート化。
- **変更履歴**（`history`）— Git から意味的な変更一覧を再構成。`diff` でベースライン比較。
- **標準パック** — `jp-sier-std` 等を `extends` で継承し、準拠検証（L1+L2+lock）と
  横断集計（`aggregate`）・パックリリースチェックを提供。
