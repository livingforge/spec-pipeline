# Changelog — context-sync

バージョニング方針は [GOVERNANCE.md](GOVERNANCE.md)、依存は
[dependencies.md](dependencies.md) を参照。

## Unreleased

### Added
- **package-meta の整備**。ライセンス（MIT）・変更履歴・依存・脅威モデル（前提とする
  安全性の担保）を `package-meta/context-sync/`（LICENSE / CHANGELOG.md /
  dependencies.md / GOVERNANCE.md / threat-model.md）へ集約した。context-sync は
  実行体を持たない手順スキルのため、pack にはガバナンス/前提のみを記載する。

## 1.0.0 (2026-07-05)

初回リリース。実装差分を `.contextdb`（設計データの正本）へ同期する**手順スキル**。
`SKILL.md` 単体で、実処理は兄弟スキル contextdb のツールに委譲する。

- 実装の締めくくりに実行し、実装だけ進んで正本が陳腐化するのを防ぐ。
- 手順: `sync-check` で乖離候補を列挙 → 「仕様の変化か」を判定 → `mutate` の plan で
  正本を更新（自動採番・`status: review` 強制・トランザクショナル適用）→ `engine` で
  機械検証（error 0）→ `visualize` / `generate` でビュー再生成 → `history --uncommitted`
  で変更一覧を報告。
- 判断が要るのは「この実装変更は仕様の変化か」の判定と出典の文章化だけで、候補列挙・
  適用・検証は contextdb ツールが機械化する。
